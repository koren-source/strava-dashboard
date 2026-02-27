#!/bin/bash
# Start the Strava recommendation API server + localhost.run tunnel
# Auto-started by launchd on boot

# Load key from local env file (never committed to git)
ENV_FILE="/Users/q/.openclaw/workspace/strava-dashboard/api/.env"
if [ -f "$ENV_FILE" ]; then
  export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

LOG_DIR="/Users/q/.openclaw/workspace/memory/strava-api-logs"
DASHBOARD_DIR="/Users/q/.openclaw/workspace/strava-dashboard"
mkdir -p "$LOG_DIR"

# Kill any existing instance
pkill -f "strava-dashboard/api/server.py" 2>/dev/null
pkill -f "nokey@localhost.run" 2>/dev/null
sleep 1

# Start Flask server
/opt/homebrew/bin/python3 "$DASHBOARD_DIR/api/server.py" \
  >> "$LOG_DIR/server.log" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$LOG_DIR/server.pid"
echo "[$(date)] Server started (PID $SERVER_PID)" >> "$LOG_DIR/server.log"

# Wait for server to be ready
sleep 3

# Start tunnel, capture URL
TUNNEL_LOG="$LOG_DIR/tunnel.log"
echo "[$(date)] Starting tunnel..." >> "$TUNNEL_LOG"

ssh -o StrictHostKeyChecking=no \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -R 80:localhost:7842 \
    nokey@localhost.run \
    2>&1 | tee -a "$TUNNEL_LOG" | while IFS= read -r line; do
      if echo "$line" | grep -q "lhr.life"; then
        TUNNEL_URL=$(echo "$line" | grep -oE 'https://[a-z0-9]+\.lhr\.life')
        if [ -n "$TUNNEL_URL" ]; then
          echo "[$(date)] Tunnel URL: $TUNNEL_URL" >> "$TUNNEL_LOG"
          # Write URL to dashboard data dir for JS to read
          echo "{\"url\": \"$TUNNEL_URL\", \"ts\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" \
            > "$DASHBOARD_DIR/data/api-config.json"
          # Commit and push so GitHub Pages picks it up
          cd "$DASHBOARD_DIR"
          git add data/api-config.json
          git commit -m "chore: update API tunnel URL [auto]" 2>/dev/null || true
          git push origin main 2>/dev/null || true
          echo "[$(date)] api-config.json pushed" >> "$TUNNEL_LOG"
        fi
      fi
    done
