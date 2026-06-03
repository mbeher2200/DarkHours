"""JSON serialization for the engine's dataclasses.

Lives in the API layer (not the engine) so the pure-function seam is preserved:
the engine keeps returning dataclasses; only this module knows how to turn them
into JSON-safe dicts. NightReport is a dataclass tree with datetime leaves, so
dataclasses.asdict() + an ISO-8601 encoder does the job. TripReport reuses the
serializers that already live in trip.py.
"""
import dataclasses
from datetime import date, datetime

from PyNightSkyPredictor import trip as _trip
from PyNightSkyPredictor.predictor import NightReport
from PyNightSkyPredictor.trip import TripReport


def _to_jsonable(obj):
    """Recursively convert datetimes to ISO strings; leave all other values as-is."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


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
