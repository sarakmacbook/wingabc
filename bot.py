#!/usr/bin/env python3
"""
KHR/USD Tracker Telegram bot — CoinMarketCap-style, 1-second tracking.
Commands:
  /start          setup wizard (storage choice, interval, notifications)
  /price          current rate + source
  /graph [hours]  price chart (default 24h)
  /calc           price calculator (enter amount, pick USD->KHR or KHR->USD, rate type)
  /track          start 1s tracking
  /stoptrack      stop tracking
  /alert <KHR>    notify me when mid price moves by >= N KHR from now
  /status         tracker & storage status
"""
import os
import re
import sys
import json
import time
import logging
import threading
import asyncio

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          ConversationHandler, MessageHandler, filters, ContextTypes)

from scraper import get_rate
from storage import create_storage
from graph import render_chart

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("bot")

SETTINGS_FILE = "data/settings.json"
os.makedirs("data", exist_ok=True)

# conversation states
(S_SETUP_STORAGE, S_SETUP_PATH, S_SETUP_INTERVAL,
 S_CALC_DIR, S_CALC_AMOUNT, S_CALC_RATE) = range(6)

DEFAULTS = {"storage_kind": None, "storage_path": None,
            "interval": 1, "notify": True, "alert_threshold": None,
            "alert_base": None}

_settings_lock = threading.Lock()


def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            d = json.load(f)
    except Exception:
        d = {}
    return {**DEFAULTS, **d}


def save_settings(s):
    with _settings_lock:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(s, f)


def get_store():
    s = load_settings()
    return create_storage(s["storage_kind"], s["storage_path"])


# ---------------- tracker ----------------
_tracker = {"running": False, "thread": None, "stop": threading.Event(),
            "last": None, "errors": 0}

# python-telegram-bot v20+ runs the bot on its own asyncio loop; cross-thread
# sends must be scheduled onto that loop (app._loop no longer exists).
_loop_ref = {"loop": None}


async def _capture_loop(app):
    _loop_ref["loop"] = asyncio.get_running_loop()


def _send(chat_id, text):
    """Schedule a cross-thread Telegram send onto the bot's running loop."""
    loop = _loop_ref.get("loop")
    if not loop:
        return
    asyncio.run_coroutine_threadsafe(
        _loop_ref["app"].bot.send_message(chat_id=chat_id, text=text), loop)


def tracker_loop(app):
    _loop_ref["app"] = app
    st = get_store()
    s = load_settings()
    interval = max(1, int(s.get("interval", 1)))
    alert_base = s.get("alert_base")
    while not _tracker["stop"].is_set():
        try:
            r = get_rate()
            st.insert(r["ts"], r["buy"], r["sell"], r["source"])
            _tracker["last"] = r
            _tracker["errors"] = 0
            # notifications
            if s.get("notify"):
                th = s.get("alert_threshold")
                mid = (r["buy"] + r["sell"]) / 2
                if th and alert_base is not None and abs(mid - alert_base) >= th:
                    for cid in s.get("chat_ids", []):
                        _send(cid, f"🚨 USD/KHR moved {mid - alert_base:+,.2f} KHR!\n"
                                  f"Buy: {r['buy']:,.2f} | Sell: {r['sell']:,.2f}\n"
                                  f"Source: {r['source']}")
                    s["alert_base"] = mid
                    save_settings(s)
            # fallback-source notification (Wing down -> ACLEDA, etc.)
            if s.get("last_source") and s["last_source"] != r["source"] and s.get("notify"):
                down = s["last_source"]
                now = r["source"]
                if down.startswith("WING") and now.startswith("ACLEDA"):
                    msg = (f"⚠️ Wing Bank is down/unreachable — switched to "
                           f"ACLEDA Bank.\nBuy: {r['buy']:,.2f} | Sell: {r['sell']:,.2f}")
                elif now.startswith("WING"):
                    msg = f"✅ Wing Bank is back — rate from Wing again."
                else:
                    msg = f"⚠️ Source changed: {down} → {now}"
                for cid in s.get("chat_ids", []):
                    _send(cid, msg)
            s["last_source"] = r["source"]
        except Exception as e:
            _tracker["errors"] += 1
            log.warning("track error: %s", e)
        _tracker["stop"].wait(interval)


# ---------------- handlers ----------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = load_settings()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💾 SQLite file (recommended)", callback_data="st_sqlite")],
        [InlineKeyboardButton("📄 CSV file", callback_data="st_csv")],
        [InlineKeyboardButton("🐘 PostgreSQL (I have a URL)", callback_data="st_postgres")],
    ])
    if s.get("storage_kind"):
        await update.message.reply_text(
            f"✅ Already set up (storage: {s['storage_kind']}).\n"
            "Commands: /price /graph /calc /track /stoptrack /alert /status")
        return
    await update.message.reply_text(
        "🇰🇭💵 <b>KHR/USD Tracker Bot</b>\n\nWhere do you want to store price data?",
        parse_mode="HTML", reply_markup=kb)
    return S_SETUP_STORAGE


async def setup_storage(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    kind = q.data.replace("st_", "")
    ctx.user_data["storage_kind"] = kind
    if kind == "sqlite":
        await q.edit_message_text("Send me the SQLite file path, e.g. <code>data/khr_usd.db</code>\n"
                                  "(or send 'default')", parse_mode="HTML")
    elif kind == "csv":
        await q.edit_message_text("Send me the CSV file path, e.g. <code>data/khr_usd.csv</code>\n"
                                  "(or send 'default')", parse_mode="HTML")
    else:
        await q.edit_message_text("Send me the PostgreSQL connection URL, e.g.\n"
                                  "<code>postgresql://user:pass@host:5432/dbname</code>", parse_mode="HTML")
    return S_SETUP_PATH


async def setup_path(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kind = ctx.user_data["storage_kind"]
    txt = update.message.text.strip()
    if kind in ("sqlite", "csv") and txt.lower() == "default":
        txt = f"data/khr_usd.{kind}"
    if kind == "postgres" and not txt.startswith("postgresql"):
        await update.message.reply_text("That doesn't look like a postgres URL. Try again:")
        return S_SETUP_PATH
    s = load_settings()
    s["storage_kind"], s["storage_path"] = kind, txt
    # init storage now to validate
    try:
        get_store()
    except Exception as e:
        await update.message.reply_text(f"❌ Could not open storage: {e}\nTry again:")
        return S_SETUP_PATH
    s.setdefault("chat_ids", [])
    if update.effective_chat.id not in s["chat_ids"]:
        s["chat_ids"].append(update.effective_chat.id)
    save_settings(s)
    await update.message.reply_text(
        f"✅ Storage set: <b>{kind}</b> → <code>{txt}</code>\n\n"
        f"Now send the tracking interval in seconds (e.g. <b>1</b> for every second, "
        f"<b>60</b> to be gentle on the bank sites):", parse_mode="HTML")
    return S_SETUP_INTERVAL


async def setup_interval(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        iv = max(1, int(update.message.text.strip()))
    except ValueError:
        await update.message.reply_text("Please send a number (seconds):")
        return S_SETUP_INTERVAL
    s = load_settings()
    s["interval"] = iv
    save_settings(s)
    await update.message.reply_text(
        f"🚀 Setup complete! Tracking every <b>{iv}s</b>.\n"
        f"Use /track to start, /price for current rate, /graph for chart, /calc to convert.",
        parse_mode="HTML")
    return ConversationHandler.END


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


async def price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        r = get_rate()
    except Exception as e:
        await update.message.reply_text(f"❌ All sources failed: {e}")
        return
    mid = (r["buy"] + r["sell"]) / 2
    await update.message.reply_text(
        f"💵 USD/KHR\nBuy:  <b>{r['buy']:,.2f}</b> KHR\nSell: <b>{r['sell']:,.2f}</b> KHR\n"
        f"Mid:  <b>{mid:,.2f}</b> KHR\nSource: {r['source']}\n🕒 {time.strftime('%H:%M:%S')}",
        parse_mode="HTML")


async def graph_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    hours = 24
    if ctx.args:
        try:
            hours = float(ctx.args[0])
        except ValueError:
            pass
    rows = get_store().since(time.time() - hours * 3600)
    if len(rows) < 2:
        await update.message.reply_text("Not enough data yet — run /track first.")
        return
    buf = render_chart(rows, hours)
    await update.message.reply_photo(photo=buf, caption=f"USD/KHR — last {hours:g}h ({len(rows)} points)")


async def track(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not load_settings().get("storage_kind"):
        await update.message.reply_text("Run /start first to choose storage.")
        return
    if _tracker["running"]:
        await update.message.reply_text("Already tracking.")
        return
    _tracker["stop"].clear()
    t = threading.Thread(target=tracker_loop, args=(ctx.application,), daemon=True)
    t.start()
    _tracker.update(running=True, thread=t)
    await update.message.reply_text(
        f"▶️ Tracking every {load_settings().get('interval',1)}s. /stoptrack to stop.\n"
        f"Notifications: {'ON' if load_settings().get('notify') else 'OFF'}")


async def stoptrack(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    _tracker["stop"].set()
    _tracker["running"] = False
    await update.message.reply_text("⏹ Tracking stopped.")


async def alert(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /alert 5  — notify when mid price moves ≥ 5 KHR.")
        return
    try:
        th = float(ctx.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /alert 5")
        return
    s = load_settings()
    s["alert_threshold"] = th
    s["alert_base"] = None
    save_settings(s)
    await update.message.reply_text(f"🔔 Alert set: you'll be notified when the mid price moves ≥ {th:g} KHR.")


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    s = load_settings()
    last = _tracker["last"]
    await update.message.reply_text(
        f"Running: {_tracker['running']}\nInterval: {s.get('interval')}s\n"
        f"Storage: {s.get('storage_kind')} → {s.get('storage_path')}\n"
        f"Last: {last}\nErrors: {_tracker['errors']}")


# ---- price calculator conversation ----
async def calc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 USD → KHR", callback_data="dir_uskhr"),
         InlineKeyboardButton("💵 KHR → USD", callback_data="dir_khrusd")],
    ])
    await update.message.reply_text("Convert which direction?", reply_markup=kb)
    return S_CALC_DIR


async def calc_dir(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["dir"] = q.data.replace("dir_", "")
    await q.edit_message_text("Enter the amount (numbers only, e.g. 100):")
    return S_CALC_AMOUNT


async def calc_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        amt = float(update.message.text.replace(",", "").strip())
    except ValueError:
        await update.message.reply_text("Numbers only, try again:")
        return S_CALC_AMOUNT
    ctx.user_data["amount"] = amt
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Buy rate (bank buys USD)", callback_data="rt_buy"),
         InlineKeyboardButton("Sell rate (bank sells USD)", callback_data="rt_sell")],
        [InlineKeyboardButton("Mid rate", callback_data="rt_mid")],
    ])
    await update.message.reply_text("Which rate should I use?", reply_markup=kb)
    return S_CALC_RATE


async def calc_rate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    rt = q.data.replace("rt_", "")
    r = get_rate()
    mid = (r["buy"] + r["sell"]) / 2
    rate = {"buy": r["buy"], "sell": r["sell"], "mid": mid}[rt]
    amt, d = ctx.user_data["amount"], ctx.user_data["dir"]
    if d == "uskhr":
        res = amt * rate
        await q.edit_message_text(
            f"💵 <b>{amt:,.2f} USD</b> × {rate:,.2f} (buy) = <b>{res:,.2f} KHR</b>\n"
            f"Source: {r['source']}", parse_mode="HTML")
    else:
        res = amt / rate
        await q.edit_message_text(
            f"💵 <b>{amt:,.2f} KHR</b> ÷ {rate:,.2f} (sell) = <b>{res:,.2f} USD</b>\n"
            f"Source: {r['source']}", parse_mode="HTML")
    return ConversationHandler.END


# ---------------- token onboarding ----------------
# A bot token looks like  1234567890:AAEx4gKz...  (id:secret)
_ANY_TOKEN = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{25,64}")
_PLACEHOLDER_PREFIX = "1234567890:"          # the fake token in env.example
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _describe(me) -> str:
    return f"{me.get('first_name', 'Bot')} (@{me.get('username', '?')})"


def check_token(token: str):
    """Verify a bot token with Telegram. Returns (bot_info, None) or (None, error)."""
    try:
        import requests as _rq
        data = _rq.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10).json()
    except Exception as e:
        return None, f"network: could not reach Telegram ({type(e).__name__})"
    if data.get("ok"):
        return data["result"], None
    return None, data.get("description", "Telegram rejected this token")


def save_token(token: str) -> str:
    """Write BOT_TOKEN into .env next to bot.py, replacing any old line."""
    lines = []
    if os.path.exists(_ENV_FILE):
        with open(_ENV_FILE, encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines()
                     if not re.match(r"\s*#?\s*BOT_TOKEN\s*=", l)]
    lines.append(f"BOT_TOKEN={token}")
    with open(_ENV_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return _ENV_FILE


def prompt_for_token():
    """Ask the user for a bot token, verify it live, save it to .env.

    Returns the token, or None if the terminal isn't interactive / user gave up.
    """
    if not sys.stdin or not sys.stdin.isatty():
        return None
    print("\nNo usable BOT_TOKEN found. Get one free in about a minute:")
    print("  1. open  https://t.me/BotFather  in Telegram")
    print("  2. send /newbot and follow the steps")
    print("  3. copy the token it gives you (looks like 1234567890:AAEx4g...)")
    for _ in range(5):
        try:
            raw = input("\nPaste the token here (Enter to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            return None
        m = _ANY_TOKEN.search(raw)          # tolerate pasting extra text around it
        if not m:
            print("✗ That doesn't look like a bot token — copy the whole thing from @BotFather.")
            continue
        token = m.group(0)
        me, err = check_token(token)
        if me:
            path = save_token(token)
            print(f"✓ Verified!  Bot: {_describe(me)}")
            print(f"✓ Saved to {path}")
            return token
        if err.startswith("network"):
            path = save_token(token)
            print(f"⚠ {err} — saved to {path} anyway; Telegram will re-check it on connect.")
            return token
        print(f"✗ Telegram rejected it: {err}")
        print("  (Copy the FULL token from @BotFather's message, nothing else.)")
    return None


def _resolve_token() -> str:
    """Get a working token: env var → saved .env → interactive prompt."""
    token = (os.environ.get("BOT_TOKEN") or "").strip()
    if not token or token.startswith(_PLACEHOLDER_PREFIX):
        token = prompt_for_token() or ""
        if not token:
            raise SystemExit(
                "\nSetup cancelled. To try again:\n"
                "  python bot.py          (it will ask for the token and save it)\n"
                f"  or set BOT_TOKEN in {_ENV_FILE}\n"
                "  Get a free token from @BotFather:  https://t.me/BotFather"
            )
        os.environ["BOT_TOKEN"] = token
        return token
    # token exists — check it once so problems are obvious immediately
    me, err = check_token(token)
    if me:
        log.info("Token verified — bot: %s", _describe(me))
    elif err.startswith("network"):
        log.warning("%s — starting anyway.", err)
    else:
        log.warning("Telegram rejected BOT_TOKEN: %s", err)
        new = prompt_for_token()
        if not new:
            raise SystemExit(f"Fix BOT_TOKEN in {_ENV_FILE} and run again.")
        token = new
        os.environ["BOT_TOKEN"] = token
    return token


def main():
    token = _resolve_token()
    app = Application.builder().token(token).post_init(_capture_loop).build()

    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            S_SETUP_STORAGE: [CallbackQueryHandler(setup_storage, pattern="^st_")],
            S_SETUP_PATH: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_path)],
            S_SETUP_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, setup_interval)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    calc_conv = ConversationHandler(
        entry_points=[CommandHandler("calc", calc)],
        states={
            S_CALC_DIR: [CallbackQueryHandler(calc_dir, pattern="^dir_")],
            S_CALC_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, calc_amount)],
            S_CALC_RATE: [CallbackQueryHandler(calc_rate, pattern="^rt_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(setup_conv)
    app.add_handler(calc_conv)
    app.add_handler(CommandHandler("price", price))
    app.add_handler(CommandHandler("graph", graph_cmd))
    app.add_handler(CommandHandler("track", track))
    app.add_handler(CommandHandler("stoptrack", stoptrack))
    app.add_handler(CommandHandler("alert", alert))
    app.add_handler(CommandHandler("status", status))
    log.info("Bot started")
    app.run_polling()


if __name__ == "__main__":
    main()
