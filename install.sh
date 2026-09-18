#!/usr/bin/env bash
# ============================================================
#  KHR/USD Tracker Bot — one-click install
#  Usage:  ./install.sh     (or: curl -fsSL <url>/install.sh | bash)
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

echo "================================================="
echo "  KHR/USD Tracker Bot — one-click install"
echo "================================================="

# --- 1. Find Python 3 ------------------------------------------------
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then
  echo "ERROR: Python 3 not found. Install it first, then re-run this script:"
  echo "   Ubuntu / Debian / Raspberry Pi:  sudo apt install python3 python3-venv"
  echo "   macOS (Homebrew):                brew install python"
  exit 1
fi
echo ">> Python: $($PYTHON --version 2>&1)"

# --- 2. Virtual environment (recommended) ---------------------------
VENV=""
if [ -d .venv ]; then
  VENV=".venv"
elif $PYTHON -m venv --help >/dev/null 2>&1; then
  echo ">> Creating virtual environment in .venv ..."
  $PYTHON -m venv .venv && VENV=".venv"
else
  echo ">> venv not available — installing into system Python instead."
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
if [ -f .env ]; then
  echo ">> .env already exists — leaving it as is."
elif [ -f env.example ]; then
  cp env.example .env
  echo ">> Created .env from env.example"
elif [ -f .env.example ]; then
  cp .env.example .env
  echo ">> Created .env from .env.example"
fi

# --- 5. Done ----------------------------------------------------------
echo "================================================="
echo "  Done! Next steps:"
echo "  1) Open .env and set your Telegram bot token"
echo "     (get one free from @BotFather in Telegram)."
echo "  2) Start the bot:"
if [ -n "$VENV" ]; then
  echo "       source .venv/bin/activate"
  echo "       python bot.py"
  echo "     (or simply: .venv/bin/python bot.py)"
  echo "  3) Start the web dashboard (optional):"
  echo "       .venv/bin/python app.py     # http://<your-ip>:5000"
else
  echo "       $PY bot.py"
  echo "  3) Start the web dashboard (optional):"
  echo "       $PY app.py     # http://<your-ip>:5000"
fi
echo "================================================="
