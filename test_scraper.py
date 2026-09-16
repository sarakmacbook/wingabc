"""Offline tests for the Wing-first / ACLEDA-fallback scraper.

Uses captured copies of the real bank pages (Wing `pair` spans + tables,
ACLEDA `4,050 KHR` tables, r.jina.ai reader-markdown, list-based markup) so the
parsing logic is verified without network access.

Run:  python3 test_scraper.py   (or pytest test_scraper.py)
"""
import json
import os

import scraper

HERE = os.path.dirname(os.path.abspath(__file__))

# --- captured formats --------------------------------------------------------

WING_TABLE = """<table><tr><td>Cambodian Riel<span class="pair">USD/KHR</span></td>
<td>USD/KHR</td><td>4051</td><td>4059</td></tr>
<tr><td>European EURO<span class="pair">EUR/USD</span></td><td>EUR/USD</td>
<td>1.142</td><td>1.165</td></tr></table>"""

WING_DIV = """<div class="rate-row"><span class="pair">USD/KHR</span>
<span class="buy">Bank Buy 4050</span><span class="sell">Bank Sell 4060</span></div>"""

WING_MD = """| Currency | Currency Pair | Bank Buy | Bank Sell |
| --- | --- | --- | --- |
| ![x](a.png)Cambodian Riel USD/KHR | USD/KHR | 4049 | 4059 |"""

ACLEDA_TABLE = """<table><thead><tr><th>Currency</th><th>Bank Buy</th>
<th>Bank Sell</th></tr></thead><tbody>
<tr><td><img alt=""/>USD / KHR</td><td>4,048 KHR</td><td>4,062 KHR</td></tr>
<tr><td><img alt=""/>USD / KHR</td><td>4,050 KHR</td><td>4,060 KHR</td></tr></tbody></table>"""

WING_API_JSON = json.dumps({
    "data": {"telegraphicTransfer": [
        {"currencyPair": "USD/KHR", "currencyName": "Cambodian Riel",
         "bankBuy": 4049, "bankSell": 4059}]}})


# --- tests -------------------------------------------------------------------

def test_wing_table():
    assert scraper.extract_pair(WING_TABLE) == (4051.0, 4059.0)


def test_wing_pair_span():
    assert scraper.extract_pair(WING_DIV) == (4050.0, 4060.0)


def test_wing_reader_markdown():
    assert scraper.extract_pair(WING_MD) == (4049.0, 4059.0)


def test_acleda_khr_suffix_cells():
    # first table matches first: 4048/4062 (special rate)
    assert scraper.extract_pair(ACLEDA_TABLE) == (4048.0, 4062.0)


def test_wrong_pair_is_not_usd_khr():
    html = "<table><tr><td>EUR/USD</td><td>1.142</td><td>1.165</td></tr></table>"
    try:
        scraper.extract_pair(html)
        assert False, "should raise"
    except ValueError:
        pass


def test_dict_row():
    assert scraper._parse_row({"currencyPair": "USD/KHR",
                               "bankBuy": 4049, "bankSell": 4059}) == \
        {"pair": "USD/KHR", "buy": 4049.0, "sell": 4059.0}


def test_priority_chain_all_ok():
    orig = scraper._SOURCES
    try:
        scraper._SOURCES = (("WING", lambda: {"buy": 4051, "sell": 4059, "source": "WING BANK"}),
                            ("ACLEDA", lambda: {"buy": 4048, "sell": 4062, "source": "ACLEDA BANK"}),
                            ("NBC", lambda: {"buy": 4053, "sell": 4053, "source": "NBC (official)"}))
        assert scraper.get_rate()["source"] == "WING BANK"
    finally:
        scraper._SOURCES = orig


def test_priority_chain_wing_down():
    orig = scraper._SOURCES
    try:
        def wing_down():
            raise RuntimeError("down")
        scraper._SOURCES = (("WING", wing_down),
                            ("ACLEDA", lambda: {"buy": 4048, "sell": 4062, "source": "ACLEDA BANK"}),
                            ("NBC", lambda: {"buy": 4053, "sell": 4053, "source": "NBC (official)"}))
        r = scraper.get_rate()
        assert r["source"] == "ACLEDA BANK" and r["buy"] == 4048
    finally:
        scraper._SOURCES = orig


def test_all_down_raises():
    orig = scraper._SOURCES
    try:
        scraper._SOURCES = (("WING", lambda: (_ for _ in ()).throw(RuntimeError("down"))),
                            ("ACLEDA", lambda: (_ for _ in ()).throw(RuntimeError("down"))),
                            ("NBC", lambda: (_ for _ in ()).throw(RuntimeError("down"))))
        try:
            scraper.get_rate()
            assert False, "should raise"
        except RuntimeError:
            pass
    finally:
        scraper._SOURCES = orig


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
