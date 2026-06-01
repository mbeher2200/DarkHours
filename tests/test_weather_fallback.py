"""weather.forecast() provider-selection + fallback (hermetic — no network).

M6.1: any NOAA/NWS failure (outage/timeout/5xx, not only "outside coverage")
must fall back to Open-Meteo so US users still get a forecast during an
api.weather.gov blip. 7Timer blending is stubbed out so nothing hits the wire.
"""
from datetime import datetime, timezone

import pytest

from PyNightSkyPredictor import weather as wx


def _pt():
    return wx.WeatherPoint(
        time=datetime(2026, 6, 2, 6, tzinfo=timezone.utc),
        cloud_cover_pct=20, seeing_arcsec=None, transparency=None,
        humidity_pct=50, wind_speed_ms=2.0, lifted_index=4,
        precip_type="none", temperature_c=12.0, feels_like_c=12.0,
    )


@pytest.fixture(autouse=True)
def _no_7timer_no_explicit_provider(monkeypatch):
    # keep _blend_7timer offline (identity) and ensure auto-select path is used
    monkeypatch.setattr(wx, "_blend_7timer", lambda points, lat, lon: points)
    monkeypatch.setattr(wx, "_provider", None, raising=False)


def test_uses_noaa_when_available(monkeypatch):
    monkeypatch.setattr(wx.NOAAProvider, "forecast", lambda self, lat, lon: [_pt()])
    # Open-Meteo should NOT be called on the happy path
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast",
                        lambda self, lat, lon: (_ for _ in ()).throw(AssertionError("should not call Open-Meteo")))
    points, source = wx.forecast(40.0, -105.0)
    assert source == "NOAA/NWS" and len(points) == 1


@pytest.mark.parametrize("noaa_error", [
    "NOAA grid data request failed: HTTP 500",   # weather.gov outage
    "NOAA points lookup failed: timed out",        # network timeout
    "Location not covered by NOAA/NWS",            # outside coverage (pre-existing path)
])
def test_falls_back_to_open_meteo_on_any_noaa_error(monkeypatch, noaa_error):
    def _boom(self, lat, lon):
        raise RuntimeError(noaa_error)
    monkeypatch.setattr(wx.NOAAProvider, "forecast", _boom)
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast", lambda self, lat, lon: [_pt(), _pt()])
    points, source = wx.forecast(40.0, -105.0)
    assert source == "Open-Meteo" and len(points) == 2


def test_propagates_when_both_providers_fail(monkeypatch):
    monkeypatch.setattr(wx.NOAAProvider, "forecast",
                        lambda self, lat, lon: (_ for _ in ()).throw(RuntimeError("NOAA points lookup failed: HTTP 503")))
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast",
                        lambda self, lat, lon: (_ for _ in ()).throw(RuntimeError("Open-Meteo request failed: HTTP 502")))
    with pytest.raises(RuntimeError, match="Open-Meteo"):
        wx.forecast(40.0, -105.0)
