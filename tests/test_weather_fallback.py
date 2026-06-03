"""weather.forecast() provider behaviour (hermetic — no network).

Open-Meteo is the sole primary provider. 7Timer blending is stubbed out so
nothing hits the wire.
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
def _no_7timer(monkeypatch):
    monkeypatch.setattr(wx.SevenTimerProvider, "forecast", lambda self, lat, lon: [])
    monkeypatch.setattr(wx, "_provider", None, raising=False)


def test_uses_open_meteo_by_default(monkeypatch):
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast", lambda self, lat, lon: [_pt()])
    points, source = wx.forecast(40.0, -105.0)
    assert source == "Open-Meteo" and len(points) == 1


def test_uses_open_meteo_outside_us(monkeypatch):
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast", lambda self, lat, lon: [_pt()])
    points, source = wx.forecast(51.5, -0.1)   # London
    assert source == "Open-Meteo" and len(points) == 1


def test_propagates_when_open_meteo_fails(monkeypatch):
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast",
                        lambda self, lat, lon: (_ for _ in ()).throw(
                            RuntimeError("Open-Meteo request failed: HTTP 502")))
    with pytest.raises(RuntimeError, match="Open-Meteo"):
        wx.forecast(40.0, -105.0)


def test_explicit_provider_is_used(monkeypatch):
    class _Stub(wx.WeatherProvider):
        name = "Stub"
        def forecast(self, lat, lon): return [_pt(), _pt()]

    monkeypatch.setattr(wx, "_provider", _Stub())
    points, source = wx.forecast(40.0, -105.0)
    assert source == "Stub" and len(points) == 2
