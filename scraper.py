"""
KHR/USD rate fetcher.

Priority order (fixed and non-negotiable):
    1. Wing Bank     — https://www.wingbank.com.kh/en/exchange-rate
    2. ACLEDA Bank   — https://www.acledabank.com.kh/kh/eng/ps_cmforeignexchange?t=p
    3. National Bank of Cambodia — final fallback

Wing is always tried first. Only if Wing genuinely cannot return a USD/KHR rate
(site down, blocked, markup changed, credential error, ...) do we fall through to
ACLEDA, and only if ACLEDA also fails do we fall through to the NBC. The bot and
web app announce which source produced each reading, so you can always tell.

Both bank sites are server-rendered HTML with no public JSON API, so parsing is
layered from most to least structured (as in the sibling project
https://github.com/sarakmacbook/USD-KHR_Data_From_WingBank):
    - HTML tables (<tr>/<td> rows)         — what both banks publish today
    - "pair" spans (Wing parallel markup)  — Wing's cell labels
    - reader-mirror tables                 — when the bank host is unreachable
    - raw token scan                       — the tag-stripped text of the page
Every layer normalizes to the same {"buy","sell"} pair and cross-checks that the
numbers look like a plausible Cambodian bank spread.

Because some firewalls/VPSes block the bank hosts themselves, each bank also has
a well-known *reader* mirror (r.jina.ai) that returns the same board as plain
markdown. These are only tried for that bank, after the primary URL, and never
for a different bank — so the priority order (Wing before ACLEDA) still holds
even when a mirror is used.
"""
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

# ---- sources (priority order) -----------------------------------------------
WING_PAGE = "https://www.wingbank.com.kh/en/exchange-rate"
WING_MIRROR = "https://r.jina.ai/{url}"

ACLEDA_PAGE = "https://www.acledabank.com.kh/kh/eng/ps_cmforeignexchange?t=p"
ACLEDA_MIRROR = "https://r.jina.ai/{url}"

NBC_PAGE = "https://www.nbc.gov.kh/english/economic_research/exchange_rate.php"

TIMEOUT = 10
MAX_MIRROR_BYTES = 3_000_000  # reader mirrors echo the whole site; cap the read.

# 3-letter currency codes and a slash-separated "FROM/TO" pair.
_CCY = r"[A-Z]{3}"
PAIR_RE = re.compile(rf"\b({_CCY})\s*[/\\|]\s*({_CCY})\b")

# Numbers exactly as the page prints them, incl. thousands separators.
NUM_RE = re.compile(r"\d{1,3}(?:[,\u00a0\u202f ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")

# A rate cell: a bare number with an optional trailing currency code.
CCY_SUFFIX = r"(?:KHR|USD|THB|VND|EUR|GBP|JPY|CNY|KRW|PHP|IDR|SGD|MYR|HKD|CAD|AUD|NZD|CHF|LAK)"
CELL_NUM_RE = re.compile(
    rf"^\s*(?P<n>[\d.,\u00a0\u202f ]+?)\s*{CCY_SUFFIX}?\s*$", re.I)

MIN_USDKHR, MAX_USDKHR = 3000.0, 5000.0  # sane USD/KHR window


def _get(url, timeout=TIMEOUT):
    """GET a URL, raise on transport/HTTP errors. Stream mirrors to cap size."""
    r = requests.get(url, headers=HEADERS, timeout=timeout, stream=True)
    r.raise_for_status()
    bytes_read, chunks = 0, []
    for chunk in r.iter_content(chunk_size=65536):
        chunks.append(chunk)
        bytes_read += len(chunk)
        if bytes_read > MAX_MIRROR_BYTES:
            break
    return b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")


# ---- shared parsing ---------------------------------------------------------
def to_number(s):
    """Parse a numeric token: 4049 | 1.1485 | 25,650.90 | (1.15) -> float."""
    if isinstance(s, (int, float)):
        return float(s)
    if s is None:
        return None
    t = str(s).strip()
    if not t or not re.fullmatch(r"[()+\-]?[\d.,\u00a0\u202f ()+\-]*", t):
        return None
    core = re.sub(r"[^\d.,]+", "", t)
    if not core or not re.search(r"\d", core):
        return None
    neg = bool(re.fullmatch(r"\(.*\)", t))
    if "," in core:
        if "." in core:
            core = core.replace(",", "")
        elif re.fullmatch(r"\d+,\d{1,2}", core):          # decimal comma
            core = core.replace(",", ".")
        else:                                             # thousand separators
            core = core.replace(",", "")
    try:
        return -float(core) if neg else float(core)
    except ValueError:
        return None


def cell_number(c):
    """Number in a rate cell like '4049', '4,048 KHR', '1.1485'. None otherwise."""
    m = CELL_NUM_RE.match(str(c))
    return to_number(m.group("n")) if m else None


def _parse_row(cells):
    """Parse one row/cell-list/dict. Returns {'pair','buy','sell'} or None.

    A row qualifies when it contains a currency pair and at least one numeric
    cell right after the pair. Numbers must be plausible (0.01..1e6, and inside
    3000..5000 for USD/KHR) and the two sides must agree within a realistic
    bank spread.
    """
    if isinstance(cells, dict):
        seq = [cells.get("pair") or cells.get("currencyPair"),
               cells.get("buy") or cells.get("bankBuy"),
               cells.get("sell") or cells.get("bankSell")]
    else:
        seq = list(cells)
    seq = [c for c in seq if c is not None and str(c).strip()]
    if not seq:
        return None

    pair = pi = None
    for i, c in enumerate(seq):
        m = PAIR_RE.search(str(c).upper())
        if m:
            pair, pi = (m.group(1), m.group(2)), i
            break
    if not pair:
        return None

    nums = []
    for c in seq[pi + 1:]:
        n = cell_number(c)
        if n is None:
            continue
        if nums and n == nums[-1]:
            continue
        nums.append(n)
        if len(nums) >= 2:
            break

    # dict/compact shapes may carry the number with the pair in one field
    if not nums:
        for c in seq[:1]:
            for m in NUM_RE.finditer(str(c)):
                n = to_number(m.group(0))
                if n is not None:
                    nums.append(n)

    if not nums:
        return None

    base, quote = pair
    if base == "USD" and quote == "KHR":
        if not (MIN_USDKHR < nums[0] < MAX_USDKHR):
            return None
    elif not (0.01 < nums[0] < 1_000_000):
        return None

    buy = nums[0]
    sell = nums[1] if len(nums) > 1 else None
    if sell is not None and not (0.3 <= sell / buy <= 3.0):
        sell = None  # implausible bank spread -> treat the second value as noise
    return {"pair": f"{base}/{quote}", "buy": buy, "sell": sell}


def _html_rows(html):
    """All <tr> cell lists + every element whose class contains 'pair'."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    def cell_text(td):
        for img in td.find_all("img"):
            img.decompose()
        return td.get_text(" ", strip=True) or ""

    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) >= 2:
            rows.append([cell_text(t) for t in tds])
    for el in soup.find_all(True):
        if "pair" in " ".join(el.get("class", [])):
            t = el.get_text(" ", strip=True)
            if t:
                rows.append([t])
    return rows


def _md_rows(markdown):
    """Data rows (skipping `|---|---|` separators) of a reader-mirror table."""
    rows = []
    for raw in markdown.splitlines():
        line = raw.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [re.sub(r"!\[[^\]]*\]\([^)]*\)", "", c).strip()
                 for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def _strip_tags(html):
    txt = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    txt = re.sub(r"<style[^>]*>.*?</style>", " ", txt, flags=re.S | re.I)
    txt = re.sub(r"<!--.*?-->", " ", txt, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"\s+", " ", txt)


def extract_pair(text, primary="USD", quote="KHR"):
    """Return (buy, sell) for `primary`/`quote` from an HTML/markdown capture."""
    pair_key = f"{primary}/{quote}"

    # 1) structured rows (tables / pair spans)
    for row in _html_rows(text):
        res = _parse_row(row)
        if res and res["pair"] == pair_key:
            return res["buy"], res["sell"] if res["sell"] is not None else res["buy"]

    # 2) reader-mirror markdown tables
    for row in _md_rows(text):
        res = _parse_row(row)
        if res and res["pair"] == pair_key:
            return res["buy"], res["sell"] if res["sell"] is not None else res["buy"]

    # 3) token scan over tag-stripped text
    pm = PAIR_RE.search(text.upper())
    if pm and pm.group(1) == primary and pm.group(2) == quote:
        nums = []
        for t in NUM_RE.findall(_strip_tags(text)):
            n = to_number(t)
            if n is not None and (not nums or n != nums[-1]):
                nums.append(n)
            if len(nums) >= 3:
                break
        if nums and MIN_USDKHR < nums[0] < MAX_USDKHR:
            buy = nums[0]
            sell = next((n for n in nums[1:] if 0.3 <= n / buy <= 3.0), None)
            return buy, sell if sell is not None else buy
    raise ValueError(f"{pair_key} rate not found in capture")


# ---- Wing Bank (top priority) ------------------------------------------------
def fetch_wing():
    """Fetch USD/KHR from Wing Bank (top priority). Raise RuntimeError on failure."""
    # 1) primary page (rate tables / pair spans / token scan)
    html = _get(WING_PAGE)
    try:
        buy, sell = extract_pair(html)
        return {"buy": buy, "sell": sell, "source": "WING BANK", "url": WING_PAGE}
    except ValueError:
        log.debug("Wing primary page parse failed, trying reader mirror")

    # 2) reader mirror of the same Wing page — only for Wing
    mirror = WING_MIRROR.replace("{url}", WING_PAGE)
    md = _get(mirror)
    buy, sell = extract_pair(md)
    return {"buy": buy, "sell": sell, "source": "WING BANK", "url": mirror}


# ---- ACLEDA Bank (fallback #2) -----------------------------------------------
def fetch_acleda():
    """Fetch USD/KHR from ACLEDA (fallback). Raise RuntimeError on failure."""
    # 1) primary page — first table flips between USD/KHR "Bank Buy"-over-"Sell"
    #    (header) and "Buy"/"Sell" (body); layered parse handles both
    html = _get(ACLEDA_PAGE)
    try:
        buy, sell = extract_pair(html)
        return {"buy": buy, "sell": sell, "source": "ACLEDA BANK", "url": ACLEDA_PAGE}
    except ValueError:
        log.debug("ACLEDA primary page parse failed, trying reader mirror")

    # 2) reader mirror of the same ACLEDA page — only for ACLEDA
    mirror = ACLEDA_MIRROR.replace("{url}", ACLEDA_PAGE)
    md = _get(mirror)
    buy, sell = extract_pair(md)
    return {"buy": buy, "sell": sell, "source": "ACLEDA BANK", "url": mirror}


# ---- National Bank of Cambodia (final fallback #3) ---------------------------
def fetch_nbc():
    """Fetch the official USD mid rate from the NBC (last-resort fallback)."""
    from bs4 import BeautifulSoup
    html = _get(NBC_PAGE)
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    m = re.search(r"USD\s*/\s*KHR.*?(\d{3,4}(?:\.\d+)?)\D+(\d{3,4}(?:\.\d+)?)", text, re.I)
    if m:
        return {"buy": float(m.group(1)), "sell": float(m.group(2)),
                "source": "NBC (official)", "url": NBC_PAGE}
    m2 = re.search(r"Official Exchange Rate\s*:\s*(\d{3,4}(?:\.\d+)?)\s*KHR\s*/\s*USD", text, re.I)
    if m2:
        v = float(m2.group(1))
        return {"buy": v, "sell": v, "source": "NBC (official mid)", "url": NBC_PAGE}
    raise RuntimeError("NBC: rate not found")


# ---- public entry point -------------------------------------------------------
_SOURCES = (("WING", fetch_wing), ("ACLEDA", fetch_acleda), ("NBC", fetch_nbc))


def get_rate():
    """Return {'buy','sell','source','url','ts'} from the first source that works.

    Priority order is fixed: Wing Bank -> ACLEDA Bank -> National Bank of
    Cambodia. A source is only skipped when its fetch raises; the returned
    'source' names the bank that actually supplied the numbers.
    """
    for name, fn in _SOURCES:
        try:
            d = fn()
            d["ts"] = time.time()
            log.info("rate from %s: buy=%s sell=%s", d["source"], d["buy"], d["sell"])
            return d
        except Exception as e:
            log.warning("%s fetch failed: %s", name, e)
    raise RuntimeError("All sources failed (check internet / all bank sites down)")
