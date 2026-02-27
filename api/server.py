#!/opt/homebrew/bin/python3
"""
Strava Dashboard — Local recommendation API server
Runs on Mac mini, exposed via Cloudflare Tunnel to the static dashboard.
Rate limited to 10 calls/week per the OpenAI key policy.
"""
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, jsonify, request
from flask_cors import CORS
from openai import OpenAI

app = Flask(__name__)
CORS(app)  # allow GitHub Pages origin

DATA_DIR = Path(__file__).parent.parent / "data"
RATE_FILE = Path(__file__).parent / "rate_limit.json"
MAX_CALLS_PER_WEEK = 10

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")


# ── Rate Limiter ───────────────────────────────────────────────────────────────

def load_rate_state():
    if RATE_FILE.exists():
        return json.loads(RATE_FILE.read_text())
    return {"week": None, "calls": 0}


def check_rate_limit():
    state = load_rate_state()
    now = datetime.now(timezone.utc)
    current_week = now.strftime("%Y-W%W")

    if state.get("week") != current_week:
        # New week — reset
        state = {"week": current_week, "calls": 0}

    if state["calls"] >= MAX_CALLS_PER_WEEK:
        remaining_days = 7 - now.weekday()
        return False, f"Weekly limit reached ({MAX_CALLS_PER_WEEK} calls/week). Resets in ~{remaining_days} days.", state

    return True, None, state


def increment_rate(state):
    state["calls"] += 1
    RATE_FILE.write_text(json.dumps(state, indent=2))


# ── Data Loading ───────────────────────────────────────────────────────────────

def load_data():
    rides_path = DATA_DIR / "rides.json"
    athlete_path = DATA_DIR / "athlete.json"

    rides = json.loads(rides_path.read_text()) if rides_path.exists() else []
    athlete = json.loads(athlete_path.read_text()) if athlete_path.exists() else {}
    return rides, athlete


def compute_context(rides, athlete):
    ftp = athlete.get("ftp", 237)
    if not rides:
        return {"days_since_last_ride": 99, "trend": "unknown", "recent_tss_avg": 0}

    last = rides[0]
    last_date = datetime.fromisoformat(last["date"]).replace(tzinfo=timezone.utc)
    days_since = (datetime.now(timezone.utc) - last_date).days

    tss_values = []
    for r in rides[:7]:
        w = r.get("avg_watts")
        mins = r.get("moving_mins", 0)
        if w and mins:
            tss_est = (mins * 60 * (w ** 2)) / ((ftp ** 2) * 3600) * 100
            tss_values.append(round(tss_est))

    avg_tss = round(sum(tss_values) / len(tss_values)) if tss_values else 0

    if len(tss_values) >= 3:
        recent = sum(tss_values[:2]) / 2
        older = tss_values[2]
        trend = "building" if recent > older * 1.1 else ("recovering" if recent < older * 0.9 else "maintaining")
    else:
        trend = "unknown"

    return {
        "days_since_last_ride": days_since,
        "trend": trend,
        "recent_tss_avg": avg_tss,
        "estimated_tss_values": tss_values[:5],
    }


# ── GPT-4o Recommendation ──────────────────────────────────────────────────────

def generate_recommendations(rides, athlete, context):
    client = OpenAI(api_key=OPENAI_API_KEY)
    ftp = athlete.get("ftp", 237)
    last = rides[0]
    intensity_pct = round((last.get("avg_watts", 0) / ftp) * 100) if last.get("avg_watts") else 0

    prompt = f"""You are an elite cycling coach. Generate exactly TWO workout recommendations for this rider.

ATHLETE PROFILE:
- FTP: {ftp}W
- Weight: {athlete.get('weight_kg', 80)}kg
- VO2 Max (est): {athlete.get('vo2max', 48)}
- Max HR: {athlete.get('max_hr', 194)} bpm
- Resting HR: {athlete.get('resting_hr', 46)} bpm
- Goals: {json.dumps(athlete.get('goals', []))}

LAST RIDE:
- Name: {last.get('name')}
- Date: {last.get('date')}
- Duration: {last.get('moving_mins')} min
- Avg Power: {last.get('avg_watts')}W ({intensity_pct}% FTP)
- Max Power: {last.get('max_watts')}W
- Avg HR: {last.get('avg_hr')} bpm
- Suffer Score: {last.get('suffer_score')}
- Distance: {last.get('dist_mi')} mi

TRAINING CONTEXT:
- Days since last ride: {context['days_since_last_ride']}
- 7-day avg TSS: {context['recent_tss_avg']}
- Training trend: {context['trend']}

Generate TWO recommendations:
1. GROWTH RIDE — the hard/progressive session to build FTP or VO2 Max
2. STABILIZER RIDE — Zone 2 endurance to maintain base without fatigue

IMPORTANT: If days_since_last_ride >= 5, note this prominently in both reasoning fields and adjust intensity accordingly.

Return ONLY valid JSON (no markdown) in this exact format:
{{
  "growth": {{
    "workout_name": "Name",
    "reasoning": "2-3 sentences with <strong> tags for emphasis. Reference days since last ride and training trend.",
    "focus": "Build FTP",
    "duration_minutes": 65,
    "target_power": {{"low": 210, "high": 240}},
    "hr_zone": "Zone 4",
    "suggested_sets": [
      {{"name": "Warmup", "duration_minutes": 10, "power_pct_ftp": [50, 70], "description": "Easy spin"}},
      {{"name": "Main Set", "duration_minutes": 40, "power_pct_ftp": [88, 95], "description": "3x10 min intervals"}},
      {{"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [40, 55], "description": "Easy spin"}}
    ]
  }},
  "stabilizer": {{
    "workout_name": "Name",
    "reasoning": "1-2 sentences explaining why Zone 2 now.",
    "focus": "Maintain aerobic base",
    "duration_minutes": 60,
    "target_power": {{"low": 154, "high": 178}},
    "hr_zone": "Zone 2",
    "suggested_sets": [
      {{"name": "Warmup", "duration_minutes": 10, "power_pct_ftp": [50, 65], "description": "Easy spin"}},
      {{"name": "Steady State", "duration_minutes": 40, "power_pct_ftp": [65, 75], "description": "Steady endurance"}},
      {{"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [50, 60], "description": "Easy spin"}}
    ]
  }}
}}"""

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.choices[0].message.content.strip()
    return json.loads(text)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok", "ts": datetime.now(timezone.utc).isoformat()})


@app.route("/recommend", methods=["GET", "POST"])
def recommend():
    allowed, error_msg, rate_state = check_rate_limit()
    if not allowed:
        return jsonify({"error": error_msg, "rate_limited": True}), 429

    if not OPENAI_API_KEY:
        return jsonify({"error": "OpenAI key not configured"}), 500

    try:
        rides, athlete = load_data()
        if not rides:
            return jsonify({"error": "No ride data found"}), 404

        context = compute_context(rides, athlete)
        recs = generate_recommendations(rides, athlete, context)

        increment_rate(rate_state)

        remaining = MAX_CALLS_PER_WEEK - rate_state["calls"] - 1
        return jsonify({
            "success": True,
            "growth": recs["growth"],
            "stabilizer": recs["stabilizer"],
            "context": context,
            "rate_remaining": remaining,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/rate-status")
def rate_status():
    state = load_rate_state()
    now = datetime.now(timezone.utc)
    current_week = now.strftime("%Y-W%W")
    calls = state["calls"] if state.get("week") == current_week else 0
    return jsonify({
        "calls_this_week": calls,
        "limit": MAX_CALLS_PER_WEEK,
        "remaining": MAX_CALLS_PER_WEEK - calls,
    })


if __name__ == "__main__":
    print(f"🚴 Strava Rec API starting on port 7842")
    print(f"   Data dir: {DATA_DIR}")
    print(f"   Rate limit: {MAX_CALLS_PER_WEEK} calls/week")
    print(f"   OpenAI key: {'set ✓' if OPENAI_API_KEY else 'MISSING ✗'}")
    app.run(host="127.0.0.1", port=7842, debug=False)
