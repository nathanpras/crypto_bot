#!/bin/bash
# ============================================================
# install_cron.sh — Install APEX cron jobs ke crontab
# Jalankan sekali di server cloud:
#   bash deploy/install_cron.sh
# ============================================================

APEX_DIR="/home/ubuntu/CryptoAgent"
PYTHON="/usr/bin/python3"

# Deteksi working directory otomatis (jika dijalankan dari dalam repo)
if [ -f "main.py" ]; then
    APEX_DIR="$(pwd)"
fi

echo "APEX Cron Installer"
echo "==================="
echo "Dir : $APEX_DIR"
echo "Py  : $PYTHON"
echo ""

# Buat logs dir kalau belum ada
mkdir -p "$APEX_DIR/logs"

# Buat crontab baru (hapus entry lama kalau ada, tambah yang baru)
TEMP_CRON=$(mktemp)
crontab -l 2>/dev/null | grep -v "CryptoAgent\|crypto_bot\|apex" > "$TEMP_CRON" || true

cat >> "$TEMP_CRON" << EOF

# === APEX Crypto Bot ===
# Setiap 4 jam jam 00: collect data Phase 8 (liquidations, on-chain, social)
0 */4 * * * cd $APEX_DIR && $PYTHON main.py --collect-phase8 >> $APEX_DIR/logs/cron.log 2>&1

# Setiap 4 jam jam 15: collect on-chain data
15 */4 * * * cd $APEX_DIR && $PYTHON main.py --collect-onchain >> $APEX_DIR/logs/cron.log 2>&1

# Setiap 4 jam jam 30: scan sinyal dan kirim update ke Telegram
30 */4 * * * cd $APEX_DIR && $PYTHON main.py --scan-once >> $APEX_DIR/logs/cron.log 2>&1

# Setiap 5 menit: poll command Telegram (/scan, /status, dll)
*/5 * * * * cd $APEX_DIR && $PYTHON main.py --poll-commands >> $APEX_DIR/logs/cron.log 2>&1
EOF

crontab "$TEMP_CRON"
rm "$TEMP_CRON"

echo "Cron berhasil diinstall! Crontab sekarang:"
echo ""
crontab -l
echo ""
echo "Test scan sekarang? Jalankan:"
echo "  python3 main.py --scan-once"
