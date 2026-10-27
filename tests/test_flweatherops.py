"""Offline tests: safety rules and an end-to-end CLI run against mocked APIs.

Run with:  python -m pytest -q
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
import requests

from flweatherops import cli, data
from flweatherops.config import DEFAULT_CONFIG
from flweatherops.data import HourData
from flweatherops.safety import Status, evaluate, summarize

TH = DEFAULT_CONFIG["thresholds"]


def calm_hour(**kw: Any) -> HourData:
    base = dict(
        time=datetime(2026, 10, 27, 9), temp_f=78, heat_index_f=80, precip_prob_pct=5,
        precip_in=0, weather_code=1, visibility_mi=15, wind_mph=6, gust_mph=10,
        wind80_mph=9, wind120_mph=11, cape=200, wave_ft=1.5,
    )
    base.update(kw)
    return HourData(**base)


def test_calm_is_safe() -> None:
    assert evaluate(calm_hour(), "drone", TH).status is Status.SAFE
    assert evaluate(calm_hour(), "marine", TH).status is Status.SAFE


def test_gusts_trigger_caution_with_reason() -> None:
    v = evaluate(calm_hour(gust_mph=20), "drone", TH)
    assert v.status is Status.CAUTION
    assert any(r.label == "Gusts" for r in v.reasons)
    assert "Gusts 20 mph ≥ 18 mph" in v.reasons[0].text()


def test_aloft_and_visibility_nogo() -> None:
    assert evaluate(calm_hour(wind120_mph=30), "drone", TH).status is Status.NOGO
    assert evaluate(calm_hour(visibility_mi=2), "drone", TH).status is Status.NOGO


def test_waves_only_affect_marine() -> None:
    h = calm_hour(wave_ft=6)
    assert evaluate(h, "marine", TH).status is Status.NOGO
    assert evaluate(h, "drone", TH).status is Status.SAFE


def test_lightning_proxy() -> None:
    assert evaluate(calm_hour(weather_code=95), "marine", TH).status is Status.NOGO
    assert evaluate(calm_hour(cape=1500, precip_prob_pct=35), "drone", TH).status is Status.CAUTION
    # High CAPE with no rain chance is not flagged.
    assert evaluate(calm_hour(cape=3000, precip_prob_pct=5), "drone", TH).status is Status.SAFE


def test_knots_display() -> None:
    v = evaluate(calm_hour(wind_mph=23), "marine", TH)
    assert "20 kt" in v.reasons[0].text("kn")


def test_summary_best_window_and_change() -> None:
    hours = [calm_hour(time=datetime(2026, 10, 27, 6 + i)) for i in range(6)]
    hours[3].gust_mph = 30
    verdicts = [evaluate(h, "drone", TH) for h in hours]
    s = summarize(hours, verdicts)
    assert s.best_window == (hours[0], hours[2])
    assert s.change is not None and s.change[1] is Status.NOGO
    assert s.top_factors == ["Gusts"]


# --------------------------------------------------------------------------- #
# End-to-end with mocked HTTP
# --------------------------------------------------------------------------- #


def _fake_forecast() -> dict[str, Any]:
    now = datetime.now(ZoneInfo("America/New_York")).replace(
        tzinfo=None, minute=0, second=0, microsecond=0
    )
    start = now.replace(hour=0)
    times = [start + timedelta(hours=i) for i in range(72)]
    hourly: dict[str, list[Any]] = {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times]}
    for i, t in enumerate(times):
        afternoon = 13 <= t.hour <= 17
        vals = {
            "temperature_2m": 84 if afternoon else 74,
            "apparent_temperature": 92 if afternoon else 76,
            "relative_humidity_2m": 70,
            "precipitation_probability": 55 if afternoon else 10,
            "precipitation": 0.04 if afternoon else 0,
            "weather_code": 95 if t.hour == 16 else (80 if afternoon else 2),
            "cloud_cover": 60, "cloud_cover_low": 30,
            "visibility": 24000,
            "wind_speed_10m": 14 if afternoon else 7,
            "wind_direction_10m": 90,
            "wind_gusts_10m": 22 if afternoon else 12,
            "wind_speed_80m": 18 if afternoon else 12,
            "wind_direction_80m": 95,
            "wind_speed_120m": 20 if afternoon else 13,
            "uv_index": 7 if t.hour in range(10, 16) else 0,
            "cape": 1800 if afternoon else 300,
            "is_day": 1 if 7 <= t.hour <= 18 else 0,
        }
        for k, v in vals.items():
            hourly.setdefault(k, []).append(v)
    hourly["visibility"][5] = None  # missing value must not crash
    return {"hourly": hourly, "current": {"temperature_2m": 75.5, "wind_speed_10m": 8}}


def _fake_marine(times: list[str]) -> dict[str, Any]:
    n = len(times)
    return {"hourly": {
        "time": times, "wave_height": [0.9] * n, "wave_direction": [80] * n,
        "wave_period": [7] * n, "wind_wave_height": [0.5] * n, "wind_wave_period": [4] * n,
        "swell_wave_height": [0.6] * n, "swell_wave_direction": [70] * n,
        "swell_wave_period": [9] * n,
    }}


class FakeResp:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._p, self.status_code, self.reason = payload, status, "OK"

    def json(self) -> Any:
        return self._p


@pytest.fixture
def fake_http(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> dict[str, Any]:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    fc = _fake_forecast()
    state: dict[str, Any] = {"marine_fails": False, "forecast_fails": False}

    def get(url: str, params: Any = None, timeout: Any = None) -> FakeResp:
        if url == data.FORECAST_URL:
            if state["forecast_fails"]:
                raise requests.ConnectionError("down")
            return FakeResp(fc)
        if url == data.MARINE_URL:
            if state["marine_fails"]:
                return FakeResp({"error": True, "reason": "boom"}, 400)
            return FakeResp(_fake_marine(fc["hourly"]["time"]))
        if url == data.NOAA_STATIONS_URL:
            return FakeResp({"stations": [
                {"id": "8722004", "name": "Test Inlet", "lat": 27.70, "lng": -80.38}]})
        if url == data.NOAA_DATA_URL:
            t0 = datetime.now().replace(minute=12)
            return FakeResp({"predictions": [
                {"t": (t0 + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M"),
                 "v": "3.1" if h % 12 == 2 else "0.4", "type": "H" if h % 12 == 2 else "L"}
                for h in (2, 8, 14, 20)]})
        raise AssertionError(url)

    monkeypatch.setattr(data.session(), "get", get)
    return state


def test_cli_default_run(fake_http: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--hours", "24"]) == 0
    out = capsys.readouterr().out
    assert "Vero Beach" in out and "Recommendation" in out and "Tides" in out


def test_cli_all_knots_and_marine_failure(
    fake_http: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_http["marine_fails"] = True
    assert cli.main(["--all", "--units", "kn", "--no-cache"]) == 0
    out = capsys.readouterr().out
    assert "All locations" in out and "Okeechobee" in out
    assert "Marine data unavailable" in out


def test_cache_fallback(fake_http: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["vero"]) == 0
    fake_http["forecast_fails"] = True
    assert cli.main(["vero"]) == 0
    assert "using cached data" in capsys.readouterr().out


def test_total_failure_exit_code(
    fake_http: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    fake_http["forecast_fails"] = True
    assert cli.main(["vero", "--no-cache"]) == 1
    assert "network unreachable" in capsys.readouterr().out
