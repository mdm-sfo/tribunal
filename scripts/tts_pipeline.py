#!/usr/bin/env python3
"""
TTS Pipeline — Converts Tribunal voice-script.json into spoken audio (MP3).

End-to-end pipeline:
  1. Reads voice-script.json (produced by screenplay_generator.py)
  2. Maps characters to ElevenLabs voices with delivery tags
  3. Calls ElevenLabs TTS API directly (per-line, with retries)
  4. Stitches per-line audio into final MP3 via ffmpeg

Requires:
  - ELEVENLABS_API_KEY environment variable
  - ffmpeg installed

Part of The Tribunal (github.com/mdm-sfo/conclave)

Usage:
    python3 tts_pipeline.py --input voice-script.json
    python3 tts_pipeline.py --input voice-script.json --output tribunal-audio.mp3
    python3 tts_pipeline.py --input voice-script.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# ElevenLabs Voice IDs (pre-made voices)
# ---------------------------------------------------------------------------

VOICE_IDS = {
    "adam":      "pNInz6obpgDQGcFmaJgB",
    "alice":     "Xb7hH8MSUJpSbSDYk0k2",
    "antoni":    "ErXwobaYiN019PkySvjV",
    "arnold":    "VR6AewLTigWG4xSOukaG",
    "bill":      "pqHfZKP75CvOlQylNhV4",
    "brian":     "nPczCjzI2devNBz1zQrb",
    "callum":    "N2lVS1w4EtoT3dr4eOWO",
    "charlie":   "IKne3meq5aSn9XLyUdCD",
    "charlotte": "XB0fDUnXU5powFXDhCwa",
    "chris":     "iP95p4xoKVk53GoZ742B",
    "daniel":    "onwK4e9ZLuTAKqWW03F9",
    "dave":      "CYw3kZ02Hs0563khs1Fj",
    "emily":     "LcfcDJNUP1GQjkzn1xUU",
    "george":    "JBFqnCBsd6RMkjVDRZzb",
    "james":     "ZQe5CZNOzWyzPSCn5a3c",
    "josh":      "TxGEqnHWrfWFTfGW9XjX",
    "liam":      "TX3LPaxmHKxFdv7VOQHJ",
    "lily":      "pFZP5JQG7iQjIQuC4Bku",
    "matilda":   "XrExE9yKIg1WjnnlVkGX",
    "rachel":    "21m00Tcm4TlvDq8ikWAM",
    "sam":       "yoZ06aMxZJJ28mfd3POQ",
    "sarah":     "EXAVITQu4vr4xnSDxMaL",
    "thomas":    "GBv7mTt0atIp3Br8iCZE",
}

# ElevenLabs API config
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVENLABS_MODEL = "eleven_multilingual_v2"  # Best quality for multi-voice

# Retry config
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2.0


# ---------------------------------------------------------------------------
# Voice Casting
# ---------------------------------------------------------------------------

DEFAULT_VOICE_MAP = {
    # Narrator
    "moderator": "daniel",          # Deep British male, authoritative narrator

    # Advocates — each gets a distinct voice for clarity
    "advocate-a": "brian",           # Deep rich American male, conversational
    "advocate-b": "sarah",           # Soft professional female
    "advocate-c": "charlie",         # Casual Australian male
    "advocate-d": "matilda",         # Warm friendly female
    "advocate-e": "george",          # Raspy British male
    "advocate-f": "josh",            # Deep young American male
    "advocate-g": "lily",            # Raspy British female
    "advocate-h": "liam",            # Young American male, energetic
    "advocate-i": "alice",           # Confident British female
    "advocate-j": "chris",           # Casual American male

    # Cardinals / Judges — gravitas
    "cardinal-a": "adam",            # Deep authoritative, formal
    "cardinal-b": "rachel",          # Calm soothing female
    "cardinal-c": "james",           # Calm old Australian male
    "cardinal-d": "bill",            # Strong American male, documentary
    "cardinal-e": "alice",           # Confident British female

    # Fresh Eyes
    "fresh-eyes": "rachel",          # Calm, measured — outsider perspective
}

FALLBACK_VOICE = "brian"


# ---------------------------------------------------------------------------
# Delivery Tag Engine
# ---------------------------------------------------------------------------

CHARACTER_DELIVERY = {
    "moderator": {
        "default": "[narrator]",
        "act_transitions": "[dramatic pause]",
        "reveals": "[slowly]",
    },
    "cardinal": {
        "default": "[seriously][measured pace]",
        "verdict": "[confidently]",
    },
    "advocate": {
        "default": "",
        "concede": "[reluctantly]",
        "defend": "[confidently]",
        "revise": "[thoughtfully]",
    },
}


def detect_speech_event(text):
    # type: (str) -> str
    t = text.lower()
    if "conceding" in t or "concede" in t:
        return "concede"
    if "defending" in t or "defend" in t:
        return "defend"
    if "revising" in t or "revis" in t:
        return "revise"
    if "verdict" in t or "judgment" in t or "synthesize" in t:
        return "verdict"
    if "identit" in t or "mask" in t or "reveal" in t:
        return "reveals"
    if "convenes" in t or "deliberation" in t or "begins" in t:
        return "act_transitions"
    return "default"


def get_delivery_tag(character, text):
    # type: (str, str) -> str
    if character == "moderator":
        role_tags = CHARACTER_DELIVERY.get("moderator", {})
    elif character.startswith("cardinal"):
        role_tags = CHARACTER_DELIVERY.get("cardinal", {})
    elif character.startswith("advocate"):
        role_tags = CHARACTER_DELIVERY.get("advocate", {})
    else:
        return ""

    event = detect_speech_event(text)
    tag = role_tags.get(event, role_tags.get("default", ""))
    return tag.strip()


# ---------------------------------------------------------------------------
# ElevenLabs API
# ---------------------------------------------------------------------------

def tts_single_line(text, voice_name, api_key, output_path, line_num, total):
    # type: (str, str, str, str, int, int) -> bool
    """
    Call ElevenLabs TTS API for a single line of dialogue.
    Writes MP3 to output_path. Returns True on success.
    """
    voice_id = VOICE_IDS.get(voice_name.lower())
    if not voice_id:
        print(
            f"  [!] Unknown voice '{voice_name}', using fallback '{FALLBACK_VOICE}'",
            file=sys.stderr,
        )
        voice_id = VOICE_IDS.get(FALLBACK_VOICE, list(VOICE_IDS.values())[0])

    url = f"{ELEVENLABS_API_URL}/{voice_id}"
    payload = json.dumps({
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.50,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }).encode("utf-8")

    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            resp = urllib.request.urlopen(req, timeout=60)
            audio_data = resp.read()

            with open(output_path, "wb") as f:
                f.write(audio_data)

            return True

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass

            if e.code == 429:
                # Rate limited — wait and retry
                wait = RETRY_DELAY_SECONDS * attempt * 2
                print(
                    f"  [!] Line {line_num}/{total}: Rate limited (429). "
                    f"Waiting {wait:.0f}s... (attempt {attempt}/{MAX_RETRIES})",
                    file=sys.stderr,
                )
                time.sleep(wait)
            elif e.code >= 500:
                wait = RETRY_DELAY_SECONDS * attempt
                print(
                    f"  [!] Line {line_num}/{total}: Server error ({e.code}). "
                    f"Retrying in {wait:.0f}s... (attempt {attempt}/{MAX_RETRIES})",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                print(
                    f"  [!] Line {line_num}/{total}: HTTP {e.code} — {body}",
                    file=sys.stderr,
                )
                return False

        except Exception as e:
            wait = RETRY_DELAY_SECONDS * attempt
            print(
                f"  [!] Line {line_num}/{total}: {type(e).__name__}: {e}. "
                f"Retrying in {wait:.0f}s... (attempt {attempt}/{MAX_RETRIES})",
                file=sys.stderr,
            )
            time.sleep(wait)

    print(f"  [!] Line {line_num}/{total}: FAILED after {MAX_RETRIES} attempts", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def load_voice_script(path):
    # type: (Path) -> dict
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if "lines" not in data or not data["lines"]:
        raise ValueError("voice-script.json missing 'lines' or empty")

    return data


def resolve_voice_map(voice_script, custom_map=None):
    # type: (dict, Optional[Dict[str, str]]) -> Dict[str, str]
    voice_map = dict(DEFAULT_VOICE_MAP)
    if custom_map:
        voice_map.update(custom_map)

    unmapped = []
    for line in voice_script["lines"]:
        char = line["character"]
        if char not in voice_map:
            unmapped.append(char)
            voice_map[char] = FALLBACK_VOICE

    if unmapped:
        print(
            f"[tts] Warning: unmapped characters → {FALLBACK_VOICE}: "
            f"{', '.join(set(unmapped))}",
            file=sys.stderr,
        )

    return voice_map


def print_cast_sheet(voice_script, voice_map):
    # type: (dict, Dict[str, str]) -> None
    print("\n[tts] Voice Cast:", file=sys.stderr)
    print("-" * 55, file=sys.stderr)

    characters = voice_script.get("characters", [])
    if characters:
        for char in characters:
            cid = char["id"]
            display = char.get("display_name", cid)
            real = char.get("real_identity", "")
            voice = voice_map.get(cid, FALLBACK_VOICE)
            identity_str = f" ({real})" if real else ""
            print(f"  {display}{identity_str} → {voice}", file=sys.stderr)
    else:
        for char_id in sorted(set(l["character"] for l in voice_script["lines"])):
            print(f"  {char_id} → {voice_map.get(char_id, FALLBACK_VOICE)}", file=sys.stderr)

    print("-" * 55, file=sys.stderr)

    counts = {}  # type: Dict[str, int]
    for line in voice_script["lines"]:
        counts[line["character"]] = counts.get(line["character"], 0) + 1

    total = sum(counts.values())
    print(f"  Total lines: {total}", file=sys.stderr)
    for char_id, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"    {char_id}: {count} lines", file=sys.stderr)

    total_words = sum(len(line["text"].split()) for line in voice_script["lines"])
    est_minutes = total_words / 150
    print(f"  Estimated duration: ~{est_minutes:.1f} min ({total_words} words @ 150 wpm)", file=sys.stderr)
    print(file=sys.stderr)


def stitch_audio(part_paths, output_path):
    # type: (List[str], str) -> bool
    """Concatenate MP3 files using ffmpeg."""
    if not part_paths:
        return False

    # Write concat list
    concat_file = output_path + ".concat.txt"
    try:
        with open(concat_file, "w") as f:
            for p in part_paths:
                # ffmpeg concat requires escaped single quotes in paths
                escaped = p.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file,
                "-c", "copy",
                output_path,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            print(f"[tts] ffmpeg error: {result.stderr[-500:]}", file=sys.stderr)
            return False

        return True

    finally:
        try:
            os.unlink(concat_file)
        except OSError:
            pass


def get_audio_duration(path):
    # type: (str) -> Optional[float]
    """Get duration of an audio file using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def run_pipeline(
    input_path,        # type: Path
    output_path,       # type: Optional[Path]
    voice_map_path,    # type: Optional[Path]
    add_tags,          # type: bool
    dry_run,           # type: bool
):
    # type: (...) -> int
    """Run the full TTS pipeline. Returns exit code."""

    # Check API key
    api_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not api_key and not dry_run:
        print("[tts] Error: ELEVENLABS_API_KEY environment variable not set", file=sys.stderr)
        return 1

    # Check ffmpeg
    if not dry_run:
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        except FileNotFoundError:
            print("[tts] Error: ffmpeg not found. Install it: apt install ffmpeg", file=sys.stderr)
            return 1

    # Load voice script
    voice_script = load_voice_script(input_path)
    session_id = voice_script.get("session_id", "unknown")
    lines = voice_script["lines"]

    print(
        f"[tts] Loaded: {session_id} ({len(lines)} lines, "
        f"{voice_script.get('act_count', '?')} acts)",
        file=sys.stderr,
    )

    # Resolve voices
    custom_map = None
    if voice_map_path:
        with open(voice_map_path, encoding="utf-8") as f:
            custom_map = json.load(f)

    voice_map = resolve_voice_map(voice_script, custom_map)
    print_cast_sheet(voice_script, voice_map)

    # Prepare dialogue with tags
    dialogue = []
    for line in lines:
        char = line["character"]
        text = line["text"]
        voice = voice_map.get(char, FALLBACK_VOICE)

        if add_tags:
            tag = get_delivery_tag(char, text)
            if tag:
                text = f"{tag} {text}"

        dialogue.append({
            "character": char,
            "voice": voice,
            "text": text,
        })

    if dry_run:
        print("\n[tts] Dry run — dialogue preview:\n", file=sys.stderr)
        for i, d in enumerate(dialogue):
            preview = d["text"][:100] + ("..." if len(d["text"]) > 100 else "")
            print(f"  [{i+1:2d}] {d['voice']:8s} ({d['character']}) | {preview}", file=sys.stderr)
        print(f"\n[tts] Would generate {len(dialogue)} audio segments.", file=sys.stderr)
        return 0

    # Determine output path
    if output_path is None:
        output_path = input_path.parent / f"{session_id}-audio.mp3"

    # Create temp dir for per-line audio files
    with tempfile.TemporaryDirectory(prefix="tribunal-tts-") as tmpdir:
        part_paths = []
        failed = 0
        start_time = time.time()

        print(f"[tts] Generating {len(dialogue)} audio segments...", file=sys.stderr)

        for i, d in enumerate(dialogue):
            part_path = os.path.join(tmpdir, f"line-{i:03d}.mp3")
            line_num = i + 1

            ok = tts_single_line(
                text=d["text"],
                voice_name=d["voice"],
                api_key=api_key,
                output_path=part_path,
                line_num=line_num,
                total=len(dialogue),
            )

            if ok:
                part_paths.append(part_path)
                # Brief pause between API calls to avoid rate limits
                if line_num < len(dialogue):
                    time.sleep(0.3)
            else:
                failed += 1
                print(
                    f"  [!] Skipping line {line_num} ({d['character']})",
                    file=sys.stderr,
                )

            # Progress every 5 lines
            if line_num % 5 == 0 or line_num == len(dialogue):
                elapsed = time.time() - start_time
                print(
                    f"  [{line_num}/{len(dialogue)}] "
                    f"{elapsed:.0f}s elapsed, {failed} failed",
                    file=sys.stderr,
                )

        if not part_paths:
            print("[tts] Error: No audio segments generated", file=sys.stderr)
            return 1

        # Stitch
        print(f"\n[tts] Stitching {len(part_paths)} segments...", file=sys.stderr)
        if not stitch_audio(part_paths, str(output_path)):
            print("[tts] Error: ffmpeg stitch failed", file=sys.stderr)
            return 1

    # Report
    elapsed = time.time() - start_time
    duration = get_audio_duration(str(output_path))
    size_mb = os.path.getsize(str(output_path)) / (1024 * 1024)

    print(f"\n[tts] Done.", file=sys.stderr)
    print(f"  Output: {output_path}", file=sys.stderr)
    if duration:
        minutes = int(duration // 60)
        seconds = int(duration % 60)
        print(f"  Duration: {minutes}:{seconds:02d}", file=sys.stderr)
    print(f"  Size: {size_mb:.1f} MB", file=sys.stderr)
    print(f"  Lines: {len(part_paths)} ok, {failed} failed", file=sys.stderr)
    print(f"  Pipeline time: {elapsed:.0f}s", file=sys.stderr)

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    # type: () -> None
    parser = argparse.ArgumentParser(
        description="Convert Tribunal voice-script.json to spoken audio (MP3)"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to voice-script.json",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Output MP3 path (default: <session-id>-audio.mp3 next to input)",
    )
    parser.add_argument(
        "--voice-map",
        default=None,
        help="Optional JSON file with custom character->voice mapping",
    )
    parser.add_argument(
        "--no-tags",
        action="store_true",
        help="Disable delivery tags (emotion/pacing annotations)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview dialogue without calling ElevenLabs API",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[tts] Error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else None
    voice_map_path = Path(args.voice_map) if args.voice_map else None

    exit_code = run_pipeline(
        input_path=input_path,
        output_path=output_path,
        voice_map_path=voice_map_path,
        add_tags=not args.no_tags,
        dry_run=args.dry_run,
    )

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
