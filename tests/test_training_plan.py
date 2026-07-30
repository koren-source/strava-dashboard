import unittest
from datetime import datetime, timezone

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
