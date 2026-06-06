#!/bin/bash
# ============================================================
# APEX Setup Script — Google Cloud e2-micro (Ubuntu 22.04)
# Jalankan sekali setelah VM pertama kali dibuat
# Usage: bash setup_gcloud.sh
# ============================================================

set -e  # stop kalau ada error

APEX_DIR="/home/ubuntu/CryptoAgent"
REPO_URL="https://github.com/nathanpras/crypto_bot.git"

echo "================================================"
echo "  APEX Setup — Google Cloud Ubuntu 22.04"
echo "================================================"

# 1. Update sistem
echo "[1/6] Update sistem..."
sudo apt-get update -qq
sudo apt-get install -y python3 python3-pip python3-venv git curl screen -qq

# 2. Clone repo dari GitHub
echo "[2/6] Clone repo..."
if [ -d "$APEX_DIR" ]; then
    echo "  Folder sudah ada, pull update saja..."
    cd "$APEX_DIR" && git pull
else
    git clone "$REPO_URL" "$APEX_DIR"
fi

# 3. Install dependencies Python
echo "[3/6] Install Python packages..."
cd "$APEX_DIR"
pip3 install -r requirements.txt --quiet

# 4. Buat file .env
echo "[4/6] Setup .env..."
if [ ! -f "$APEX_DIR/.env" ]; then
    cat > "$APEX_DIR/.env" << 'EOF'
TELEGRAM_TOKEN=8215797141:AAE9GxG8IxRYWTq0f5JTfsW_4osROf4TY1s
TELEGRAM_CHAT_ID=1412272198
FRED_API_KEY=af266643fd6f8c6f773bb3ae980ba3ce
ETHERSCAN_API_KEY=YI8YQI8GMAQ56T2T587WKP22NY5QR2FI6R
PAPER_TRADING=true
PORTFOLIO_IDR=10000000
IDR_RATE=17800
LOG_LEVEL=INFO
EOF
    echo "  .env dibuat."
else
    echo "  .env sudah ada, skip."
fi

# 5. Buat folder logs dan data
echo "[5/6] Buat folder..."
mkdir -p "$APEX_DIR/logs"
mkdir -p "$APEX_DIR/data"

# 6. Download data historis
echo "[6/6] Download data historis (ini mungkin 5-10 menit)..."
cd "$APEX_DIR"
python3 main.py --fetch-history

echo ""
echo "================================================"
echo "  Setup selesai! Sekarang aktifkan cron:"
echo "================================================"
echo ""
echo "  crontab -e"
echo ""
echo "  Paste baris ini:"
echo "  0 */4 * * * cd $APEX_DIR && python3 main.py --collect-phase8 >> logs/cron.log 2>&1"
echo "  15 */4 * * * cd $APEX_DIR && python3 main.py --collect-onchain >> logs/cron.log 2>&1"
echo "  30 */4 * * * cd $APEX_DIR && python3 main.py --scan-once >> logs/cron.log 2>&1"
echo ""
echo "  Test scan pertama:"
echo "  python3 main.py --scan-once"
echo ""
