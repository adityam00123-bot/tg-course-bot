#!/bin/bash
# =================================================================
# Google Cloud Shell / Linux One-Click Starter Script
# =================================================================

set -e

echo "===================================================="
echo "🚀 Setting up Telegram Content Migration Bot on Cloud"
echo "===================================================="

# Ensure Python3, ffmpeg, fonts, and tmux are available
if ! command -v ffmpeg &> /dev/null; then
    echo "Installing ffmpeg for video watermarking & thumbnails..."
    sudo apt update && sudo apt install -y ffmpeg fonts-dejavu-core tmux python3-venv || true
fi

# Create and activate virtual environment
if [ ! -d "venv" ]; then
    echo "Creating Python virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

echo "Installing requirements..."
pip install --upgrade pip
pip install -r requirements.txt

echo "===================================================="
echo "✅ Environment Ready! Launching Bot..."
echo "===================================================="

# Remove old lock file if any
rm -f .bot.lock

# Launch bot with 24/7 background persistence + live console
echo "===================================================="
echo "🎉 Launching Telegram Migration Bot (Persistent Session)..."
echo "===================================================="

if [ -n "$TMUX" ]; then
    # Already inside tmux: run directly in foreground
    python main.py
else
    # Launch in persistent tmux and attach live
    if command -v tmux &> /dev/null; then
        tmux kill-session -t cvbot 2>/dev/null || true
        tmux new-session -d -s cvbot "source venv/bin/activate && python main.py; read -p 'Press Enter to exit'"
        tmux attach -t cvbot
    else
        python main.py
    fi
fi
