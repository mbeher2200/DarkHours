"""JSON serialization for the engine's dataclasses.

Lives in the API layer (not the engine) so the pure-function seam is preserved:
the engine keeps returning dataclasses; only this module knows how to turn them
into JSON-safe dicts. NightReport is a dataclass tree with datetime leaves, so
dataclasses.asdict() + an ISO-8601 encoder does the job. TripReport reuses the
serializers that already live in trip.py.
"""
import dataclasses
import json
from datetime import date, datetime

from PyNightSkyPredictor import trip as _trip
from PyNightSkyPredictor.predictor import NightReport
from PyNightSkyPredictor.trip import TripReport


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _to_jsonable(obj):
    """Round-trip through json with an ISO datetime encoder → pure-JSON structure."""
    return json.loads(json.dumps(obj, default=_json_default))


def night_report_to_dict(report: NightReport) -> dict:
    """NightReport → JSON-safe dict (datetimes as ISO 8601, tuples as lists)."""
    return _to_jsonable(dataclasses.asdict(report))


def trip_report_to_dict(report: TripReport) -> dict:
    """TripReport → JSON-safe dict, reusing trip._to_dict for each NightSummary."""
    return {
        "date_start": report.date_start.isoformat(),
        "date_end":   report.date_end.isoformat(),
        "locations":  report.locations,
        "nights":     [_trip._to_dict(n) for n in report.nights],
        "ranked":     [_trip._to_dict(n) for n in report.ranked],
    }
