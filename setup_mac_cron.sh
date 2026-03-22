#!/bin/bash
# Schedules fetch_and_push.py to run at 9:15 AM and 3:20 PM IST on weekdays
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON=$(which python3)
LOG="$SCRIPT_DIR/fetch.log"
C1="15 9 * * 1-5 cd $SCRIPT_DIR && $PYTHON fetch_and_push.py >> $LOG 2>&1"
C2="20 15 * * 1-5 cd $SCRIPT_DIR && $PYTHON fetch_and_push.py >> $LOG 2>&1"
(crontab -l 2>/dev/null | grep -v "fetch_and_push"; echo "$C1"; echo "$C2") | crontab -
echo "Cron jobs added. Mac will fetch NSE data at 9:15 AM and 3:20 PM IST on weekdays."
echo "Log: $LOG"
