import csv
import math
import os
from datetime import date, datetime
from itertools import pairwise

from fastapi import HTTPException

# The parser accepts the current headings and a few common alternatives without
# requiring any changes to existing log files.
COLUMN_ALIASES = {
    "timestamp": ("timestamp", "time", "datetime"),
    "pm1": ("pm1_ug_m3", "pm1", "pm1.0"),
    "pm25": ("pm25_ug_m3", "pm2_5_ug_m3", "pm2.5", "pm25"),
    "pm4": ("pm4_ug_m3", "pm4", "pm4.0"),
    "pm10": ("pm10_ug_m3", "pm10"),
    "voc": ("voc_index", "voc"),
    "nox": ("nox_index", "nox"),
    "co2": ("co2_ppm", "co2"),
    "temperature": ("temperature_c", "temperature", "temp_c"),
    "humidity": ("humidity_percent", "humidity", "rh_percent"),
    "ppe_confirmed": ("ppe_confirmed", "ppe_worn"),
    "mask_detected": ("mask_detected", "mask_worn"),
    "respirator_detected": ("respirator_detected", "respirator_worn"),
}

METRICS = {
    "pm1": {"label": "PM1.0", "unit": "µg/m³", "ppe_protective": True},
    "pm25": {"label": "PM2.5", "unit": "µg/m³", "ppe_protective": True},
    "pm4": {"label": "PM4.0", "unit": "µg/m³", "ppe_protective": True},
    "pm10": {"label": "PM10", "unit": "µg/m³", "ppe_protective": True},
    "voc": {"label": "VOC index", "unit": "index", "ppe_protective": True},
    "nox": {"label": "NOx index", "unit": "index"},
    "co2": {"label": "CO₂", "unit": "ppm"},
    "temperature": {"label": "Temperature", "unit": "°C"},
    "humidity": {"label": "Humidity", "unit": "% RH"},
}

# SEN66 error/sentinel values can otherwise look like extreme exposure events.
INVALID_SENTINELS = {
    "pm1": {6553.5},
    "pm25": {6553.5},
    "pm4": {6553.5},
    "pm10": {6553.5},
    "voc": {32767.0, -32768.0},
    "nox": {32767.0, -32768.0},
    "co2": {65535.0},
    "temperature": {163.835, -163.84},
    "humidity": {327.67, -327.68},
}


class DashboardData:
    def __init__(self, log_directory, thresholds, max_points=1200):
        self.log_directory = log_directory
        self.thresholds = thresholds
        self.max_points = max_points

    def available_dates(self):
        days = set()

        for row in self._iter_csv_rows():
            timestamp = self._parse_timestamp(self._get_value(row, "timestamp"))

            if timestamp is not None:
                days.add(timestamp.date().isoformat())

        return {"dates": sorted(days, reverse=True)}

    def day(self, selected_day):
        try:
            requested_date = date.fromisoformat(selected_day)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Date must use YYYY-MM-DD.")

        rows = []
        ignored_rows = 0

        for raw_row in self._iter_csv_rows():
            timestamp = self._parse_timestamp(self._get_value(raw_row, "timestamp"))

            if timestamp is None:
                ignored_rows += 1
                continue

            if timestamp.date() != requested_date:
                continue

            row = {"timestamp": timestamp}

            for metric in METRICS:
                row[metric] = self._parse_number(
                    self._get_value(raw_row, metric), metric
                )

            ppe_confirmed = self._parse_bool(self._get_value(raw_row, "ppe_confirmed"))
            mask_detected = self._parse_bool(self._get_value(raw_row, "mask_detected"))
            respirator_detected = self._parse_bool(
                self._get_value(raw_row, "respirator_detected")
            )
            row["ppe_worn"] = (
                ppe_confirmed is True
                or mask_detected is True
                or respirator_detected is True
            )

            if any(row[metric] is not None for metric in METRICS):
                rows.append(row)
            else:
                ignored_rows += 1

        rows.sort(key=lambda item: item["timestamp"])

        summary = self._summarize(rows)
        plotted_rows = self._downsample(rows)

        points = []
        for row in plotted_rows:
            point = {
                "timestamp": row["timestamp"].isoformat(timespec="seconds"),
                "ppe_worn": row["ppe_worn"],
            }
            point.update({metric: row[metric] for metric in METRICS})
            points.append(point)

        return {
            "date": requested_date.isoformat(),
            "sample_count": len(rows),
            "plotted_sample_count": len(points),
            "ignored_row_count": ignored_rows,
            "metrics": {
                key: {
                    **details,
                    "threshold": self.thresholds.get(key),
                }
                for key, details in METRICS.items()
            },
            "summary": summary,
            "points": points,
        }

    def _csv_paths(self):
        try:
            names = os.listdir(self.log_directory)
        except OSError:
            return []

        return sorted(
            os.path.join(self.log_directory, name)
            for name in names
            if name.lower().endswith(".csv")
        )

    def _iter_csv_rows(self):
        for path in self._csv_paths():
            try:
                # Replacing NUL bytes makes interrupted files readable while the
                # logger continues to append to them.
                with open(
                    path, "r", encoding="utf-8", errors="replace", newline=""
                ) as file:
                    clean_lines = (line.replace("\x00", "") for line in file)
                    reader = csv.DictReader(clean_lines)

                    if not reader.fieldnames:
                        continue

                    for row in reader:
                        if row:
                            yield row
            except (OSError, csv.Error):
                # One damaged file should not prevent other days from loading.
                continue

    @staticmethod
    def _get_value(row, field):
        normalized = {
            str(key).strip().lower(): value
            for key, value in row.items()
            if key is not None
        }

        for alias in COLUMN_ALIASES[field]:
            if alias.lower() in normalized:
                return normalized[alias.lower()]

        return None

    @staticmethod
    def _parse_timestamp(value):
        if value is None:
            return None

        try:
            return datetime.fromisoformat(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_number(value, metric):
        if value is None or str(value).strip() == "":
            return None

        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

        if not math.isfinite(number) or number in INVALID_SENTINELS.get(metric, set()):
            return None

        return number

    @staticmethod
    def _parse_bool(value):
        if value is None:
            return None

        normalized = str(value).strip().lower()

        if normalized in {"true", "1", "yes"}:
            return True

        if normalized in {"false", "0", "no"}:
            return False

        return None

    def _summarize(self, rows):
        summary = {}

        for metric in METRICS:
            samples = [
                (row["timestamp"], row[metric])
                for row in rows
                if row[metric] is not None
            ]
            values = [value for _, value in samples]
            threshold = self.thresholds.get(metric)

            item = {
                "maximum": max(values) if values else None,
                "average": sum(values) / len(values) if values else None,
                "valid_samples": len(values),
                "exceedance_events": None,
                "seconds_above": None,
            }

            if threshold is not None:
                events, seconds = self._exposure_duration(samples, threshold)
                item["exceedance_events"] = events
                item["seconds_above"] = seconds

            summary[metric] = item

        return summary

    @staticmethod
    def _exposure_duration(samples, threshold):
        if not samples:
            return 0, 0.0

        positive_deltas = [
            (right[0] - left[0]).total_seconds()
            for left, right in pairwise(samples)
            if 0 < (right[0] - left[0]).total_seconds() <= 10
        ]
        typical_interval = (
            sorted(positive_deltas)[len(positive_deltas) // 2]
            if positive_deltas
            else 1.0
        )
        maximum_gap = max(typical_interval * 3, 3.0)
        events = 0
        seconds = 0.0
        previously_above = False

        for index, (timestamp, value) in enumerate(samples):
            above = value >= threshold

            if index:
                gap_from_previous = (timestamp - samples[index - 1][0]).total_seconds()
                if gap_from_previous <= 0 or gap_from_previous > maximum_gap:
                    previously_above = False

            if above and not previously_above:
                events += 1

            if above:
                if index + 1 < len(samples):
                    interval = (samples[index + 1][0] - timestamp).total_seconds()
                    if interval <= 0 or interval > maximum_gap:
                        interval = typical_interval
                else:
                    interval = typical_interval

                seconds += interval

            previously_above = above

        return events, round(seconds, 1)

    def _downsample(self, rows):
        if len(rows) <= self.max_points:
            return rows

        # Keep the minimum and maximum for every metric in each time bucket.
        # This preserves short exposure spikes better than a simple stride.
        selected = {0, len(rows) - 1}
        bucket_count = max(1, self.max_points // (len(METRICS) * 2))
        bucket_size = math.ceil(len(rows) / bucket_count)

        # Retain PPE state transitions so protected exposure sections remain
        # visible after graph downsampling.
        for index in range(1, len(rows)):
            if rows[index]["ppe_worn"] != rows[index - 1]["ppe_worn"]:
                selected.add(index - 1)
                selected.add(index)

        for start in range(0, len(rows), bucket_size):
            end = min(start + bucket_size, len(rows))

            for metric in METRICS:
                valid_indices = [
                    index
                    for index in range(start, end)
                    if rows[index][metric] is not None
                ]

                if valid_indices:
                    selected.add(
                        min(valid_indices, key=lambda index: rows[index][metric])
                    )
                    selected.add(
                        max(valid_indices, key=lambda index: rows[index][metric])
                    )

        selected = sorted(selected)

        if len(selected) > self.max_points:
            step = (len(selected) - 1) / (self.max_points - 1)
            selected = sorted(
                {selected[round(index * step)] for index in range(self.max_points)}
            )

        return [rows[index] for index in selected]
