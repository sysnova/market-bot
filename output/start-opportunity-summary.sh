#!/bin/bash
set -euo pipefail
pane=$(tmux split-window -d -v -P -F '#{pane_id}' -t marketbot:Opportunities 'cd /home/lgonz/Projects/market-bot && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/home/lgonz/Projects/market-bot .venv-linux/bin/python /mnt/c/Users/lgonz/Projects/market-bot/app/integration/opportunity_summary_monitor.py')
tmux select-pane -t "$pane" -T 'P/L RESUMEN OPPORTUNITIES'
tmux select-layout -t marketbot:Opportunities even-vertical
tmux select-window -t marketbot:Opportunities
printf '%s\n' "$pane"
