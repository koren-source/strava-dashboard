import unittest
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from unittest.mock import patch

from api import recommend

from training_plan import (
    build_training_plan,
    compute_training_context,
    validate_plan,
)


FTP = 237
NOW = datetime(2026, 7, 29, 19, 11, tzinfo=timezone.utc)


def ride(
    ride_id,
    date,
    *,
    minutes=60,
    suffer=60,
    watts=150,
    elevation=1000,
):
    return {
        "id": ride_id,
        "name": f"Ride {ride_id}",
        "date": date,
        "moving_mins": minutes,
        "elev_ft": elevation,
        "avg_watts": watts,
        "normalized_watts": None,
        "suffer_score": suffer,
    }


class TrainingPlanTests(unittest.TestCase):
    def test_live_recommendation_api_uses_same_home_day_as_planner(self):
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 16, 3, 18, tzinfo=timezone.utc).astimezone(tz)

        with patch.object(recommend, "datetime", FrozenDateTime):
            context = recommend.compute_context([ride(1, "2026-11-15")], {"ftp": FTP})
        self.assertEqual(context["days_since_last_ride"], 0)

    def test_browser_dates_match_home_calendar_in_a_different_system_timezone(self):
        page = Path(__file__).resolve().parents[1] / "index.html"
        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(process.argv[1], 'utf8');
const source = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const context = vm.createContext({fetch: () => new Promise(() => {})});
vm.runInContext(source, context);
const cases = [
  [{date:'2026-11-15'}, '2026-11-16T03:18:00Z'],
  [{start_date_local:'2026-11-15T16:11:59Z'}, '2026-11-16T03:18:00Z'],
  [{date:'2026-11-15'}, '2026-11-16T08:00:00Z'],
  [{date:'2026-03-08'}, '2026-03-09T06:59:59Z'],
  [{date:'2026-03-08'}, '2026-03-09T07:00:00Z'],
  [{date:'2026-11-01'}, '2026-11-02T07:59:59Z'],
  [{date:'2026-11-01'}, '2026-11-02T08:00:00Z'],
  [{date:'2026-12-31'}, '2027-01-01T07:59:59Z'],
  [{date:'2026-12-31'}, '2027-01-01T08:00:00Z'],
  [{date:'2026-11-17'}, '2026-11-16T03:18:00Z'],
  [{}, '2026-11-16T03:18:00Z'],
  [{date:'invalid'}, '2026-11-16T03:18:00Z'],
  [{date:'2026-02-30'}, '2026-11-16T03:18:00Z'],
];
console.log(JSON.stringify(cases.map(([ride, now]) => context.daysSinceRide(ride, new Date(now)))));
"""
        import os
        completed = subprocess.run(
            ["node", "-e", script, str(page)], check=True, text=True,
            capture_output=True, env={**os.environ, "TZ": "Asia/Tokyo"},
        )
        self.assertEqual(json.loads(completed.stdout), [0, 0, 1, 0, 1, 0, 1, 0, 1, 0, None, None, None])

    def test_evening_ride_uses_las_vegas_day_after_utc_midnight(self):
        context = compute_training_context(
            [ride(1, "2026-11-15", suffer=27)],
            {"ftp": FTP},
            now=datetime(2026, 11, 16, 3, 18, tzinfo=timezone.utc),
        )
        self.assertEqual(context["days_since_last_ride"], 0)

    def test_local_midnight_dst_and_year_boundaries_count_calendar_days(self):
        cases = [
            ("2026-11-15", "2026-11-16T07:59:59+00:00", 0),
            ("2026-11-15", "2026-11-16T08:00:00+00:00", 1),
            ("2026-03-08", "2026-03-09T06:59:59+00:00", 0),
            ("2026-03-08", "2026-03-09T07:00:00+00:00", 1),
            ("2026-11-01", "2026-11-02T07:59:59+00:00", 0),
            ("2026-11-01", "2026-11-02T08:00:00+00:00", 1),
            ("2026-12-31", "2027-01-01T07:59:59+00:00", 0),
            ("2026-12-31", "2027-01-01T08:00:00+00:00", 1),
        ]
        for date, instant, expected in cases:
            with self.subTest(date=date, instant=instant):
                context = compute_training_context(
                    [ride(1, date)], {"ftp": FTP},
                    now=datetime.fromisoformat(instant),
                )
                self.assertEqual(context["days_since_last_ride"], expected)

    def test_local_timestamp_label_and_future_date_do_not_add_recovery_days(self):
        current = datetime(2026, 11, 16, 3, 18, tzinfo=timezone.utc)
        for value in [
            {"start_date_local": "2026-11-15T16:11:59Z"},
            {"date": "2026-11-17"},
        ]:
            with self.subTest(value=value):
                context = compute_training_context([value], {"ftp": FTP}, now=current)
                self.assertEqual(context["days_since_last_ride"], 0)

    def test_invalid_or_missing_ride_dates_do_not_generate_a_plan(self):
        for value in [{}, {"date": "bad-date"}, {"date": "2026-02-30"}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_training_plan([value], {"ftp": FTP}, now=NOW)

    def test_seven_day_load_window_uses_home_calendar_at_utc_midnight(self):
        context = compute_training_context(
            [ride(1, "2026-11-15", suffer=27), ride(2, "2026-11-09", suffer=70)],
            {"ftp": FTP},
            now=datetime(2026, 11, 16, 3, 18, tzinfo=timezone.utc),
        )
        self.assertEqual(context["recent_7d_load"], 97)
        self.assertEqual(context["prior_7d_load"], 0)

    def test_current_training_history_selects_controlled_quality(self):
        rides = [
            ride(1, "2026-07-25", minutes=131, suffer=164, elevation=3310),
            ride(2, "2026-07-23", minutes=64, suffer=75, elevation=1385),
            ride(3, "2026-07-18", minutes=239, suffer=223, elevation=5407),
        ]

        plan = build_training_plan(rides, {"ftp": FTP}, now=NOW)

        self.assertEqual(plan["workout_name"], "Sustained Climbing Power")
        self.assertEqual(plan["duration_minutes"], 70)
        self.assertEqual(
            sum(item["duration_minutes"] for item in plan["suggested_sets"]),
            70,
        )
        self.assertIn("2 × 20 min", plan["suggested_sets"][1]["description"])
        self.assertNotIn("5-to-20", plan["reasoning"])
        self.assertNotIn("lactate clearance", plan["reasoning"])
        self.assertEqual(
            plan["plan_basis"]["signals"][3]["value"],
            "Stable",
        )

    def test_high_load_ride_yesterday_selects_recovery(self):
        rides = [
            ride(1, "2026-07-28", minutes=150, suffer=175),
            ride(2, "2026-07-25", minutes=80, suffer=70),
        ]

        plan = build_training_plan(rides, {"ftp": FTP}, now=NOW)

        self.assertEqual(plan["workout_name"], "Post-Ride Recovery Spin")
        self.assertEqual(plan["target_power"], {"low": 118, "high": 154})
        validate_plan(plan)

    def test_moderate_load_ride_yesterday_does_not_stack_quality(self):
        rides = [
            ride(1, "2026-07-28", minutes=59, suffer=89, watts=175),
            ride(2, "2026-07-25", minutes=131, suffer=164),
        ]

        plan = build_training_plan(rides, {"ftp": FTP}, now=NOW)

        self.assertEqual(plan["workout_name"], "Post-Ride Recovery Spin")
        self.assertIn("moderate load", plan["plan_basis"]["decision"])
        validate_plan(plan)

    def test_easy_spin_does_not_hide_yesterdays_moderate_load(self):
        rides = [
            ride(1, "2026-07-29", minutes=71, suffer=14, watts=111),
            ride(2, "2026-07-28", minutes=59, suffer=89, watts=175),
            ride(3, "2026-07-25", minutes=131, suffer=164),
        ]

        plan = build_training_plan(rides, {"ftp": FTP}, now=NOW)

        self.assertEqual(plan["workout_name"], "Post-Ride Recovery Spin")
        self.assertIn(
            "does not reset the recovery clock",
            plan["plan_basis"]["decision"],
        )
        validate_plan(plan)

    def test_week_off_selects_aerobic_return(self):
        rides = [ride(1, "2026-07-20", minutes=75, suffer=80)]

        plan = build_training_plan(rides, {"ftp": FTP}, now=NOW)

        self.assertEqual(plan["workout_name"], "Aerobic Return Ride")
        self.assertEqual(plan["duration_minutes"], 60)
        validate_plan(plan)

    def test_load_trend_uses_calendar_windows(self):
        rides = [
            ride(1, "2026-07-28", suffer=60),
            ride(2, "2026-07-24", suffer=50),
            ride(3, "2026-07-21", suffer=100),
            ride(4, "2026-07-17", suffer=40),
        ]

        context = compute_training_context(rides, {"ftp": FTP}, now=NOW)

        self.assertEqual(context["recent_7d_load"], 110)
        self.assertEqual(context["prior_7d_load"], 140)
        self.assertEqual(context["load_trend"], "falling")


if __name__ == "__main__":
    unittest.main()
