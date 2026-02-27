#!/bin/bash
# Start the Strava recommendation API server + Cloudflare named tunnel
# Auto-started by launchd on boot

LOG_DIR="/Users/q/.openclaw/workspace/memory/strava-api-logs"
DASHBOARD_DIR="/Users/q/.openclaw/workspace/strava-dashboard"
mkdir -p "$LOG_DIR"

# Load API key
ENV_FILE="$DASHBOARD_DIR/api/.env"
if [ -f "$ENV_FILE" ]; then
  export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

# Kill any existing instances
pkill -f "strava-dashboard/api/server.py" 2>/dev/null
pkill -f "cloudflared tunnel run" 2>/dev/null
pkill -f "nokey@localhost.run" 2>/dev/null
sleep 1

# Start Flask server
/opt/homebrew/bin/python3 "$DASHBOARD_DIR/api/server.py" \
  >> "$LOG_DIR/server.log" 2>&1 &
echo "[$(date)] Server started (PID $!)" >> "$LOG_DIR/server.log"

sleep 3

# Start named Cloudflare tunnel (permanent URL: rides.whiteslateconsulting.com)
/opt/homebrew/bin/cloudflared tunnel run strava-rec-api \
  >> "$LOG_DIR/tunnel.log" 2>&1 &
echo "[$(date)] Cloudflare tunnel started" >> "$LOG_DIR/tunnel.log"

# Update api-config.json with permanent URL (only if changed)
PERMANENT_URL="https://rides.whiteslateconsulting.com"
CONFIG_FILE="$DASHBOARD_DIR/data/api-config.json"
CURRENT=$(cat "$CONFIG_FILE" 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get('url',''))" 2>/dev/null)
if [ "$CURRENT" != "$PERMANENT_URL" ]; then
  echo "{\"url\": \"$PERMANENT_URL\", \"ts\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\", \"permanent\": true}" > "$CONFIG_FILE"
  cd "$DASHBOARD_DIR"
  git add data/api-config.json
  git commit -m "chore: switch to permanent Cloudflare tunnel URL [auto]" 2>/dev/null
  git push origin main 2>/dev/null
  echo "[$(date)] api-config.json updated to permanent URL" >> "$LOG_DIR/tunnel.log"
fi

wait
