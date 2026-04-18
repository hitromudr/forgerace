#!/bin/bash
# Watchdog: restarts dead championship team processes every 60s
# Usage: ./scripts/watchdog.sh &

TEAMS="ensemble rework preflight distributed benchmark testrunner dashboard"
PREFIX="ch3"
LOG_DIR=".agents/logs"

while true; do
    for T in $TEAMS; do
        FULL="${PREFIX}-${T}"
        RUNNING=$(ps aux | grep "$FULL" | grep -v grep | wc -l)
        if [ "$RUNNING" -eq 0 ]; then
            HAS_WORK=$(python3 -c "
from forgerace.tasks import parse_tasks
t = [x for x in parse_tasks() if '${FULL}' in (x.discussion or '') and x.status != 'done']
print(len(t))
" 2>/dev/null)
            if [ "$HAS_WORK" -gt 0 ] 2>/dev/null; then
                echo "$(date +%H:%M:%S) watchdog: restarting $FULL ($HAS_WORK tasks remaining)"
                python3 -c "
from forgerace.tasks import parse_tasks, update_task_status
for t in parse_tasks():
    if '${FULL}' in (t.discussion or '') and t.status.startswith('in_progress'):
        update_task_status(t.id, 'open')
" 2>/dev/null
                python3 forgerace.py run --auto --team "$FULL" >> "$LOG_DIR/${FULL}.log" 2>&1 &
            fi
        fi
    done
    sleep 60
done
