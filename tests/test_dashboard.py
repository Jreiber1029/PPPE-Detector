import csv
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_DIRECTORY = PROJECT_ROOT / "python"

import sys

sys.path.insert(0, str(PYTHON_DIRECTORY))

from dashboard import DashboardData

THRESHOLDS = {
    "pm1": 20.0,
    "pm25": 40.0,
    "pm4": 70.0,
    "pm10": 100.0,
    "voc": 2000,
    "nox": 100,
    "co2": 3000,
}


class DashboardDataTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.logs = Path(self.temporary_directory.name)

        # Use a representative log so tests never depend on personal data.
        path = self.logs / "sen66_2026-09-10.csv"
        with path.open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "timestamp",
                    "pm1_ug_m3",
                    "pm25_ug_m3",
                    "pm4_ug_m3",
                    "pm10_ug_m3",
                    "voc_index",
                    "nox_index",
                    "co2_ppm",
                    "temperature_c",
                    "humidity_percent",
                ]
            )
            writer.writerow(
                ["2026-09-10T12:00:00", 10, 20, 30, 40, 100, 10, 800, 22, 45]
            )
            writer.writerow(
                ["2026-09-10T12:00:01", 25, 45, 75, 105, 2100, 110, 3100, 23, 46]
            )

        self.dashboard = DashboardData(str(self.logs), THRESHOLDS, max_points=20)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_logs_are_discovered_and_filtered_by_day(self):
        dates = self.dashboard.available_dates()["dates"]

        self.assertIn("2026-09-10", dates)

        result = self.dashboard.day("2026-09-10")

        self.assertGreater(result["sample_count"], 0)
        self.assertLessEqual(result["plotted_sample_count"], 20)
        self.assertEqual(result["metrics"]["pm25"]["threshold"], 40.0)
        self.assertIsNotNone(result["summary"]["co2"]["average"])

    def test_partial_malformed_and_unknown_columns_do_not_crash(self):
        path = self.logs / "sen66_2026-09-12.csv"
        with path.open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["time", "pm1", "co2", "extra"])
            writer.writerow(["2026-09-12T10:00:00", "12.5", "900", "ignored"])
            writer.writerow(["2026-09-12T10:00:01", "bad", "65535", "ignored"])
            file.write("2026-09-12T10:00")
            file.write("\x00" * 20)

        result = self.dashboard.day("2026-09-12")

        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["summary"]["pm1"]["maximum"], 12.5)
        self.assertEqual(result["summary"]["co2"]["maximum"], 900.0)

    def test_event_count_and_duration_follow_timestamps(self):
        path = self.logs / "events.csv"
        with path.open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["timestamp", "pm1_ug_m3"])
            writer.writerows(
                [
                    ["2026-09-12T12:00:00", 10],
                    ["2026-09-12T12:00:01", 21],
                    ["2026-09-12T12:00:02", 22],
                    ["2026-09-12T12:00:03", 10],
                    ["2026-09-12T12:00:04", 25],
                ]
            )

        result = self.dashboard.day("2026-09-12")
        summary = result["summary"]["pm1"]

        self.assertEqual(summary["exceedance_events"], 2)
        self.assertEqual(summary["seconds_above"], 3.0)

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(HTTPException):
            self.dashboard.day("not-a-date")

    def test_http_endpoints_return_dates_and_one_filtered_day(self):
        app = FastAPI()
        app.get("/api/dates")(self.dashboard.available_dates)
        app.get("/api/day/{selected_day}")(self.dashboard.day)
        client = TestClient(app)

        dates_response = client.get("/api/dates")
        day_response = client.get("/api/day/2026-09-10")

        self.assertEqual(dates_response.status_code, 200)
        self.assertIn("2026-09-10", dates_response.json()["dates"])
        self.assertEqual(day_response.status_code, 200)
        self.assertEqual(day_response.json()["date"], "2026-09-10")
        self.assertTrue(day_response.json()["points"])

    def test_existing_ppe_columns_are_exposed_as_read_only_graph_state(self):
        path = self.logs / "ppe-state.csv"
        with path.open("w", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                ["timestamp", "pm1_ug_m3", "mask_detected", "ppe_confirmed"]
            )
            writer.writerow(["2026-09-12T13:00:00", 25, "True", "True"])
            writer.writerow(["2026-09-12T13:00:01", 30, "False", "False"])

        result = self.dashboard.day("2026-09-12")
        states = [point["ppe_worn"] for point in result["points"]]

        self.assertIn(True, states)
        self.assertIn(False, states)
        self.assertTrue(result["metrics"]["pm1"]["ppe_protective"])
        self.assertFalse(result["metrics"]["co2"].get("ppe_protective", False))


if __name__ == "__main__":
    unittest.main()
