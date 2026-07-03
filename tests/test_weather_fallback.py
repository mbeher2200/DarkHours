"""weather.forecast() provider behaviour (hermetic — no network).

Open-Meteo is the primary provider; 7Timer is the fallback. Network calls are
stubbed so nothing hits the wire.
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


@pytest.fixture(autouse=True)
def _no_air_quality(monkeypatch):
    """Air quality is fetched unconditionally by wx.forecast() — stub it so tests
    never make a real HTTP call."""
    monkeypatch.setattr(wx, "_fetch_air_quality", lambda lat, lon: [])


def test_uses_open_meteo_by_default(monkeypatch):
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast", lambda self, lat, lon: [_pt()])
    points, source, fetched_at = wx.forecast(40.0, -105.0)
    assert source == "Open-Meteo" and len(points) == 1


def test_uses_open_meteo_outside_us(monkeypatch):
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast", lambda self, lat, lon: [_pt()])
    points, source, fetched_at = wx.forecast(51.5, -0.1)   # London
    assert source == "Open-Meteo" and len(points) == 1


def test_falls_back_to_7timer_when_open_meteo_fails(monkeypatch):
    """When Open-Meteo fails, 7Timer data is returned as the fallback source."""
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast",
                        lambda self, lat, lon: (_ for _ in ()).throw(
                            RuntimeError("Open-Meteo request failed: HTTP 502")))
    monkeypatch.setattr(wx.SevenTimerProvider, "forecast",
                        lambda self, lat, lon: [_pt(), _pt()])
    points, source, fetched_at = wx.forecast(40.0, -105.0)
    assert source == "7Timer" and len(points) == 2


def test_propagates_when_both_providers_fail(monkeypatch):
    """RuntimeError is raised only when both Open-Meteo and 7Timer fail."""
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast",
                        lambda self, lat, lon: (_ for _ in ()).throw(
                            RuntimeError("Open-Meteo request failed: HTTP 502")))
    monkeypatch.setattr(wx.SevenTimerProvider, "forecast",
                        lambda self, lat, lon: (_ for _ in ()).throw(
                            RuntimeError("7Timer request failed: timeout")))
    with pytest.raises(RuntimeError, match="Open-Meteo.*7Timer also failed"):
        wx.forecast(40.0, -105.0)


def test_explicit_provider_is_used(monkeypatch):
    class _Stub(wx.WeatherProvider):
        name = "Stub"
        def forecast(self, lat, lon): return [_pt(), _pt()]

    monkeypatch.setattr(wx, "_provider", _Stub())
    points, source, fetched_at = wx.forecast(40.0, -105.0)
    assert source == "Stub" and len(points) == 2


def test_forecast_returns_three_tuple_with_iso_fetched_at(monkeypatch):
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast", lambda self, lat, lon: [_pt()])
    result = wx.forecast(40.0, -105.0)
    assert len(result) == 3
    points, source, fetched_at = result
    assert isinstance(fetched_at, str)
    datetime.fromisoformat(fetched_at)  # raises if not ISO-parseable


def test_air_quality_failure_does_not_break_forecast(monkeypatch):
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast", lambda self, lat, lon: [_pt()])
    monkeypatch.setattr(wx, "_fetch_air_quality",
                        lambda lat, lon: (_ for _ in ()).throw(RuntimeError("aq down")))
    points, source, fetched_at = wx.forecast(40.0, -105.0)
    assert len(points) == 1
    assert points[0].aerosol_optical_depth is None
    assert points[0].pm2_5 is None


def test_air_quality_merges_into_primary_path(monkeypatch):
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast", lambda self, lat, lon: [_pt()])
    monkeypatch.setattr(wx, "_fetch_air_quality",
                        lambda lat, lon: [(_pt().time, 15.0, 0.25)])
    points, source, fetched_at = wx.forecast(40.0, -105.0)
    assert points[0].pm2_5 == pytest.approx(15.0)
    assert points[0].aerosol_optical_depth == pytest.approx(0.25)


def test_air_quality_merges_into_7timer_fallback_path(monkeypatch):
    """Air quality reaches points even when Open-Meteo fails and 7Timer serves as
    full primary — validates the fallback branch also merges AQ."""
    monkeypatch.setattr(wx.OpenMeteoProvider, "forecast",
                        lambda self, lat, lon: (_ for _ in ()).throw(
                            RuntimeError("Open-Meteo request failed: HTTP 502")))
    monkeypatch.setattr(wx.SevenTimerProvider, "forecast",
                        lambda self, lat, lon: [_pt()])
    monkeypatch.setattr(wx, "_fetch_air_quality",
                        lambda lat, lon: [(_pt().time, 15.0, 0.25)])
    points, source, fetched_at = wx.forecast(40.0, -105.0)
    assert source == "7Timer"
    assert points[0].pm2_5 == pytest.approx(15.0)
    assert points[0].aerosol_optical_depth == pytest.approx(0.25)


def test_historical_provider_url_includes_weather_code_and_precip_probability():
    """Regression guard: this URL was previously missing these params entirely,
    which meant precip_type could never be non-'none' on the historical path."""
    assert "weather_code" in wx.OpenMeteoHistoricalProvider._URL
    assert "precipitation_probability" in wx.OpenMeteoHistoricalProvider._URL
