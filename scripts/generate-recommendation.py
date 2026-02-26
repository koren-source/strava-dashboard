#!/usr/bin/env python3
"""
Generate AI-powered workout recommendation using Claude API.
Reads data/rides.json + data/athlete.json, writes data/recommendation.json.

Required env var: ANTHROPIC_API_KEY
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Only import anthropic if we have the key — allows graceful fallback
HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))


def load_data():
    rides_path = DATA_DIR / "rides.json"
    athlete_path = DATA_DIR / "athlete.json"

    if not rides_path.exists() or not athlete_path.exists():
        print("Missing data files. Run fetch-strava.py first.")
        sys.exit(1)

    rides = json.loads(rides_path.read_text())
    athlete = json.loads(athlete_path.read_text())
    return rides, athlete


def compute_context(rides, athlete):
    """Derive training context from recent rides."""
    ftp = athlete.get("ftp", 237)
    if not rides:
        return {"days_since_last_ride": 99, "trend": "unknown", "recent_tss_avg": 0}

    last = rides[0]
    last_date = datetime.fromisoformat(last["date"]).replace(tzinfo=timezone.utc)
    days_since = (datetime.now(timezone.utc) - last_date).days

    # Estimate TSS from available data: (duration_sec * NP^2) / (FTP^2 * 3600) * 100
    # Simplified: use avg_watts as proxy for NP
    tss_values = []
    for r in rides[:7]:
        w = r.get("avg_watts")
        mins = r.get("moving_mins", 0)
        if w and mins:
            tss_est = (mins * 60 * (w ** 2)) / ((ftp ** 2) * 3600) * 100
            tss_values.append(round(tss_est))

    avg_tss = round(sum(tss_values) / len(tss_values)) if tss_values else 0

    # Determine trend from last 3 rides
    if len(tss_values) >= 3:
        recent = sum(tss_values[:2]) / 2
        older = tss_values[2]
        if recent > older * 1.1:
            trend = "building"
        elif recent < older * 0.9:
            trend = "recovering"
        else:
            trend = "maintaining"
    else:
        trend = "unknown"

    return {
        "days_since_last_ride": days_since,
        "trend": trend,
        "recent_tss_avg": avg_tss,
        "estimated_tss_values": tss_values[:5],
    }


def generate_with_claude(rides, athlete, context):
    """Call Claude API to generate workout recommendation."""
    import anthropic

    last = rides[0]
    ftp = athlete.get("ftp", 237)
    intensity_pct = round((last.get("avg_watts", 0) / ftp) * 100) if last.get("avg_watts") else 0

    prompt = f"""You are an elite cycling coach analyzing a rider's data to prescribe their next Growth Ride.

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
- Max HR: {last.get('max_hr')} bpm
- Suffer Score: {last.get('suffer_score')}
- Distance: {last.get('dist_mi')} mi
- Elevation: {last.get('elev_ft')} ft

TRAINING CONTEXT:
- Days since last ride: {context['days_since_last_ride']}
- 7-day average estimated TSS: {context['recent_tss_avg']}
- Training trend: {context['trend']}

INSTRUCTIONS:
Prescribe ONE Growth Ride. This is the hard/progressive session — not recovery.
Consider the athlete's fatigue, training trend, and goals.
The workout should push adaptation toward higher FTP and VO2 Max.

Return ONLY valid JSON (no markdown, no backticks) in this exact format:
{{
  "workout_name": "Name of workout",
  "reasoning": "2-3 sentences explaining why this workout right now, referencing their last ride and training state. Use <strong> tags for emphasis.",
  "focus": "Short focus label (e.g. Build FTP, Raise VO2 Max)",
  "duration_minutes": 65,
  "target_power": {{"low": 210, "high": 240}},
  "hr_zone": "Zone 4",
  "suggested_sets": [
    {{"name": "Warmup", "duration_minutes": 10, "power_pct_ftp": [50, 70], "description": "Easy spin building to tempo"}},
    {{"name": "Main Set", "duration_minutes": 40, "power_pct_ftp": [88, 95], "description": "3x10 min @ 88-95% FTP, 5 min recovery"}},
    {{"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [40, 55], "description": "Easy spin"}}
  ]
}}"""

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6-20250514",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Parse the JSON response
    return json.loads(text)


def generate_fallback(rides, athlete, context):
    """Rule-based fallback if AI is unavailable."""
    ftp = athlete.get("ftp", 237)
    last = rides[0] if rides else {}
    avg_watts = last.get("avg_watts", 0)
    intensity = avg_watts / ftp if ftp else 0

    if intensity < 0.75:
        lo, hi = round(ftp * 0.90), round(ftp * 0.95)
        return {
            "workout_name": "Threshold Intervals",
            "reasoning": f"Your last ride was aerobic base work ({round(intensity*100)}% FTP — Zone 2). <strong>To grow your FTP, you need time at threshold.</strong> This is the most direct path to getting stronger.",
            "focus": "Build FTP",
            "duration_minutes": 70,
            "target_power": {"low": lo, "high": hi},
            "hr_zone": "Zone 4",
            "suggested_sets": [
                {"name": "Warmup", "duration_minutes": 10, "power_pct_ftp": [50, 75], "description": "Easy spin to tempo"},
                {"name": "Main Set", "duration_minutes": 45, "power_pct_ftp": [90, 95], "description": f"3x10 min @ {lo}-{hi}W, 5 min easy recovery"},
                {"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [50, 60], "description": "Easy spin"},
            ],
        }
    elif intensity < 0.90:
        lo, hi = round(ftp * 1.06), round(ftp * 1.20)
        return {
            "workout_name": "VO2 Max Intervals",
            "reasoning": f"Your last ride hit sweet spot ({round(intensity*100)}% FTP). <strong>You're primed to spike VO2 Max now</strong> — short hard efforts will push your ceiling.",
            "focus": "Raise VO2 Max",
            "duration_minutes": 60,
            "target_power": {"low": lo, "high": hi},
            "hr_zone": "Zone 5",
            "suggested_sets": [
                {"name": "Warmup", "duration_minutes": 10, "power_pct_ftp": [50, 75], "description": "Easy spin to tempo"},
                {"name": "Main Set", "duration_minutes": 33, "power_pct_ftp": [106, 120], "description": f"5x3 min @ {lo}-{hi}W, 3 min easy recovery"},
                {"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [50, 60], "description": "Easy spin"},
            ],
        }
    else:
        lo, hi = round(ftp * 0.88), round(ftp * 0.93)
        return {
            "workout_name": "Sweet Spot",
            "reasoning": f"You pushed hard last ride ({round(intensity*100)}% FTP). <strong>Sweet spot now locks in those gains</strong> without over-reaching.",
            "focus": "Lock in gains",
            "duration_minutes": 75,
            "target_power": {"low": lo, "high": hi},
            "hr_zone": "Zone 3-4",
            "suggested_sets": [
                {"name": "Warmup", "duration_minutes": 10, "power_pct_ftp": [50, 75], "description": "Easy spin to tempo"},
                {"name": "Main Set", "duration_minutes": 45, "power_pct_ftp": [88, 93], "description": f"2x20 min @ {lo}-{hi}W, 5 min recovery"},
                {"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [50, 60], "description": "Easy spin"},
            ],
        }


def main():
    print("Generating AI workout recommendation...")
    rides, athlete = load_data()
    context = compute_context(rides, athlete)

    if not rides:
        print("No rides found. Skipping recommendation.")
        return

    if HAS_API_KEY:
        try:
            rec = generate_with_claude(rides, athlete, context)
            rec["source"] = "ai"
            rec["model"] = "claude-sonnet-4-6"
            print(f"AI recommendation: {rec['workout_name']}")
        except Exception as e:
            print(f"AI generation failed ({e}), using rule-based fallback.")
            rec = generate_fallback(rides, athlete, context)
            rec["source"] = "rule-based"
    else:
        print("No ANTHROPIC_API_KEY set. Using rule-based fallback.")
        rec = generate_fallback(rides, athlete, context)
        rec["source"] = "rule-based"

    rec["generated_at"] = datetime.now(timezone.utc).isoformat()
    rec["based_on_ride"] = rides[0].get("id") if rides else None

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "recommendation.json").write_text(json.dumps(rec, indent=2))
    print(f"Wrote data/recommendation.json")


if __name__ == "__main__":
    main()
