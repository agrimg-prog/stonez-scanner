#!/bin/bash
# setup_cron.sh — Adds Stonez scans to your Mac's crontab
# Runs at 9:20 AM and 3:25 PM IST on weekdays
# Your Mac must be awake at those times for this to work.
# For always-on automation, use GitHub Actions instead (see README).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON=$(which python3)
LOG_FILE="$SCRIPT_DIR/stonez.log"

CRON_MORNING="20 9 * * 1-5 cd $SCRIPT_DIR && $PYTHON run_scan.py >> $LOG_FILE 2>&1"
CRON_EVENING="25 15 * * 1-5 cd $SCRIPT_DIR && $PYTHON run_scan.py >> $LOG_FILE 2>&1"

echo "Adding Stonez cron jobs..."
echo "  Morning scan: 9:20 AM IST (weekdays)"
echo "  Evening scan: 3:25 PM IST (weekdays)"
echo "  Log file: $LOG_FILE"
echo ""

# Add to crontab (avoids duplicates)
(crontab -l 2>/dev/null | grep -v "stonez_v2"; echo "$CRON_MORNING"; echo "$CRON_EVENING") | crontab -

echo "✅ Cron jobs added."
echo ""
echo "To verify:"
echo "  crontab -l"
echo ""
echo "To remove:"
echo "  crontab -l | grep -v stonez_v2 | crontab -"
echo ""
echo "NOTE: Your MacBook must be awake at scan time for cron to fire."
echo "For cloud automation (Mac can be off), use GitHub Actions — see README."
