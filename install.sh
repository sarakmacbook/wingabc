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
  echo "       cd $(pwd)"
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
