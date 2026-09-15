"""
KHR/USD rate fetcher.
Priority: Wing Bank (wingbank.com.kh) -> ACLEDA Bank -> National Bank of Cambodia (final fallback).
"""
import json
import re
import time
import logging
import requests

log = logging.getLogger("scraper")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

WING_PAGE = "https://www.wingbank.com.kh/en/exchange-rate"
# Candidate Wing Bank data endpoints (the page is a JS SPA; these are tried in order)
WING_API_CANDIDATES = [
    "https://www.wingbank.com.kh/api/exchange-rate",
    "https://www.wingbank.com.kh/api/exchange-rates",
    "https://www.wingbank.com.kh/api/v1/exchange-rate",
    "https://www.wingbank.com.kh/api/v1/exchange-rates",
    "https://www.wingbank.com.kh/en/api/exchange-rate",
    "https://api.wingbank.com.kh/exchange-rate",
    "https://www.wingbank.com.kh/api/portal/exchange-rate",
]
ACLEDA_PAGE = "https://www.acledabank.com.kh/kh/eng/ps_cmforeignexchange?t=p"
NBC_PAGE = "https://www.nbc.gov.kh/english/economic_research/exchange_rate.php"

TIMEOUT = 10


def _find_usd_in_json(obj):
    """Recursively search a parsed JSON structure for a USD/KHR buy/sell pair."""
    results = []
    if isinstance(obj, dict):
        keys = {str(k).lower() for k in obj.keys()}
        code = str(obj.get("currency") or obj.get("code") or obj.get("symbol") or "").upper()
        if code == "USD" or ("currency" in keys and "usd" in json.dumps(obj).upper()[:400]):
            def num(*names):
                for n in names:
                    for k, v in obj.items():
                        if str(k).lower() == n:
                            try:
                                return float(str(v).replace(",", ""))
                            except (TypeError, ValueError):
                                pass
                return None
            buy = num("buy", "bid", "buy_rate", "buying")
            sell = num("sell", "ask", "sell_rate", "selling", "offer")
            if buy and sell:
                results.append((buy, sell))
        for v in obj.values():
            results.extend(_find_usd_in_json(v))
    elif isinstance(obj, list):
        for v in obj:
            results.extend(_find_usd_in_json(v))
    return results


def fetch_wing():
    """Fetch USD/KHR from Wing Bank. Raises on failure (so caller falls back)."""
    # 1) try candidate JSON APIs
    for url in WING_API_CANDIDATES:
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 200 and ("json" in r.headers.get("Content-Type", "") or r.text.strip().startswith(("{", "["))):
                hits = _find_usd_in_json(r.json())
                if hits:
                    buy, sell = hits[0]
                    return {"buy": buy, "sell": sell, "source": "WING BANK", "url": url}
        except Exception as e:
            log.debug("Wing API %s failed: %s", url, e)
    # 2) try embedded __NEXT_DATA__ / JSON in the HTML page itself
    r = requests.get(WING_PAGE, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if m:
        hits = _find_usd_in_json(json.loads(m.group(1)))
        if hits:
            buy, sell = hits[0]
            return {"buy": buy, "sell": sell, "source": "WING BANK", "url": WING_PAGE + " (__NEXT_DATA__)"}
    # 3) last resort: raw numbers near "USD" in rendered HTML
    m2 = re.search(r"USD.{0,2000}?(\d{3,4}[\d,.]*)\D+(\d{3,4}[\d,.]*)", r.text, re.S | re.I)
    if m2:
        buy = float(m2.group(1).replace(",", ""))
        sell = float(m2.group(2).replace(",", ""))
        if 3000 < buy < 5000 and 3000 < sell < 5000:
            return {"buy": buy, "sell": sell, "source": "WING BANK", "url": WING_PAGE}
    raise RuntimeError("Wing Bank: USD/KHR rate not found (site layout changed or site down)")


def fetch_acleda():
    """Fetch USD/KHR from ACLEDA static HTML table."""
    from bs4 import BeautifulSoup
    r = requests.get(ACLEDA_PAGE, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for row in soup.find_all("tr"):
        cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) >= 3 and "USD" in cells[0].upper() and "KHR" in cells[0].upper():
            buy = float(re.sub(r"[^\d.]", "", cells[1]))
            sell = float(re.sub(r"[^\d.]", "", cells[2]))
            return {"buy": buy, "sell": sell, "source": "ACLEDA BANK", "url": ACLEDA_PAGE}
    raise RuntimeError("ACLEDA: USD/KHR row not found")


def fetch_nbc():
    """Fetch official USD mid rate from National Bank of Cambodia (both static pages)."""
    from bs4 import BeautifulSoup
    r = requests.get(NBC_PAGE, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"USD\s*/\s*KHR.*?(\d{3,4})\D+(\d{3,4})", text, re.I)
    if m:
        buy, sell = float(m.group(1)), float(m.group(2))
        return {"buy": buy, "sell": sell, "source": "NBC (official)", "url": NBC_PAGE}
    # fallback: official exchange rate line
    m2 = re.search(r"Official Exchange Rate\s*:\s*(\d{3,4})\s*KHR\s*/\s*USD", text, re.I)
    if m2:
        v = float(m2.group(1))
        return {"buy": v, "sell": v, "source": "NBC (official mid)", "url": NBC_PAGE}
    raise RuntimeError("NBC: rate not found")


def get_rate():
    """Return {'buy','sell','source','ts'} trying sources in priority order."""
    for name, fn in (("WING", fetch_wing), ("ACLEDA", fetch_acleda), ("NBC", fetch_nbc)):
        try:
            d = fn()
            d["ts"] = time.time()
            return d
        except Exception as e:
            log.warning("%s fetch failed: %s", name, e)
    raise RuntimeError("All sources failed (check internet / both bank sites down)")
