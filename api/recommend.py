"""
Vercel serverless function — Strava dashboard ride recommender.
POST /api/recommend → returns two workout recommendations via GPT-4o.
Rate limited to 10 calls/week using Vercel KV (or simple in-memory fallback).
"""
from http.server import BaseHTTPRequestHandler
import json
import os
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
MAX_CALLS_PER_WEEK = 30

# In-memory rate limit (Vercel is stateless — upgrade to KV if needed)
_rate_store: dict = {}


def get_week_key():
    return datetime.now(timezone.utc).strftime("%Y-W%W")


def check_rate():
    week = get_week_key()
    calls = _rate_store.get(week, 0)
    return calls < MAX_CALLS_PER_WEEK, MAX_CALLS_PER_WEEK - calls


def increment_rate():
    week = get_week_key()
    _rate_store[week] = _rate_store.get(week, 0) + 1


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
            tss_values.append(round((mins * 60 * (w ** 2)) / ((ftp ** 2) * 3600) * 100))
    avg_tss = round(sum(tss_values) / len(tss_values)) if tss_values else 0
    if len(tss_values) >= 3:
        recent = sum(tss_values[:2]) / 2
        trend = "building" if recent > tss_values[2] * 1.1 else ("recovering" if recent < tss_values[2] * 0.9 else "maintaining")
    else:
        trend = "unknown"
    return {"days_since_last_ride": days_since, "trend": trend, "recent_tss_avg": avg_tss}


def generate_recs(rides, athlete, context):
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    ftp = athlete.get("ftp", 237)
    last = rides[0]
    intensity_pct = round((last.get("avg_watts", 0) / ftp) * 100) if last.get("avg_watts") else 0

    prompt = f"""You are an elite cycling coach. Generate exactly TWO workout recommendations.

ATHLETE: FTP {ftp}W | VO2Max {athlete.get('vo2max', 48)} | MaxHR {athlete.get('max_hr', 194)}bpm | Goals: {athlete.get('goals', [])}
LAST RIDE: {last.get('date')} | {last.get('moving_mins')}min | {last.get('avg_watts')}W ({intensity_pct}% FTP) | Suffer: {last.get('suffer_score')}
CONTEXT: {context['days_since_last_ride']} days since last ride | TSS avg: {context['recent_tss_avg']} | Trend: {context['trend']}

CRITICAL: If days_since_last_ride >= 5, prominently note this and adjust intensity lower. If >= 3 days, mention freshness.

Return ONLY valid JSON:
{{
  "growth": {{
    "workout_name": "Name",
    "reasoning": "2-3 sentences with <strong> tags. Reference days since last ride and trend.",
    "focus": "Build FTP",
    "duration_minutes": 65,
    "target_power": {{"low": 210, "high": 240}},
    "hr_zone": "Zone 4",
    "suggested_sets": [
      {{"name": "Warmup", "duration_minutes": 10, "power_pct_ftp": [50, 70], "description": "Easy spin building to tempo"}},
      {{"name": "Main Set", "duration_minutes": 40, "power_pct_ftp": [88, 95], "description": "3x10 min @ threshold, 5 min recovery"}},
      {{"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [40, 55], "description": "Easy spin"}}
    ]
  }},
  "stabilizer": {{
    "workout_name": "Name",
    "reasoning": "1-2 sentences on why Zone 2 now.",
    "focus": "Maintain aerobic base",
    "duration_minutes": 60,
    "target_power": {{"low": {round(ftp*0.65)}, "high": {round(ftp*0.75)}}},
    "hr_zone": "Zone 2",
    "suggested_sets": [
      {{"name": "Warmup", "duration_minutes": 10, "power_pct_ftp": [50, 65], "description": "Easy spin"}},
      {{"name": "Steady State", "duration_minutes": 40, "power_pct_ftp": [65, 75], "description": "Steady endurance @ {round(ftp*0.65)}-{round(ftp*0.75)}W"}},
      {{"name": "Cooldown", "duration_minutes": 10, "power_pct_ftp": [50, 60], "description": "Easy spin"}}
    ]
  }}
}}"""

    resp = client.chat.completions.create(
        model="gpt-4o", max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )
    return json.loads(resp.choices[0].message.content.strip())


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _respond(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _handle(self):
        allowed, remaining = check_rate()
        if not allowed:
            self._respond(429, {"error": f"Weekly limit of {MAX_CALLS_PER_WEEK} reached. Resets Monday.", "rate_limited": True})
            return

        try:
            rides_path = DATA_DIR / "rides.json"
            athlete_path = DATA_DIR / "athlete.json"
            if not rides_path.exists():
                self._respond(404, {"error": "No ride data"})
                return

            rides = json.loads(rides_path.read_text())
            athlete = json.loads(athlete_path.read_text()) if athlete_path.exists() else {}
            context = compute_context(rides, athlete)
            recs = generate_recs(rides, athlete, context)
            increment_rate()

            self._respond(200, {
                "success": True,
                "growth": recs["growth"],
                "stabilizer": recs["stabilizer"],
                "context": context,
                "rate_remaining": remaining - 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            self._respond(500, {"error": str(e)})

    def log_message(self, *args):
        pass  # suppress access logs
