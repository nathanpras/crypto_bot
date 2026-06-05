#!/bin/bash
# APEX cron schedule for Oracle Cloud (Ubuntu)
# Run: chmod +x cron.sh && crontab -e, then add entries below
# Working directory: /home/ubuntu/CryptoAgent
# Log: /home/ubuntu/CryptoAgent/logs/cron.log

APEX_DIR="/home/ubuntu/CryptoAgent"
PYTHON="/usr/bin/python3"
LOG="$APEX_DIR/logs/cron.log"

# Ensure log directory exists
mkdir -p "$APEX_DIR/logs"

# ── Cron entries (add these to crontab -e) ──────────────────────
# Format: minute hour day month weekday command

# Every 4 hours: collect Phase 8 data (liquidations, on-chain, social, funding)
# 0 */4 * * * cd /home/ubuntu/CryptoAgent && /usr/bin/python3 main.py --collect-phase8 >> /home/ubuntu/CryptoAgent/logs/cron.log 2>&1

# Every 6 hours: full data collection (Phase 6 + Phase 8)
# 30 */6 * * * cd /home/ubuntu/CryptoAgent && /usr/bin/python3 main.py --collect >> /home/ubuntu/CryptoAgent/logs/cron.log 2>&1

# Daily at 2am UTC: optimize weights for all regimes (long-running, ~5 min)
# 0 2 * * * cd /home/ubuntu/CryptoAgent && /usr/bin/python3 main.py --optimize-weights-all >> /home/ubuntu/CryptoAgent/logs/cron.log 2>&1

# Every 4 hours: run signal scan and send Telegram alerts
# 15 */4 * * * cd /home/ubuntu/CryptoAgent && /usr/bin/python3 main.py --scan-once >> /home/ubuntu/CryptoAgent/logs/cron.log 2>&1

echo "APEX cron schedule reference. Copy the commented lines above into: crontab -e"
echo "Current crontab entries:"
crontab -l 2>/dev/null || echo "(no existing crontab)"
