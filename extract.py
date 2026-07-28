"""Fast, deterministic extraction for the FLASH push (no LLM, no network).

The whole point of stage 1 is speed: a regex ticker is worth more delivered in
seconds than a perfect one delivered in two minutes, because the scarce resource
is the borrow locate, not the quote.

Stage 2 (phase 3) will add an LLM pass that can correct these and add thesis /
evidence-quality fields. The interface is stubbed here so watch.py doesn't change.
"""
from __future__ import annotations
import re

import config as C

# Tokens that look like tickers but aren't.
STOP = {
    "CEO", "CFO", "COO", "CTO", "SEC", "DOJ", "FDA", "FBI", "IRS", "IPO", "SPAC", "ESG",
    "AI", "EV", "US", "USA", "UK", "EU", "GAAP", "IFRS", "LLC", "INC", "LP", "ETF", "NFT",
    "PDF", "USD", "EUR", "GBP", "Q1", "Q2", "Q3", "Q4", "FY", "YOY", "EBITDA", "ARR", "TAM",
    "II", "III", "IV", "TV", "IP", "RD", "PR", "HR", "IT", "OTC", "NYSE", "NASDAQ", "LSE",
    "ASX", "TSX", "HKEX", "SGX", "BME", "JSE", "NSE", "BSE", "AIM", "SPY", "DOE", "DOD",
    "COVID", "SPV", "REIT", "M&A", "CAGR", "NAV", "AUM", "KPI", "SaaS", "IPOs", "NEW",
    "THE", "AND", "FOR", "WITH", "HOW", "WHY", "ITS", "OUR", "WE", "A", "AN", "IS", "ARE",
}

# Ordered by confidence: an exchange-qualified ticker beats a bare parenthetical.
PATTERNS = [
    # (NASDAQ: ABCD) / (NYSE American: AB) / (Borsa Italiana: BC) / (Warsaw: CCC)
    (re.compile(r"\((?:NASDAQ|NYSE(?:\s+American|\s+Arca)?|OTC(?:MKTS)?|LSE|ASX|TSX(?:-V)?|"
                r"HKEX|SGX|BME|JSE|NSE|BSE|Xetra|STO|Borsa\s+Italiana|Warsaw|Milan|Frankfurt|Vienna|SIX)"
                r"\s*[:\s]\s*([A-Z0-9]{1,6})\)", re.I), "exchange"),
    # NASDAQ: ABCD  (no parens)
    (re.compile(r"\b(?:NASDAQ|NYSE|ASX|TSX|HKEX|LSE|STO|Xetra)\s*:\s*([A-Z0-9]{1,6})\b"), "exchange"),
    # $ABCD
    (re.compile(r"\$([A-Z]{1,5})\b"), "cashtag"),
    # Company Name (ABCD): ...
    (re.compile(r"\(([A-Z]{2,5})\)"), "paren"),
    # "Short TE – ..." / "ABCD: thesis"
    (re.compile(r"^(?:Short\s+|We\s+are\s+Short\s+)([A-Z]{2,5})\b", re.I), "lead"),
    (re.compile(r"^([A-Z]{2,5})\s*[:–—-]\s"), "lead"),
]


def extract_ticker(*texts) -> tuple[str | None, str | None]:
    """Return (ticker, how). Tries texts in order; first confident hit wins."""
    for txt in texts:
        if not txt:
            continue
        t = str(txt)
        for rx, how in PATTERNS:
            for m in rx.finditer(t):
                cand = m.group(1).upper()
                if cand in STOP or len(cand) < 2:
                    continue
                if cand.isdigit():
                    continue
                return cand, how
    return None, None


def is_new_thesis(title: str, body: str = "") -> bool | None:
    """Heuristic gate for the FLASH push. None = unsure (still alert, flag it).
    Stage 2's LLM makes the authoritative call and can retract."""
    t = (title or "").lower()
    if not t:
        return None
    for bad in C.NOT_A_NEW_THESIS:
        if bad in t:
            return False
    # strong positive signals
    if re.search(r"\b(short|fraud|scam|accounting|misleading|undisclosed|inflated|"
                 r"overstat|fabricat|red flag|we believe|downside|sham|whistleblower)\b", t):
        return True
    return None


def enrich_stub(item_title: str, item_body: str) -> dict:
    """Placeholder for the phase-3 LLM pass. Keeps watch.py stable."""
    return {"llm": None, "borrow": None, "note": "enrichment pending (phase 3/4)"}
