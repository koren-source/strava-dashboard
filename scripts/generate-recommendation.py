#!/usr/bin/env python3
"""Build the next cycling session from recent Strava training load."""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
sys.path.insert(0, str(ROOT_DIR))

from training_plan import build_training_plan  # noqa: E402


def load_data():
    rides_path = DATA_DIR / "rides.json"
    athlete_path = DATA_DIR / "athlete.json"

    if not rides_path.exists() or not athlete_path.exists():
        print("Missing data files. Run fetch-strava.py first.")
        sys.exit(1)

    rides = json.loads(rides_path.read_text())
    athlete = json.loads(athlete_path.read_text())
    return rides, athlete


def main():
    print("Building data-backed cycling training plan...")
    rides, athlete = load_data()

    if not rides:
        print("No rides found. Skipping recommendation.")
        return

    plan = build_training_plan(rides, athlete)
    plan["source"] = "training-engine-v2"
    plan["generated_at"] = datetime.now(timezone.utc).isoformat()
    plan["based_on_ride"] = rides[0].get("id")

    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "recommendation.json").write_text(json.dumps(plan, indent=2))
    print(f"Training plan: {plan['workout_name']}")
    print("Wrote data/recommendation.json")


if __name__ == "__main__":
    main()
