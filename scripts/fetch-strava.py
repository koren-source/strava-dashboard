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
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def refresh_token():
    """Exchange refresh token for a fresh access token.

    Note: Strava rotates refresh tokens — the response includes a new refresh_token.
    In GitHub Actions we can't persist the new token back to secrets, but Strava's
    rotation is graceful (old tokens remain valid for an extended period). If syncs
    start failing with 401, the STRAVA_REFRESH_TOKEN secret needs manual update.
    """
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
    """Fetch recent ride activities.

    The list endpoint usually includes HR and power data, but occasionally
    HR fields are null even when has_heartrate=True. In that case we fetch
    the individual activity detail as a fallback.
    """
    activities = api_get("/athlete/activities", token, {"per_page": count})
    rides = []
    for a in activities:
        if a.get("type") not in ("Ride", "VirtualRide"):
            continue

        # Fallback: if HR data missing from summary, fetch detailed activity
        if a.get("has_heartrate") and not a.get("average_heartrate"):
            try:
                detail = api_get(f"/activities/{a['id']}", token)
                a.update({k: detail[k] for k in ("average_heartrate", "max_heartrate", "calories") if k in detail})
            except Exception as e:
                print(f"Warning: could not fetch detail for activity {a['id']}: {e}")

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
            "normalized_watts": a.get("weighted_average_watts"),
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

    # Compute days since last ride
    days_since_last_ride = None
    if rides:
        try:
            last_date = datetime.fromisoformat(rides[0]["date"])
            days_since_last_ride = (datetime.now() - last_date).days
        except Exception:
            pass

    # Merge athlete + stats + goals
    athlete_data = {
        **athlete,
        **stats,
        "vo2max": 48,          # Not available from API — manual entry
        "max_hr": 194,         # Manual
        "resting_hr": 46,      # Manual
        "goals": ["Increase FTP", "Build aerobic base", "Improve VO2 Max", "Get faster on Zwift"],
        "days_since_last_ride": days_since_last_ride,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }

    # Compute weekly totals from rides
    from collections import defaultdict
    week_map = defaultdict(lambda: {"rides": 0, "miles": 0.0, "mins": 0})
    for r in rides:
        try:
            d = datetime.fromisoformat(r["date"])
            week_key = d.strftime("%Y-W%W")
            # Build label like "Feb 17-23"
            week_start = d - timedelta(days=d.weekday())
            week_end = week_start + timedelta(days=6)
            if week_start.month == week_end.month:
                label = f"{week_start.strftime('%b %-d')}-{week_end.strftime('%-d')}"
            else:
                label = f"{week_start.strftime('%b %-d')} – {week_end.strftime('%b %-d')}"
            wk = week_map[week_key]
            wk["week"] = week_key
            wk["label"] = label
            wk["rides"] += 1
            wk["miles"] = round(wk["miles"] + r.get("dist_mi", 0), 1)
            wk["mins"] += r.get("moving_mins", 0)
        except Exception:
            pass
    weekly = sorted(week_map.values(), key=lambda x: x["week"], reverse=True)
    for w in weekly:
        w["hours"] = round(w["mins"] / 60, 1)
        del w["mins"]
    weekly = weekly[:8]

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "athlete.json").write_text(json.dumps(athlete_data, indent=2))
    (DATA_DIR / "rides.json").write_text(json.dumps(rides, indent=2))
    (DATA_DIR / "weekly.json").write_text(json.dumps(weekly, indent=2))

    print(f"Wrote data/athlete.json, data/rides.json, data/weekly.json")
    if days_since_last_ride is not None:
        print(f"Days since last ride: {days_since_last_ride}")


if __name__ == "__main__":
    main()
