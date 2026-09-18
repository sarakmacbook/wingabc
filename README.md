# KHR/USD Tracker Bot 🇰🇭💵

CoinMarketCap-style USD/KHR tracking with a Telegram bot + home-screen web app (PWA).

## Sources (priority order)
1. **Wing Bank** — https://www.wingbank.com.kh/en/exchange-rate
2. **ACLEDA Bank** — https://www.acledabank.com.kh/kh/eng/ps_cmforeignexchange?t=p
3. **National Bank of Cambodia** — final fallback

Wing is always tried first. If Wing is down/unreachable, the bot automatically switches to ACLEDA and tells you; when Wing recovers it switches back. If your host's egress firewall blocks the bank host itself, each bank is also fetched via a reader mirror (`r.jina.ai/<page-url>`) — still for that same bank only, so the Wing → ACLEDA priority never changes.

## Setup
```bash
pip install -r requirements.txt
cp .env.example .env       # paste your token from @BotFather
python bot.py
```
Then open Telegram, send `/start` to your bot. It will **ask you where to store price data**:
- SQLite file (recommended, default `data/khr_usd.db`)
- CSV file
- PostgreSQL (paste a connection URL)

Then it asks for the interval. Use `1` for every second like CoinMarketCap.

## Commands
| Command | What it does |
|---|---|
| `/track` | start tracking |
| `/stoptrack` | stop |
| `/price` | current rate + which bank it's from |
| `/graph 24` | chart of last 24h (any number of hours) |
| `/calc` | **price calculator**: enter amount → USD→KHR or KHR→USD → buy/sell/mid rate |
| `/alert 5` | Telegram notification when mid price moves ≥ 5 KHR |
| `/status` | tracker & storage status |

## Web app (home screen on BOTH devices)
```bash
python app.py        # serves on port 5000
```
- **Android (Chrome):** open `http://<computer-ip>:5000` → menu ⋮ → **Add to Home screen** → allow notifications.
- **iPhone (Safari):** open the same URL → Share → **Add to Home Screen**. Notifications on iOS need the tab open (real iOS push needs a paid Apple setup) — **use the Telegram bot for reliable iOS notifications instead**.

## Notes
- Banks update rates a few times per day; per-second tracking records every tick but values repeat between bank updates.
- Every-second polling of bank sites from one IP may eventually get rate-limited — if that happens set interval to 30–60s.
- Run it 24/7 on a VPS (e.g. a $5/mo server) or a Raspberry Pi so tracking never stops.
