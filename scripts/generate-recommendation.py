#!/usr/bin/env python3
"""
Generate AI-powered workout recommendation using OpenAI GPT API.
Reads data/rides.json + data/athlete.json, writes data/recommendation.json.

Required env var: OPENAI_API_KEY
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Only import openai if we have the key — allows graceful fallback
HAS_API_KEY = bool(os.environ.get("OPENAI_API_KEY"))


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


def generate_with_gpt(rides, athlete, context):
    """Call OpenAI GPT API to generate workout recommendation."""
    from openai import OpenAI

    last = rides[0]
    ftp = athlete.get("ftp", 237)
    intensity_pct = round((last.get("avg_watts", 0) / ftp) * 100) if last.get("avg_watts") else 0

    pc = athlete.get("power_curve", {})
    ss_lo, ss_hi = round(ftp * 0.88), round(ftp * 0.94)
    z2_lo, z2_hi = round(ftp * 0.56), round(ftp * 0.75)
    thresh_lo, thresh_hi = round(ftp * 0.95), round(ftp * 1.05)
    ou_under_lo, ou_under_hi = round(ftp * 0.85), round(ftp * 0.92)
    ou_over_lo, ou_over_hi = round(ftp * 1.02), round(ftp * 1.08)
    days_off = context['days_since_last_ride']

    prompt = f"""You are a world-class cycling coach. Your athlete has one primary physiological limiter you must directly address in every Growth Ride recommendation.

## RIDER: Koren Saida, 26yo, Utah
- FTP: {ftp}W → target {athlete.get('target_ftp', 260)}W
- Weight: {athlete.get('weight_kg', 83)}kg | VO2 Max (est): {athlete.get('vo2max', 48)} | Max HR: {athlete.get('max_hr', 194)} bpm
- Trainer: Wahoo Kickr Core (ERG) | Zwift + Garmin | 4 days/week

## POWER CURVE
- 5s: {pc.get('5s_watts', 932)}W | 1min: {pc.get('1min_watts', 368)}W | 5min: {pc.get('5min_watts', 270)}W | 20min: {pc.get('20min_watts', 245)}W

## ⚠️ THE LIMITER (reference this every time)
25W drop from 5-min (270W) to 20-min (245W) = 9.3% decline vs 5-8% for trained cyclists.
This means: poor lactate clearance, underdeveloped aerobic base, low fatigue resistance.
Fix with: over-unders ({ou_under_lo}-{ou_under_hi}W / {ou_over_lo}-{ou_over_hi}W), extended sweet spot ({ss_lo}-{ss_hi}W), fatigue-resistant sweet spot (Zone 2 → sweet spot back-to-back).

## TARGET EVENT
Alpine Loop (Midway, UT): 40 miles, 4,200 ft. Key segment: Sundance → Cascade Springs, 8.5 miles @ 6% avg grade.
Race target: 220-235W avg on main climb = 58-64 min.
Regular training climb: Emigration Canyon, SLC.

## LAST RIDE
- {last.get('name')} | {last.get('date')} | {last.get('moving_mins')} min | {last.get('avg_watts')}W ({intensity_pct}% FTP) | HR: {last.get('avg_hr')} bpm | Suffer: {last.get('suffer_score')}

## CONTEXT
- Days since last ride: {days_off} {"⚠️ extended rest — reduce intensity 5-10%, start controlled" if days_off >= 5 else ""}
- 7-day avg TSS: {context['recent_tss_avg']} | Trend: {context['trend']}

## RULES
1. Prescribe ONE Growth Ride that attacks the lactate clearance limiter
2. Give specific watt targets (not just %)
3. Mention Alpine Loop or Emigration Canyon by name
4. If trend = overreaching: use 85-88% FTP ({round(ftp*0.85)}-{round(ftp*0.88)}W), not full intervals
5. Response must feel like a real coach talking to Koren

Return ONLY valid JSON (no markdown):
{{
  "workout_name": "Specific name (e.g. Alpine Loop Over-Unders)",
  "reasoning": "2-3 sentences with <strong> tags. Name the limiter, reference the last ride, connect to Alpine Loop.",
  "focus": "Short label",
  "duration_minutes": 75,
  "target_power": {{"low": {ss_lo}, "high": {thresh_hi}}},
  "hr_zone": "Zone 4",
  "suggested_sets": [
    {{"name": "Warmup", "duration_minutes": 15, "power_pct_ftp": [50, 75], "description": "Easy spin building to tempo"}},
    {{"name": "Main Set", "duration_minutes": 45, "power_pct_ftp": [88, 108], "description": "Specific structure with watt targets"}},
    {{"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [40, 55], "description": "Easy spin"}}
  ]
}}"""

    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.choices[0].message.content.strip()
    # Parse the JSON response
    return json.loads(text)


def generate_fallback(rides, athlete, context):
    """Rule-based fallback if AI is unavailable."""
    ftp = athlete.get("ftp", 237)
    last = rides[0] if rides else {}
    avg_watts = last.get("avg_watts", 0)
    intensity = avg_watts / ftp if ftp else 0
    days_since = context.get("days_since_last_ride", 0)

    # Build days-since prefix for reasoning
    if days_since >= 5:
        days_prefix = f"<strong>{days_since} days since your last ride</strong> — start with a controlled effort to reconnect with your legs before pushing hard. "
    elif days_since >= 3:
        days_prefix = f"{days_since} days off the bike means some freshness to work with. "
    else:
        days_prefix = ""

    if intensity < 0.75:
        lo, hi = round(ftp * 0.90), round(ftp * 0.95)
        return {
            "workout_name": "Threshold Intervals",
            "reasoning": days_prefix + f"Your last ride was aerobic base work ({round(intensity*100)}% FTP — Zone 2). <strong>To grow your FTP, you need time at threshold.</strong> This is the most direct path to getting stronger.",
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
            "reasoning": days_prefix + f"Your last ride hit sweet spot ({round(intensity*100)}% FTP). <strong>You're primed to spike VO2 Max now</strong> — short hard efforts will push your ceiling.",
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
            "reasoning": days_prefix + f"You pushed hard last ride ({round(intensity*100)}% FTP). <strong>Sweet spot now locks in those gains</strong> without over-reaching.",
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
            rec = generate_with_gpt(rides, athlete, context)
            rec["source"] = "ai"
            rec["model"] = "gpt-4o"
            print(f"AI recommendation: {rec['workout_name']}")
        except Exception as e:
            print(f"AI generation failed ({e}), using rule-based fallback.")
            rec = generate_fallback(rides, athlete, context)
            rec["source"] = "rule-based"
    else:
        print("No OPENAI_API_KEY set. Using rule-based fallback.")
        rec = generate_fallback(rides, athlete, context)
        rec["source"] = "rule-based"

    rec["generated_at"] = datetime.now(timezone.utc).isoformat()
    rec["based_on_ride"] = rides[0].get("id") if rides else None
    # Add weekly_focus if not already set by AI
    if not rec.get("weekly_focus"):
        days_off = context.get("days_since_last_ride", 0)
        trend = context.get("trend", "unknown")
        if trend == "building":
            rec["weekly_focus"] = "This week: progressive loading — keep attacking the lactate clearance gap."
        elif trend == "recovering" or days_off >= 5:
            rec["weekly_focus"] = "This week: controlled return — rebuild aerobic base before pushing threshold again."
        else:
            rec["weekly_focus"] = "This week: close the 5-to-20 gap with over-unders and Zone 2 base work."

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "recommendation.json").write_text(json.dumps(rec, indent=2))
    print(f"Wrote data/recommendation.json")


if __name__ == "__main__":
    main()
