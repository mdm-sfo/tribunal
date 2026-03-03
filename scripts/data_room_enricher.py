"""
Data Room enricher — unified briefing enrichment before advocate dispatch.

Detects stock tickers in the briefing, routes to the appropriate data provider
by exchange/region, fetches live fundamentals, and prepends a structured
Data Room block so every advocate starts from accurate numbers.

Provider routing:
  - U.S. tickers (NYSE/NASDAQ/AMEX, or bare uppercase ticker) → Massive.com
  - European tickers (.MC, .DE, .L, .PA, etc.)               → Bavest
  - Japanese tickers (TSE/TYO, .T, or all-digit code)        → J-Quants (TODO)

Silent fallback: no API key, no ticker, or API failure → original briefing unchanged.
"""

import os
import re
import requests
from datetime import date, timedelta
from typing import Optional


# ── Provider base URLs ──────────────────────────────────────────────────────

BAVEST_BASE  = "https://api.bavest.co/v0"
MASSIVE_BASE = "https://api.massive.com"


# ── Exchange routing tables ─────────────────────────────────────────────────

# Exchanges that map to a Bavest/Yahoo suffix for European markets
EUROPEAN_EXCHANGE_SUFFIX: dict[str, str] = {
    "BME": ".MC",  "MC": ".MC",
    "XETRA": ".DE", "ETR": ".DE",
    "LSE": ".L",
    "EPA": ".PA",  "PA": ".PA",
    "AMS": ".AS",  "AS": ".AS",
    "EBR": ".BR",
    "HEL": ".HE",
    "STO": ".ST",
    "OSE": ".OL",
    "JSE": ".JO",
}

US_EXCHANGES     = {"NYSE", "NASDAQ", "AMEX", "BATS", "CBOE", "ARCX", "XNAS", "XNYS"}
JAPAN_EXCHANGES  = {"TSE", "TYO", "JPX", "OSE", "TKS"}

ALL_EXCHANGE_SUFFIX = {
    **EUROPEAN_EXCHANGE_SUFFIX,
    "TSE": ".T", "TYO": ".T",
    **{ex: "" for ex in US_EXCHANGES},
}


# ── Ticker detection ────────────────────────────────────────────────────────

def _extract_ticker(briefing: str) -> Optional[tuple[str, str, str]]:
    """
    Return (raw_ticker, resolved_symbol, region) or None.
    region is "us" | "eu" | "jp" | "unknown"

    Detection priority:
    1. (EXCHANGE: TICKER) — most reliable
    2. Already-dotted symbol, e.g. ACX.MC
    3. Labelled bare ticker, e.g. "ticker AAPL"
    """
    # 1. (EXCHANGE: TICKER) or [EXCHANGE: TICKER]
    m = re.search(r'[\[(]([A-Z]{2,6}):\s*([A-Z0-9]{1,10})[\])]', briefing)
    if m:
        exchange, ticker = m.group(1).upper(), m.group(2).upper()
        if exchange in US_EXCHANGES:
            return ticker, ticker, "us"
        if exchange in JAPAN_EXCHANGES:
            return ticker, f"{ticker}.T", "jp"
        suffix = EUROPEAN_EXCHANGE_SUFFIX.get(exchange, "")
        return ticker, f"{ticker}{suffix}", "eu"

    # 2. Already dotted: ACX.MC, BRBY.L, DAI.DE, 6981.T etc.
    m = re.search(r'\b([A-Z0-9]{1,6}\.[A-Z]{1,3})\b', briefing)
    if m:
        sym = m.group(1)
        suffix = sym.split(".")[-1].upper()
        if suffix in ("T",):
            return sym, sym, "jp"
        if suffix in ("DE", "MC", "L", "PA", "AS", "BR", "HE", "ST", "OL", "JO"):
            return sym, sym, "eu"
        return sym, sym, "us"

    # 3. All-digit code — likely Japanese TSE (e.g. 6981 for Murata)
    m = re.search(r'(?:TSE|TYO|JPX|ticker|stock|symbol)[\s:]+([0-9]{4})\b', briefing, re.IGNORECASE)
    if m:
        ticker = m.group(1)
        return ticker, f"{ticker}.T", "jp"

    # 4. Labelled bare uppercase ticker
    m = re.search(r'(?:ticker|stock|symbol)\s+([A-Z]{2,6})\b', briefing, re.IGNORECASE)
    if m:
        ticker = m.group(1)
        return ticker, ticker, "us"  # default bare tickers to U.S.

    return None


# ── Formatting helpers ──────────────────────────────────────────────────────

def _pct(v) -> str:
    return f"{v*100:.1f}%" if v is not None else "n/a"

def _num(v, d: int = 2) -> str:
    return f"{v:,.{d}f}" if v is not None else "n/a"

def _bn(v, ccy: str = "") -> str:
    if v is None:
        return "n/a"
    if abs(v) >= 1e9:
        return f"{ccy}{v/1e9:.2f}B"
    if abs(v) >= 1e6:
        return f"{ccy}{v/1e6:.1f}M"
    return f"{ccy}{v:,.0f}"


# ── Bavest (European stocks) ────────────────────────────────────────────────

def _bavest_post(endpoint: str, symbol: str, api_key: str) -> Optional[dict]:
    try:
        r = requests.post(
            f"{BAVEST_BASE}/{endpoint}",
            json={"symbol": symbol},
            headers={"x-api-key": api_key},
            timeout=8,
        )
        d = r.json()
        return None if (isinstance(d, dict) and d.get("status") == "ERROR") else d
    except Exception:
        return None


def _build_bavest_room(symbol: str, api_key: str) -> Optional[str]:
    quote        = _bavest_post("quote", symbol, api_key)
    fundamentals = _bavest_post("stock/fundamentals", symbol, api_key)
    ttm          = _bavest_post("stock/financials/ttm", symbol, api_key)

    if not quote:
        return None

    ccy   = quote.get("currency", "")
    lines = [f"## Data Room — {symbol} ({ccy})\n"]
    lines.append(f"**Price:** {ccy}{_num(quote.get('c'))}  ({_num(quote.get('dp'))}% vs prev close)")

    metrics = quote.get("metrics", {})
    mktcap  = metrics.get("marketCapitalization")
    if mktcap:
        lines.append(f"**Market cap:** {_bn(mktcap, ccy)}")
    pe  = metrics.get("pe/ratio")
    eps = metrics.get("eps")
    if pe:
        lines.append(f"**Trailing P/E:** {_num(pe)}  |  **EPS:** {_num(eps)}")

    fund_list = (fundamentals or {}).get("fundamentals")
    if fund_list:
        f0     = fund_list[0]
        period = f0.get("period", "latest")
        rev    = f0.get("revenue", {})
        mult   = f0.get("multiples", {})
        stab   = f0.get("stability", {})
        lines.append(f"\n**Annual fundamentals ({period}):**")
        lines.append(f"- EBITDA margin: {_pct(rev.get('ebitdaMargin'))}  |  EBIT margin: {_pct(rev.get('ebitMargin'))}")
        lines.append(f"- ROE: {_pct(rev.get('equityReturn'))}  |  ROA: {_pct(rev.get('assetsReturn'))}")
        lines.append(f"- P/E: {_num(mult.get('priceEarningsRatio'))}  |  P/B: {_num(mult.get('priceBookRatio'))}  |  P/S: {_num(mult.get('priceSalesRatio'))}")
        lines.append(f"- Current ratio: {_num(stab.get('currentRatio'))}  |  D/E: {_num(stab.get('debtToEquityRatio'))}")

    bs = (ttm or {}).get("bs")
    if bs:
        lines.append(f"\n**Balance sheet (TTM):**")
        lines.append(f"- Total assets: {_bn(bs.get('totalAssets'), ccy)}")
        lines.append(f"- Net debt: {_bn(bs.get('netDebt'), ccy)}")
        lines.append(f"- Total equity: {_bn(bs.get('totalStockholdersEquity'), ccy)}")

    lines.append("\n*Source: Bavest — data as of last close.*")
    lines.append("\n---\n")
    return "\n".join(lines)


# ── Massive.com (U.S. stocks) ───────────────────────────────────────────────

def _massive_get(path: str, api_key: str, params: Optional[dict] = None) -> Optional[dict]:
    try:
        p = {"apiKey": api_key, **(params or {})}
        r = requests.get(f"{MASSIVE_BASE}{path}", params=p, timeout=8)
        d = r.json()
        if d.get("status") in ("NOT_AUTHORIZED", "ERROR"):
            return None
        return d
    except Exception:
        return None


def _build_massive_room(ticker: str, api_key: str) -> Optional[str]:
    ref   = _massive_get(f"/v3/reference/tickers/{ticker}", api_key)
    prev  = _massive_get(f"/v2/aggs/ticker/{ticker}/prev", api_key, {"adjusted": "true"})
    fins  = _massive_get("/vX/reference/financials", api_key, {"ticker": ticker, "limit": 1, "timeframe": "ttm"})

    if not ref or not ref.get("results"):
        return None

    info    = ref["results"]
    name    = info.get("name", ticker)
    mktcap  = info.get("market_cap")
    desc    = info.get("description", "")[:200]

    lines = [f"## Data Room — {ticker} (USD)\n"]

    # Price from previous close (snapshot requires higher plan)
    prev_results = (prev or {}).get("results", [])
    if prev_results:
        bar   = prev_results[0]
        close = bar.get("c")
        chg_pct = ((bar["c"] - bar["o"]) / bar["o"] * 100) if bar.get("o") else None
        lines.append(f"**Price (prev close):** ${_num(close)}  ({_num(chg_pct)}% open→close)")

    if mktcap:
        lines.append(f"**Market cap:** {_bn(mktcap, '$')}")

    # Financials from TTM
    fin_results = (fins or {}).get("results", [])
    if fin_results:
        fin    = fin_results[0]
        period = f"{fin.get('start_date', '')} → {fin.get('end_date', '')}"
        is_    = fin.get("financials", {}).get("income_statement", {})
        bs_    = fin.get("financials", {}).get("balance_sheet", {})

        def _v(section, key):
            entry = section.get(key, {})
            return entry.get("value") if isinstance(entry, dict) else None

        revenue   = _v(is_, "revenues")
        gross     = _v(is_, "gross_profit")
        op_inc    = _v(is_, "operating_income_loss")
        net_inc   = _v(is_, "net_income_loss")
        eps_basic = _v(is_, "basic_earnings_per_share")
        equity    = _v(bs_, "equity_attributable_to_parent")
        lt_debt   = _v(bs_, "long_term_debt")

        lines.append(f"\n**Financials TTM ({period}):**")
        if revenue:
            lines.append(f"- Revenue: {_bn(revenue, '$')}")
            if gross:
                lines.append(f"- Gross margin: {_pct(gross/revenue)}  |  Gross profit: {_bn(gross, '$')}")
            if op_inc:
                lines.append(f"- Operating margin: {_pct(op_inc/revenue)}  |  Op. income: {_bn(op_inc, '$')}")
            if net_inc:
                lines.append(f"- Net margin: {_pct(net_inc/revenue)}  |  Net income: {_bn(net_inc, '$')}")
        if eps_basic is not None:
            lines.append(f"- EPS (basic): ${_num(eps_basic)}")
            if prev_results and prev_results[0].get("c") and eps_basic:
                pe = prev_results[0]["c"] / eps_basic
                lines.append(f"- Implied P/E: {_num(pe)}x")
        if equity:
            lines.append(f"- Book equity: {_bn(equity, '$')}")
        if lt_debt:
            lines.append(f"- Long-term debt: {_bn(lt_debt, '$')}")

    if desc:
        lines.append(f"\n**Company:** {name} — {desc}...")

    lines.append("\n*Source: Massive.com — prev-close price + TTM financials.*")
    lines.append("\n---\n")
    return "\n".join(lines)


# ── J-Quants (Japanese stocks) — stub ──────────────────────────────────────

def _build_jquants_room(ticker: str, api_key: str) -> Optional[str]:
    # J-Quants requires a two-step auth (refresh token → ID token).
    # TODO: implement when needed for Japanese stock sessions.
    return None


# ── CourtListener (U.S. case law) ──────────────────────────────────────────

# Common sentence-starter words that precede a company name but aren't part of it
_NOT_NAME = {
    "analyze", "buy", "sell", "invest", "research", "evaluate", "assess",
    "is", "are", "was", "should", "would", "could", "will", "does",
    "what", "why", "how", "who", "where", "when", "tell", "discuss",
    "compare", "review", "check", "look", "about", "on", "for", "in",
}


def _extract_company_name(briefing: str, ticker: str) -> str:
    """
    Extract company name from briefing. Falls back to ticker.
    Looks for capitalised words immediately before (EXCHANGE: TICKER).
    """
    m = re.search(
        r'([A-Z][A-Za-z&\.\'-]+(?:\s+[A-Z][A-Za-z&\.\'-]+)*)\s*[\[(][A-Z]{2,6}:',
        briefing,
    )
    if m:
        words = m.group(1).strip().split()
        # Strip leading non-name words (verbs, question words, prepositions)
        while words and words[0].lower() in _NOT_NAME:
            words = words[1:]
        if words:
            return " ".join(words)
    return ticker


def _build_legal_exposure(company_name: str, ticker: str) -> Optional[str]:
    """
    Query CourtListener for significant recent U.S. court cases.
    Filters to cases where the company appears as a party (caseName match).
    Returns None if no relevant cases found or on any error.
    """
    cutoff = (date.today() - timedelta(days=3 * 365)).strftime("%Y-%m-%d")

    # Try company name first, fall back to ticker if different
    search_terms = list(dict.fromkeys([company_name, ticker]))

    for term in search_terms:
        try:
            r = requests.get(
                "https://www.courtlistener.com/api/rest/v4/search/",
                params={
                    "q": term,
                    "type": "o",
                    "order_by": "dateFiled desc",
                    "page_size": 20,
                    "filed_after": cutoff,
                },
                headers={"Accept": "application/json"},
                timeout=8,
            )
            results = r.json().get("results", [])
        except Exception:
            continue

        # Only cases where the company is a named party, not just mentioned
        term_lower = term.lower()
        relevant = [
            c for c in results
            if term_lower in c.get("caseName", "").lower()
        ][:5]

        if relevant:
            lines = ["\n**Legal Exposure (U.S. courts, last 3 years):**"]
            for c in relevant:
                court = c.get("court_citation_string") or c.get("court", "")
                lines.append(f"- *{c['caseName']}*  |  {court}  |  {c['dateFiled']}")
            lines.append("*Source: CourtListener — U.S. federal + state courts only.*")
            return "\n".join(lines)

    return None


# ── Public entry point ──────────────────────────────────────────────────────

def enrich_briefing(briefing: str) -> str:
    """
    Detect a stock ticker, route to the right data provider, and prepend
    a Data Room block. Returns the original briefing unchanged on any failure.
    """
    result = _extract_ticker(briefing)
    if not result:
        return briefing

    raw_ticker, symbol, region = result
    data_room: Optional[str] = None

    if region == "eu":
        api_key = os.environ.get("BAVEST_API_KEY", "")
        if api_key:
            data_room = _build_bavest_room(symbol, api_key)

    elif region == "us":
        api_key = os.environ.get("MASSIVE_API_KEY", "")
        if api_key:
            data_room = _build_massive_room(raw_ticker, api_key)

    elif region == "jp":
        api_key = os.environ.get("JQUANTS_API_KEY", "")
        if api_key:
            data_room = _build_jquants_room(raw_ticker, api_key)

    if not data_room:
        return briefing

    # Append legal exposure (no API key needed — CourtListener is free)
    company_name = _extract_company_name(briefing, raw_ticker)
    legal = _build_legal_exposure(company_name, raw_ticker)
    if legal:
        # Insert before trailing separator
        if data_room.endswith("---\n"):
            data_room = data_room[:-4].rstrip() + f"\n{legal}\n\n---\n"
        else:
            data_room += f"\n{legal}\n"

    return data_room + briefing
