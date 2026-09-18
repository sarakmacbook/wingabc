#!/usr/bin/env bash
# ============================================================
#  KHR/USD Tracker Bot — one-click install
#  One-liner (fresh server):  curl -fsSL <url>/install.sh | bash
#  Local clone:                ./install.sh
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/sarakmacbook/wingabc.git"
APP_DIR="wingabc"

echo "================================================="
echo "  KHR/USD Tracker Bot — one-click install"
echo "================================================="

# --- 0. Make sure we are inside the project -----------------------------
# When piped via `curl ... | bash`, $0 is "bash" (not a file), so there
# is no script directory — the repo must be cloned first.
if [ -n "${0:-}" ] && [ -f "${0:-}" ]; then
  cd "$(dirname "$0")"
fi

if [ ! -f requirements.txt ] || [ ! -f bot.py ]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git not found, and the project files aren't here."
    echo "Install git first, then re-run this script:"
    echo "   Ubuntu / Debian / Raspberry Pi:  sudo apt update && sudo apt install -y git"
    echo "   macOS (Homebrew):                brew install git"
    exit 1
  fi
  if [ -d "$APP_DIR/.git" ]; then
    echo ">> Found existing $APP_DIR/ — updating ..."
    cd "$APP_DIR"
    git pull --ff-only || true
  elif [ -d "$APP_DIR" ]; then
    echo ">> Found existing $APP_DIR/ (not a git clone) — using it."
    cd "$APP_DIR"
  else
    echo ">> Cloning $REPO_URL ..."
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
  fi
fi

if [ ! -f requirements.txt ] || [ ! -f bot.py ]; then
  echo "ERROR: still can't find requirements.txt / bot.py in $(pwd)."
  echo "Clone the repo manually and run ./install.sh from inside it:"
  echo "   git clone $REPO_URL && cd $APP_DIR && ./install.sh"
  exit 1
fi
echo ">> Project dir: $(pwd)"

# --- 1. Find Python 3 ------------------------------------------------
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
  echo "ERROR: Python 3 not found. Install it first, then re-run this script:"
  echo "   Ubuntu / Debian / Raspberry Pi:  sudo apt update && sudo apt install -y python3 python3-venv"
  echo "   macOS (Homebrew):                brew install python"
  exit 1
fi
echo ">> Python: $($PYTHON --version 2>&1)"

# --- 2. Virtual environment (recommended) ---------------------------
VENV=""
if [ -d .venv ]; then
  VENV=".venv"
elif "$PYTHON" -m venv .venv >/dev/null 2>&1; then
  echo ">> Created virtual environment in .venv"
  VENV=".venv"
else
  rm -rf .venv
  echo ">> Could not create a virtual environment."
  echo "   On Ubuntu/Debian this usually means python3-venv is missing:"
  echo "     sudo apt update && sudo apt install -y python3-venv"
  echo "   Then re-run this script. Falling back to system Python for now."
fi

if [ -n "$VENV" ]; then
  PY="$VENV/bin/python"
else
  PY="$PYTHON"
fi
pip_install() { "$PY" -m pip install "$@"; }

echo ">> Installing Python packages ..."
pip_install --upgrade pip >/dev/null 2>&1 || true
pip_install -r requirements.txt

# --- 3. Sanity check: all imports work -------------------------------
"$PY" -c "import telegram, requests, bs4, matplotlib, dotenv, flask; print('>> All dependencies imported OK')"

# --- 4. Create .env from the template (only if missing) --------------
if [ ! -f .env ]; then
  if   [ -f env.example  ]; then cp env.example  .env
  elif [ -f .env.example ]; then cp .env.example .env
  else echo "BOT_TOKEN=" > .env
  fi
  echo ">> Created .env"
fi

# --- 5. Telegram bot token: ASK the user, verify it, save it ---------
# Works interactively AND when piped (curl -fsSL ... | bash): prompts are
# read from the terminal (/dev/tty), never from the piped script stream.
ask() {                            # ask "prompt"  -> answer in $REPLY (rc 1 = no terminal)
  REPLY=""
  if [ -t 0 ]; then
    read -r -p "$1" REPLY
  elif { : < /dev/tty; } 2>/dev/null; then
    printf '%s' "$1" > /dev/tty
    read -r REPLY < /dev/tty
  else
    return 1
  fi
}

extract_token() {                  # pull the token out of any pasted BotFather text
  printf '%s' "$1" | grep -oE '[0-9]{6,12}:[A-Za-z0-9_-]{25,64}' | head -n 1
}

verify_token() {                   # verify_token TOKEN  ->  "OK|@user (name)" / "BAD|desc" / "NET|desc"
  "$PY" - "$1" <<'PYEOF'
import sys, requests
tok = sys.argv[1]
try:
    d = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=10).json()
except Exception as e:
    print(f"NET|could not reach Telegram ({type(e).__name__})")
    sys.exit(0)
if d.get("ok"):
    r = d["result"]
    print(f"OK|@{r.get('username','?')} ({r.get('first_name','?')})")
else:
    print(f"BAD|{d.get('description', 'token rejected')}")
PYEOF
}

save_token() {                     # save_token TOKEN — (re)write the BOT_TOKEN line in .env
  if grep -qE '^[[:space:]]*#?[[:space:]]*BOT_TOKEN=' .env 2>/dev/null; then
    grep -vE '^[[:space:]]*#?[[:space:]]*BOT_TOKEN=' .env > .env.tmp || true
    printf 'BOT_TOKEN=%s\n' "$1" >> .env.tmp
    mv .env.tmp .env
  else
    printf '\nBOT_TOKEN=%s\n' "$1" >> .env
  fi
}

live_rate() {                      # best-effort live rate check, capped at ~20 s
  "$PY" - <<'PYEOF' 2>/dev/null
import threading
res = {}
def _work():
    try:
        from scraper import get_rate
        res["r"] = get_rate()
    except Exception:
        pass
t = threading.Thread(target=_work, daemon=True)
t.start(); t.join(20)
if "r" in res:
    r = res["r"]
    print(f"{r['buy']:,.0f} / {r['sell']:,.0f} KHR per USD  [{r['source']}]")
PYEOF
}

BOT_NAME=""
current="$(grep -E '^[[:space:]]*#?[[:space:]]*BOT_TOKEN=' .env 2>/dev/null | head -n 1 | cut -d= -f2- | tr -d '\" ' | tr -d "'")"
case "$current" in 1234567890:*) current="";; esac        # template placeholder

if [ -n "$current" ]; then
  echo ">> Checking the token already saved in .env ..."
  out="$(verify_token "$current")"
  case "$out" in
    "OK|"*)  BOT_NAME="${out#OK|}"; echo "   ✓ verified: $BOT_NAME" ;;
    "BAD|"*) echo "   ✗ Telegram rejected it (${out#BAD|}) — let's set a fresh one." ;;
    *)       echo "   ⚠ ${out#NET|} — you can paste it again below." ;;
  esac
fi

while [ -z "$BOT_NAME" ]; do
  echo
  echo ">> Last step — your free Telegram bot token (~1 minute):"
  echo "   1. open  https://t.me/BotFather  in Telegram"
  echo "   2. send /newbot and follow its steps"
  echo "   3. copy the token it gives you  (looks like  1234567890:AAEx4g...)"
  ask ">> Paste the token here (Enter to skip): " || break
  [ -z "$REPLY" ] && break
  tok="$(extract_token "$REPLY")"
  [ -z "$tok" ] && tok="$(printf '%s' "$REPLY" | tr -d ' ')"
  out="$(verify_token "$tok")"
  case "$out" in
    "OK|"*)
      BOT_NAME="${out#OK|}"
      save_token "$tok"
      echo "   ✓ Verified!  Bot: $BOT_NAME"
      echo "   ✓ Saved to .env"
      ;;
    "BAD|"*)
      echo "   ✗ Telegram rejected it: ${out#BAD|}"
      echo "     Make sure you copied the whole token from @BotFather's message."
      ;;
    *)
      echo "   ⚠ ${out#NET|}"
      ask "   Save it anyway and verify later? [y/N] " || break
      case "$REPLY" in
        y|Y|yes)
          save_token "$tok"
          BOT_NAME="(token saved — verified on first run)"
          echo "   ✓ Saved to .env"
          ;;
      esac
      ;;
  esac
done

# --- 6. Summary + optional immediate launch --------------------------
RATE="$(live_rate || true)"
echo
echo "================================================="
if [ -n "$BOT_NAME" ]; then
  echo "  ✅  You're all set!"
  echo "  Bot   : $BOT_NAME"
  echo "  Token : saved in $(pwd)/.env"
  if [ -n "$RATE" ]; then
    echo "  Live  : $RATE"
  else
    echo "  Live  : rate test skipped (bank sites unreachable from here)"
  fi
  echo "-------------------------------------------------"
  if ask "  Start the bot now? [Y/n] "; then
    case "$REPLY" in
      ""|y|Y|yes)
        echo "  >> Starting the bot — press Ctrl+C to stop."
        echo "================================================="
        exec "$PY" bot.py
        ;;
    esac
  fi
  echo
  echo "  Start later      :  $PY bot.py"
  echo "  Then in Telegram :  send /start to $BOT_NAME"
  echo "  Web dashboard    :  $PY app.py     (http://<your-ip>:5000)"
else
  echo "  ⚠  Almost done — just add your bot token"
  echo "-------------------------------------------------"
  if [ -n "$RATE" ]; then echo "  Live rate check  :  $RATE"; fi
  echo
  echo "  Run:  $PY bot.py"
  echo "  It will ASK you to paste the token (from https://t.me/BotFather),"
  echo "  verify it against Telegram, save it to .env and start."
  echo
  echo "  Web dashboard (optional):  $PY app.py   (http://<your-ip>:5000)"
fi
echo "================================================="
