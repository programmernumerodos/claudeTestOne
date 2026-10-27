"""Offline fakes for Open-Meteo, NOAA CO-OPS and NHC, shared by tests and demos.

``FakeHTTP.install(monkeypatch, tmp_path)`` replaces the shared requests session
so the whole app (CLI or dashboard) runs without the network. Toggle failure and
scenario flags on the instance.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from flweatherops import data, tropical

TZ = ZoneInfo("America/New_York")


def fake_forecast() -> dict[str, Any]:
    """Four days of hourly data: calm mornings/nights, stormy afternoons."""
    now = datetime.now(TZ).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    start = now.replace(hour=0)
    times = [start + timedelta(hours=i) for i in range(96)]
    hourly: dict[str, list[Any]] = {"time": [t.strftime("%Y-%m-%dT%H:%M") for t in times]}
    for t in times:
        afternoon = 13 <= t.hour <= 17
        night = not 7 <= t.hour <= 18
        vals = {
            "temperature_2m": 84 if afternoon else 74,
            "apparent_temperature": 92 if afternoon else 76,
            "relative_humidity_2m": 70,
            "precipitation_probability": 55 if afternoon else 10,
            "precipitation": 0.04 if afternoon else 0,
            "weather_code": 95 if t.hour == 16 else (80 if afternoon else 2),
            "cloud_cover": 60 if afternoon else (10 if night else 30),
            "cloud_cover_low": 30 if afternoon else 5,
            "cloud_cover_mid": 20,
            "cloud_cover_high": 10,
            "dew_point_2m": 68,
            "visibility": 24000,
            "wind_speed_10m": 14 if afternoon else 7,
            "wind_direction_10m": 90,
            "wind_gusts_10m": 22 if afternoon else 12,
            "wind_speed_80m": 18 if afternoon else 12,
            "wind_direction_80m": 95,
            "wind_speed_120m": 20 if afternoon else 13,
            "uv_index": 7 if 10 <= t.hour <= 15 else 0,
            "cape": 1800 if afternoon else 300,
            "is_day": 0 if night else 1,
        }
        for k, v in vals.items():
            hourly.setdefault(k, []).append(v)
    hourly["visibility"][5] = None  # missing value must not crash
    days = sorted({t.date() for t in times})
    daily = {
        "time": [d.isoformat() for d in days],
        "sunrise": [f"{d.isoformat()}T07:15" for d in days],
        "sunset": [f"{d.isoformat()}T18:40" for d in days],
    }
    return {
        "hourly": hourly,
        "daily": daily,
        "current": {"temperature_2m": 75.5, "wind_speed_10m": 8},
    }


def fake_marine(times: list[str]) -> dict[str, Any]:
    n = len(times)
    return {"hourly": {
        "time": times, "wave_height": [0.9] * n, "wave_direction": [80] * n,
        "wave_period": [7] * n, "wind_wave_height": [0.5] * n, "wind_wave_period": [4] * n,
        "swell_wave_height": [0.6] * n, "swell_wave_direction": [70] * n,
        "swell_wave_period": [9] * n,
    }}


def fake_tides() -> dict[str, Any]:
    t0 = datetime.now(TZ).replace(tzinfo=None, minute=12, second=0, microsecond=0) - timedelta(hours=10)
    preds = []
    for i in range(16):
        t = t0 + timedelta(hours=6 * i)
        high = i % 2 == 1
        preds.append({"t": t.strftime("%Y-%m-%d %H:%M"), "v": "3.1" if high else "0.4",
                      "type": "H" if high else "L"})
    return {"predictions": preds}


# --------------------------------------------------------------------------- #
# NHC: a hurricane in the Bahamas heading WNW toward SE Florida
# --------------------------------------------------------------------------- #

NHC_TCM_URL = "https://www.nhc.noaa.gov/text/MIATCMAT4.shtml"
NHC_TCP_URL = "https://www.nhc.noaa.gov/text/MIATCPAT4.shtml"


def _nhc_now() -> datetime:
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def fake_nhc_json() -> dict[str, Any]:
    return {"activeStorms": [
        {
            "id": "al042026", "binNumber": "AT4", "name": "Testy", "classification": "HU",
            "intensity": "85", "pressure": "968",
            "latitude": "25.0N", "longitude": "75.0W",
            "latitudeNumeric": 25.0, "longitudeNumeric": -75.0,
            "movementDir": 285, "movementSpeed": 12,
            "lastUpdate": _nhc_now().isoformat().replace("+00:00", "Z"),
            "publicAdvisory": {"advNum": "12", "url": NHC_TCP_URL},
            "forecastAdvisory": {"advNum": "12", "url": NHC_TCM_URL},
        },
        {   # Far-away East Pacific system: must be ignored (not Atlantic).
            "id": "ep092026", "name": "Faraway", "classification": "TS", "intensity": "45",
            "latitudeNumeric": 15.0, "longitudeNumeric": -110.0,
        },
    ]}


def fake_tcm() -> str:
    t0 = _nhc_now()
    pts = [(12, 25.4, 76.6, 90), (24, 26.0, 78.5, 95), (36, 26.8, 80.3, 85),
           (48, 27.9, 81.6, 65), (72, 30.0, 82.5, 45)]
    lines = ["<html><body><pre>",
             "HURRICANE TESTY FORECAST/ADVISORY NUMBER  12",
             f"HURRICANE CENTER LOCATED NEAR 25.0N  75.0W AT {t0:%d/%H%M}Z", ""]
    for h, lat, lon, kt in pts:
        t = t0 + timedelta(hours=h)
        kind = "OUTLOOK" if h > 60 else "FORECAST"
        lines += [f"{kind} VALID {t:%d/%H%M}Z {lat:.1f}N  {lon:.1f}W",
                  f"MAX WIND {kt:3d} KT...GUSTS {kt + 20:3d} KT.", ""]
    lines.append("</pre></body></html>")
    return "\n".join(lines)


def fake_tcp() -> str:
    return """<pre>
BULLETIN
Hurricane Testy Advisory Number  12

WATCHES AND WARNINGS
--------------------
CHANGES WITH THIS ADVISORY:

None.

SUMMARY OF WATCHES AND WARNINGS IN EFFECT:

A Hurricane Watch is in effect for...
* Deerfield Beach to Sebastian Inlet
* Lake Okeechobee

A Tropical Storm Warning is in effect for...
* Northwestern Bahamas

For storm information specific to your area, please monitor
products issued by your national meteorological service.

DISCUSSION AND OUTLOOK
----------------------
</pre>"""


class FakeResp:
    def __init__(self, payload: Any = None, status: int = 200, text: str | None = None) -> None:
        self._p, self.status_code, self.reason = payload, status, "OK"
        self.text = text if text is not None else ""

    def json(self) -> Any:
        if self._p is None:
            raise ValueError("not json")
        return self._p


class FakeHTTP:
    """Routes requests to fake payloads. Flip attributes to simulate failures."""

    def __init__(self) -> None:
        self.forecast_fails = False
        self.marine_fails = False
        self.nhc_fails = False
        self.storms = True
        self.calls: list[str] = []
        self.forecast = fake_forecast()

    def get(self, url: str, params: Any = None, timeout: Any = None) -> FakeResp:
        self.calls.append(url)
        if url == data.FORECAST_URL:
            if self.forecast_fails:
                raise requests.ConnectionError("down")
            return FakeResp(self.forecast)
        if url == data.MARINE_URL:
            if self.marine_fails:
                return FakeResp({"error": True, "reason": "boom"}, 400)
            return FakeResp(fake_marine(self.forecast["hourly"]["time"]))
        if url == data.NOAA_STATIONS_URL:
            return FakeResp({"stations": [
                {"id": "8722956", "name": "Test Inlet", "lat": 26.55, "lng": -80.05},
                {"id": "8722004", "name": "Test Inlet North", "lat": 27.70, "lng": -80.38}]})
        if url == data.NOAA_DATA_URL:
            return FakeResp(fake_tides())
        if url == tropical.NHC_CURRENT_URL:
            if self.nhc_fails:
                raise requests.ConnectionError("nhc down")
            return FakeResp(fake_nhc_json() if self.storms else {"activeStorms": []})
        if url == NHC_TCM_URL:
            return FakeResp(text=fake_tcm())
        if url == NHC_TCP_URL:
            return FakeResp(text=fake_tcp())
        if url == data.GEOCODE_URL:
            return FakeResp({"results": [
                {"name": "Jupiter", "latitude": 26.934, "longitude": -80.094, "admin1": "Florida"}]})
        raise requests.ConnectionError(f"unexpected URL {url}")

    def install(self, monkeypatch: Any, tmp_path: Path) -> "FakeHTTP":
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        monkeypatch.setattr(data.session(), "get", self.get)
        return self
