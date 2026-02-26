#!/usr/bin/env python3
"""
Fetch latest rides from Strava API and write to data/rides.json + data/athlete.json.
Runs in GitHub Actions with secrets, or locally with env vars.

Required env vars:
  STRAVA_CLIENT_ID
  STRAVA_CLIENT_SECRET
  STRAVA_REFRESH_TOKEN
"""
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def refresh_token():
    """Exchange refresh token for a fresh access token."""
    data = urllib.parse.urlencode({
        "client_id": os.environ["STRAVA_CLIENT_ID"],
        "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "grant_type": "refresh_token",
        "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"],
    }).encode()

    req = urllib.request.Request("https://www.strava.com/oauth/token", data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read())

    print(f"Token refreshed. Expires at {datetime.fromtimestamp(body['expires_at'])}")
    return body["access_token"]


def api_get(path, token, params=None):
    """GET from Strava API v3."""
    url = f"https://www.strava.com/api/v3{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def fetch_athlete(token):
    """Fetch athlete profile (FTP, weight)."""
    data = api_get("/athlete", token)
    return {
        "name": f"{data.get('firstname', '')} {data.get('lastname', '')}".strip(),
        "ftp": data.get("ftp") or 237,  # fallback to known value
        "weight_kg": data.get("weight") or 80,
        "athlete_id": data.get("id"),
    }


def fetch_athlete_stats(token, athlete_id):
    """Fetch YTD stats."""
    data = api_get(f"/athletes/{athlete_id}/stats", token)
    ytd = data.get("ytd_ride_totals", {})
    return {
        "ytd_miles": round(ytd.get("distance", 0) / 1609.34, 1),
        "ytd_rides": ytd.get("count", 0),
        "ytd_hours": round(ytd.get("moving_time", 0) / 3600, 1),
    }


def fetch_rides(token, count=10):
    """Fetch recent ride activities."""
    activities = api_get("/athlete/activities", token, {"per_page": count})
    rides = []
    for a in activities:
        if a.get("type") not in ("Ride", "VirtualRide"):
            continue
        rides.append({
            "id": a["id"],
            "name": a.get("name", "Ride"),
            "date": a.get("start_date_local", "")[:10],
            "start_date_local": a.get("start_date_local", ""),
            "type": a.get("type"),
            "dist_mi": round(a.get("distance", 0) / 1609.34, 1),
            "moving_mins": a.get("moving_time", 0) // 60,
            "elapsed_mins": a.get("elapsed_time", 0) // 60,
            "elev_ft": round(a.get("total_elevation_gain", 0) * 3.28084),
            "avg_speed_mph": round(a.get("average_speed", 0) * 2.23694, 1),
            "max_speed_mph": round(a.get("max_speed", 0) * 2.23694, 1),
            "avg_watts": a.get("average_watts"),
            "max_watts": a.get("max_watts"),
            "avg_hr": a.get("average_heartrate"),
            "max_hr": a.get("max_heartrate"),
            "suffer_score": a.get("suffer_score"),
            "calories": a.get("calories"),
            "strava_url": f"https://www.strava.com/activities/{a['id']}",
        })
    return rides


def main():
    print("Fetching Strava data...")
    token = refresh_token()

    athlete = fetch_athlete(token)
    print(f"Athlete: {athlete['name']} (FTP: {athlete['ftp']}W)")

    stats = fetch_athlete_stats(token, athlete["athlete_id"])
    print(f"YTD: {stats['ytd_miles']} mi, {stats['ytd_rides']} rides")

    rides = fetch_rides(token, count=10)
    print(f"Fetched {len(rides)} rides")

    # Merge athlete + stats + goals
    athlete_data = {
        **athlete,
        **stats,
        "vo2max": 48,          # Not available from API — manual entry
        "max_hr": 194,         # Manual
        "resting_hr": 46,      # Manual
        "goals": ["Increase FTP", "Build aerobic base", "Improve VO2 Max", "Get faster on Zwift"],
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "athlete.json").write_text(json.dumps(athlete_data, indent=2))
    (DATA_DIR / "rides.json").write_text(json.dumps(rides, indent=2))

    print(f"Wrote data/athlete.json and data/rides.json")


if __name__ == "__main__":
    main()
