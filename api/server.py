#!/opt/homebrew/bin/python3
"""
Strava Dashboard — Local recommendation API server
Runs on Mac mini, exposed via Cloudflare Tunnel to the static dashboard.
Rate limited to 30 calls/week.
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
CORS(app, origins=["https://koren-source.github.io"])  # restrict to dashboard only

DATA_DIR = Path(__file__).parent.parent / "data"
RATE_FILE = Path(__file__).parent / "rate_limit.json"
MAX_CALLS_PER_WEEK = 30

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

    weight_kg = athlete.get('weight_kg', 81.6)
    target_ftp = athlete.get('target_ftp', 260)
    watts_per_kg = round(ftp / weight_kg, 2)
    target_wpk = round(target_ftp / weight_kg, 2)
    pc = athlete.get('power_curve', {})

    # Coggan zones at current FTP
    z2_lo, z2_hi = round(ftp * 0.56), round(ftp * 0.75)
    ss_lo, ss_hi = round(ftp * 0.88), round(ftp * 0.94)
    thresh_lo, thresh_hi = round(ftp * 0.95), round(ftp * 1.05)
    vo2_lo, vo2_hi = round(ftp * 1.06), round(ftp * 1.20)
    ou_under_lo, ou_under_hi = round(ftp * 0.85), round(ftp * 0.92)
    ou_over_lo, ou_over_hi = round(ftp * 1.02), round(ftp * 1.08)
    days_off = context['days_since_last_ride']
    trend = context['trend']

    prompt = f"""You are a world-class cycling coach who combines elite periodization science with deep rider knowledge. Your coaching philosophy: recovery equals training, aerobic base is the foundation of all climbing speed, and every hard session must directly attack the rider's specific physiological limiter.

## RIDER: Koren Saida — 26yo male, Utah

**Current metrics:**
- FTP: {ftp}W → target {target_ftp}W in 12-16 weeks
- Weight: {weight_kg}kg ({athlete.get('weight_lbs', 180)} lbs) → target ~78kg (172 lbs)
- Power-to-weight: {watts_per_kg} W/kg → target 3.33 W/kg
- VO2 Max (est): {athlete.get('vo2max', 48)} ml/kg/min | Max HR: {athlete.get('max_hr', 194)} bpm | Resting HR: {athlete.get('resting_hr', 46)} bpm
- Trainer: {athlete.get('trainer', 'Wahoo Kickr Core (ERG)')} | Zwift + Garmin | 4 days/week, {athlete.get('training_hours_per_week', '6-8')} hrs/wk

**Power curve:**
- 5s: {pc.get('5s_watts', 932)}W — excellent sprint/neuromuscular
- 1min: {pc.get('1min_watts', 368)}W — strong anaerobic capacity
- 5min: {pc.get('5min_watts', 270)}W — solid VO2max
- 20min: {pc.get('20min_watts', 245)}W — FTP anchor

**⚠️ PRIMARY LIMITER — THE 5-TO-20 GAP:**
The 25W drop from 5-min (270W) to 20-min (245W) = 9.3% decline. Well-trained cyclists show only 5-8%. This reveals poor lactate clearance, an underdeveloped aerobic base relative to anaerobic capacity, and low fatigue resistance. This is WHY Koren fades on sustained climbs. EVERY Growth Ride must directly attack this limiter.

The three workout types that fix this gap:
1. Over-under intervals (under: {ou_under_lo}-{ou_under_hi}W / over: {ou_over_lo}-{ou_over_hi}W) — train lactate production AND clearance simultaneously
2. Extended sweet spot ({ss_lo}-{ss_hi}W) + fatigue-resistant sweet spot (90min Zone 2 → sweet spot back-to-back)
3. Sustained threshold ({thresh_lo}-{thresh_hi}W) for 20-40 min continuous efforts

**Coggan zones at {ftp}W FTP:**
- Zone 1 (Recovery): <{round(ftp*0.55)}W
- Zone 2 (Endurance): {z2_lo}-{z2_hi}W — the aerobic base builder
- Zone 3 (Tempo): {round(ftp*0.76)}-{round(ftp*0.87)}W
- Zone 4 (Sweet Spot): {ss_lo}-{ss_hi}W — highest stimulus:recovery ratio
- Zone 5 (Threshold): {thresh_lo}-{thresh_hi}W
- Zone 6 (VO2max): {vo2_lo}-{vo2_hi}W
- Zone 7 (Neuromuscular): >{round(ftp*1.30)}W

**Target event: Alpine Loop (Midway, UT)**
- 40 miles, 4,200 ft total gain
- KEY SEGMENT: Sundance → Cascade Springs — 8.5 miles at 6% avg grade, starts at 5,300 ft, tops at 8,000 ft
- Altitude adjustment: at 8,000 ft, effective FTP drops ~8-10% for non-acclimatized rider (~{round(ftp*0.92)}W effective)
- Race-day target: 220-235W avg on main climb = 58-64 min
- Pacing phases: Miles 1-2 (Sundance, steepest) → 215-225W / RPE 6-7. Miles 3-6 (steady aspens) → 225-235W / RPE 7-7.5. Miles 7-8.5 (summit push) → 230-245W / RPE 8-8.5
- Regular training climb: Emigration Canyon, SLC (~3.2%, 1,200 ft — good for threshold work but not a close analog)

## LAST RIDE
- Name: {last.get('name')}
- Date: {last.get('date')} | Duration: {last.get('moving_mins')} min | Distance: {last.get('dist_mi')} mi
- Avg Power: {last.get('avg_watts')}W ({intensity_pct}% FTP, {"Zone 2" if intensity_pct < 76 else "Sweet Spot" if intensity_pct < 95 else "Threshold" if intensity_pct < 106 else "VO2max"}) | Normalized: {last.get('normalized_watts') or 'n/a'}W | Max: {last.get('max_watts')}W
- Avg HR: {last.get('avg_hr')} bpm | Max HR: {last.get('max_hr')} bpm | Suffer Score: {last.get('suffer_score')}

## TRAINING CONTEXT
- Days since last ride: {days_off} {"⚠️ — extended rest, start controlled" if days_off >= 5 else "— some freshness available" if days_off >= 3 else "— normal recovery"}
- 7-day avg TSS: {context['recent_tss_avg']}
- Training trend: {trend} {"→ BACK OFF, recovery needed" if trend == "overreaching" else "→ building well, keep loading" if trend == "building" else "→ consolidating gains" if trend == "maintaining" else ""}

## COACHING RULES (non-negotiable)
1. ALWAYS reference the 5-to-20 gap limiter in Growth Ride reasoning — this is what we're fixing
2. ALWAYS give specific watt targets, not just percentages
3. If days_off >= 5: reduce intensity 5-10%, start with controlled effort, explicitly mention the rest
4. If trend == "overreaching": Growth Ride becomes threshold-lite at 85-88% FTP ({round(ftp*0.85)}-{round(ftp*0.88)}W), not full intervals
5. Stabilizer is ALWAYS pure Zone 2 ({z2_lo}-{z2_hi}W). No excuses, no "just a little harder." Zone 2 is where mitochondrial biogenesis happens.
6. Growth Ride should rotate workout type based on context: over-unders if last ride was threshold+, sweet spot if last ride was Zone 2, VO2max if trend is building and days_off <= 2
7. Mention Alpine Loop or Emigration Canyon by name — make it real
8. Response must feel like a coach who knows Koren, not a generic AI recommendation
9. Add a "weekly_focus" field: one sentence on what this week's training priority is

## OUTPUT FORMAT
Return ONLY valid JSON (no markdown, no backticks):
{{
  "weekly_focus": "One sentence: e.g. 'This week: closing the lactate clearance gap with over-unders + Zone 2 base.'",
  "growth": {{
    "workout_name": "Specific name (e.g. Alpine Loop Over-Unders, Sundance Threshold Blocks)",
    "reasoning": "2-3 sentences. Use <strong> tags for key points. Reference the limiter, the last ride, days off, and Alpine Loop.",
    "focus": "Short label (e.g. Close the 5-to-20 gap, Build lactate clearance)",
    "duration_minutes": 75,
    "target_power": {{"low": {ss_lo}, "high": {thresh_hi}}},
    "hr_zone": "Zone 4",
    "suggested_sets": [
      {{"name": "Warmup", "duration_minutes": 15, "power_pct_ftp": [50, 75], "description": "Easy spin building to tempo, flush legs"}},
      {{"name": "Main Set", "duration_minutes": 45, "power_pct_ftp": [88, 105], "description": "Specific intervals — be precise about structure"}},
      {{"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [40, 55], "description": "Easy spin, let the adaptation begin"}}
    ]
  }},
  "stabilizer": {{
    "workout_name": "Specific name (e.g. Aerobic Base Builder, Zone 2 Engine Work)",
    "reasoning": "1-2 sentences. Explain WHY Zone 2 now — mitochondrial density, fat oxidation, recovery from hard work.",
    "focus": "Build aerobic engine for Alpine Loop",
    "duration_minutes": 90,
    "target_power": {{"low": {z2_lo}, "high": {z2_hi}}},
    "hr_zone": "Zone 2",
    "suggested_sets": [
      {{"name": "Warmup", "duration_minutes": 10, "power_pct_ftp": [50, 65], "description": "Easy spin"}},
      {{"name": "Steady Zone 2", "duration_minutes": 70, "power_pct_ftp": [56, 75], "description": "Steady {z2_lo}-{z2_hi}W — no spikes above Zone 2"}},
      {{"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [45, 55], "description": "Easy spin"}}
    ]
  }}
}}"""

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.choices[0].message.content.strip()
    # Strip markdown code fences if GPT-4o wraps the response
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0].strip()
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

        # increment_rate already bumped calls by 1, so this gives correct remaining
        remaining = MAX_CALLS_PER_WEEK - rate_state["calls"]
        return jsonify({
            "success": True,
            "growth": recs["growth"],
            "stabilizer": recs["stabilizer"],
            "weekly_focus": recs.get("weekly_focus", ""),
            "context": context,
            "rate_remaining": remaining,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        })

    except Exception as e:
        print(f"ERROR /recommend: {e}")
        return jsonify({"error": "Internal error generating recommendation. Check server logs."}), 500


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
