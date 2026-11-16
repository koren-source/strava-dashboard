import importlib.util
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "scripts" / file)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


collector = module("strava_collector", "fetch-strava.py")
publisher = module("strava_publisher", "publish-history.py")


class HistoryCaptureTests(unittest.TestCase):
    def test_collector_publishes_same_day_evening_ride_as_zero_days_ago(self):
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 11, 16, 3, 18, tzinfo=timezone.utc).astimezone(tz)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "public" / "data"
            data.parent.mkdir()
            latest = {"id": 21003923505, "date": "2026-11-15", "dist_mi": 14.2, "moving_mins": 52}
            with patch.object(collector, "datetime", FrozenDateTime), \
                 patch.object(collector, "DATA_DIR", data), \
                 patch.object(collector, "refresh_token", return_value="synthetic"), \
                 patch.object(collector, "fetch_athlete", return_value={"name": "Synthetic", "ftp": 237, "athlete_id": 1}), \
                 patch.object(collector, "fetch_athlete_stats", return_value={"ytd_miles": 14.2, "ytd_rides": 1}), \
                 patch.object(collector, "fetch_rides", return_value=[latest]), \
                 patch.dict(os.environ, STRAVA_CAPTURE_PATH=str(root / "private.json")), \
                 patch("sys.stdout", new_callable=io.StringIO):
                collector.main()
            self.assertEqual(json.loads((data / "athlete.json").read_text())["days_since_last_ride"], 0)
            self.assertEqual(json.loads((root / "private.json").read_text())["athlete"]["days_since_last_ride"], 0)

    def activity(self, index, kind="Ride"):
        return {"id": index, "type": kind, "start_date_local": "2026-09-01T10:00:00Z", "distance": 1000, "moving_time": 600}

    def test_paginates_until_empty_even_when_page_is_short_and_filters_nonrides_after_scan(self):
        pages = [[self.activity(i) for i in range(1, 13)], [self.activity(13, "Run"), self.activity(14)], []]
        with patch.object(collector, "api_get", side_effect=pages) as api:
            rides = collector.fetch_rides("synthetic", all_history=True)
        self.assertEqual(len(rides), 13)
        self.assertEqual([call.args[2]["page"] for call in api.call_args_list], [1, 2, 3])
        self.assertEqual(len({call.args[2]["before"] for call in api.call_args_list}), 1)

    def test_late_page_failure_or_repeated_activity_aborts_complete_capture(self):
        for pages in ([ [self.activity(1)], RuntimeError("synthetic source outage") ], [ [self.activity(1)], [self.activity(1)] ]):
            with self.subTest(pages=len(pages)), patch.object(collector, "api_get", side_effect=pages):
                with self.assertRaises((ValueError, RuntimeError)):
                    collector.fetch_rides("synthetic", all_history=True)

    def test_lost_save_response_retries_exact_private_payload(self):
        bodies = []
        def request(req, timeout):
            self.assertTrue(req.full_url.endswith("/api/fitness/capture"))
            bodies.append(req.data)
            if len(bodies) == 1:
                raise TimeoutError("synthetic lost response")
            return io.BytesIO(json.dumps({"ok": True, "captureId": "a" * 64, "rides": 1}).encode())
        with patch.dict(os.environ, FITNESS_CAPTURE_SECRET="synthetic"), patch.object(publisher.time, "sleep"):
            result = publisher.publish({"rides": [self.activity(1)], "coverage": "complete"}, request=request)
        self.assertEqual(result["captureId"], "a" * 64)
        self.assertEqual(bodies[0], bodies[1])

    def test_missing_required_duration_is_not_fabricated_as_zero(self):
        activity = self.activity(1)
        del activity["moving_time"]
        with patch.object(collector, "api_get", side_effect=[[activity], []]):
            with self.assertRaises(ValueError):
                collector.fetch_rides("synthetic", all_history=True)

    def test_optional_detail_enrichment_is_bounded_during_full_history_capture(self):
        activities = [dict(self.activity(i), has_heartrate=True) for i in range(1, 31)]
        def get(path, _token, params=None):
            if path == "/athlete/activities":
                return activities if params["page"] == 1 else []
            return {"average_heartrate": 100}
        with patch.object(collector, "api_get", side_effect=get) as api:
            rides = collector.fetch_rides("synthetic", all_history=True)
        self.assertEqual(len(rides), 30)
        self.assertEqual(sum(call.args[0].startswith("/activities/") for call in api.call_args_list), 10)

    def test_rejects_origin_with_path_before_sending_credentials(self):
        with patch.dict(os.environ, FITNESS_CAPTURE_SECRET="synthetic", QMC_BASE="https://dashboard.invalid/redirect?to=other"):
            with self.assertRaises(ValueError):
                publisher.publish({"rides": []}, request=lambda *_a, **_kw: self.fail("must not send credentials"))

    def test_receipt_for_a_partial_activity_count_is_rejected(self):
        with patch.dict(os.environ, FITNESS_CAPTURE_SECRET="synthetic"), patch.object(publisher.time, "sleep"):
            with self.assertRaises(ValueError):
                publisher.publish({"rides": [self.activity(1)]}, request=lambda *_a, **_kw: io.BytesIO(json.dumps({"ok": True, "captureId": "a" * 64, "rides": 0}).encode()))

    def test_missing_receipt_is_not_saved(self):
        with patch.dict(os.environ, FITNESS_CAPTURE_SECRET="synthetic"), patch.object(publisher.time, "sleep"):
            with self.assertRaises(ValueError):
                publisher.publish({"rides": []}, request=lambda *args, **kwargs: io.BytesIO(b'{}'))


if __name__ == "__main__":
    unittest.main()
