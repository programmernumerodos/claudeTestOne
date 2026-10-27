"""Data fetching: Open-Meteo forecast + marine, Open-Meteo geocoding, NOAA CO-OPS tides.

Every fetch goes through :func:`get_json`, which caches the last successful
response on disk and falls back to it (with a warning) when the network or
API fails.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from flweatherops import __version__
from flweatherops.config import Location

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
NOAA_DATA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NOAA_STATIONS_URL = (
    "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
)

USER_AGENT = f"flWeatherOps/{__version__} (personal weather briefing CLI)"
TIMEOUT_S = 15
M_PER_MILE = 1609.344
FT_PER_M = 3.28084

HOURLY_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation_probability",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "dew_point_2m",
    "visibility",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "wind_speed_80m",
    "wind_direction_80m",
    "wind_speed_120m",
    "uv_index",
    "cape",
    "is_day",
]
CURRENT_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
]
DAILY_VARS = ["sunrise", "sunset"]
FORECAST_DAYS = 4  # enough for a 72 h scan starting late in the day
MARINE_VARS = [
    "wave_height",
    "wave_direction",
    "wave_period",
    "wind_wave_height",
    "wind_wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
]


class FetchError(Exception):
    """A request failed and no usable cached response was available."""


@dataclasses.dataclass
class FetchResult:
    """JSON payload plus where it came from."""

    data: dict[str, Any]
    cached_age: timedelta | None = None  # set when served from fallback cache


@dataclasses.dataclass
class HourData:
    """One hour of merged weather + marine data (internal units)."""

    time: datetime
    temp_f: float | None = None
    heat_index_f: float | None = None
    humidity_pct: float | None = None
    precip_prob_pct: float | None = None
    precip_in: float | None = None
    weather_code: int | None = None
    cloud_cover_pct: float | None = None
    cloud_low_pct: float | None = None
    cloud_mid_pct: float | None = None
    cloud_high_pct: float | None = None
    dew_point_f: float | None = None
    visibility_mi: float | None = None
    wind_mph: float | None = None
    wind_dir: float | None = None
    gust_mph: float | None = None
    wind80_mph: float | None = None
    wind80_dir: float | None = None
    wind120_mph: float | None = None
    uv_index: float | None = None
    cape: float | None = None
    is_day: bool = True
    wave_ft: float | None = None
    wave_dir: float | None = None
    wave_period_s: float | None = None
    wind_wave_ft: float | None = None
    swell_ft: float | None = None
    swell_dir: float | None = None
    swell_period_s: float | None = None

    @property
    def wind_aloft_mph(self) -> float | None:
        """Strongest of the 80 m / 120 m winds."""
        vals = [v for v in (self.wind80_mph, self.wind120_mph) if v is not None]
        return max(vals) if vals else None


@dataclasses.dataclass
class TideEvent:
    """A predicted high or low tide."""

    time: datetime
    height_ft: float
    kind: str  # "High" or "Low"


@dataclasses.dataclass
class TideStation:
    """A NOAA tide-prediction station."""

    id: str
    name: str
    distance_mi: float | None = None


@dataclasses.dataclass
class LocationData:
    """Everything fetched for one location."""

    location: Location
    current: HourData
    hours: list[HourData]
    marine_available: bool = False
    tide_station: TideStation | None = None
    tides: list[TideEvent] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)
    generated: datetime = dataclasses.field(default_factory=datetime.now)  # tz-aware when set
    sun: list[tuple[datetime, datetime]] = dataclasses.field(default_factory=list)

    def next_tide(self, after: datetime) -> TideEvent | None:
        """First tide event at or after ``after``."""
        return next((e for e in self.tides if e.time >= after), None)

    def prev_tide(self, before: datetime) -> TideEvent | None:
        """Last tide event before ``before``."""
        prior = [e for e in self.tides if e.time < before]
        return prior[-1] if prior else None


# --------------------------------------------------------------------------- #
# HTTP + cache
# --------------------------------------------------------------------------- #


def cache_dir() -> Path:
    """Directory used for cached API responses."""
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "flweatherops"


def _session() -> requests.Session:
    retry = Retry(
        total=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["User-Agent"] = USER_AGENT
    return s


_SESSION: requests.Session | None = None


def session() -> requests.Session:
    """Shared HTTP session with retries."""
    global _SESSION
    if _SESSION is None:
        _SESSION = _session()
    return _SESSION


def _cache_path(url: str, params: dict[str, Any]) -> Path:
    key = json.dumps([url, sorted(params.items())], default=str)
    return cache_dir() / (hashlib.sha256(key.encode()).hexdigest()[:24] + ".json")


def _read_cache(path: Path) -> tuple[dict[str, Any], timedelta] | None:
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(blob["fetched_at"])
        return blob["data"], datetime.now() - fetched
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_cache(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"fetched_at": datetime.now().isoformat(), "data": data}),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass  # caching is best-effort


def get_json(
    url: str,
    params: dict[str, Any],
    *,
    use_cache: bool = True,
    max_cache_age: timedelta = timedelta(hours=6),
    prefer_cache: bool = False,
) -> FetchResult:
    """GET ``url`` and return parsed JSON, falling back to the disk cache.

    Args:
        url: Endpoint.
        params: Query parameters.
        use_cache: Read/write the on-disk cache.
        max_cache_age: Oldest cached response accepted as a fallback (or as a
            fresh hit when ``prefer_cache`` is set).
        prefer_cache: Serve from cache without a request if it is younger than
            ``max_cache_age`` (used for slow-changing metadata).

    Raises:
        FetchError: The request failed and no usable cache exists.
    """
    path = _cache_path(url, params)
    if use_cache and prefer_cache:
        hit = _read_cache(path)
        if hit and hit[1] <= max_cache_age:
            return FetchResult(hit[0])

    try:
        resp = session().get(url, params=params, timeout=TIMEOUT_S)
        try:
            data = resp.json()
        except ValueError:
            data = None
        if resp.status_code != 200:
            reason = _api_error_reason(data) or resp.reason or "HTTP error"
            raise FetchError(f"{resp.status_code} {reason}")
        if not isinstance(data, dict):
            raise FetchError("malformed (non-JSON-object) response")
        reason = _api_error_reason(data)
        if reason:
            raise FetchError(reason)
        if use_cache:
            _write_cache(path, data)
        return FetchResult(data)
    except (requests.RequestException, FetchError) as exc:
        if use_cache:
            hit = _read_cache(path)
            if hit and hit[1] <= max_cache_age:
                return FetchResult(hit[0], cached_age=hit[1])
        raise FetchError(_short_error(exc)) from exc


def get_text(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    use_cache: bool = True,
    max_cache_age: timedelta = timedelta(hours=6),
) -> tuple[str, timedelta | None]:
    """GET a text/HTML resource with the same cache fallback as :func:`get_json`.

    Returns (text, cached_age) where cached_age is set when served from cache.
    """
    params = params or {}
    path = _cache_path(url, params)
    try:
        resp = session().get(url, params=params, timeout=TIMEOUT_S)
        if resp.status_code != 200:
            raise FetchError(f"{resp.status_code} {resp.reason or 'HTTP error'}")
        text = resp.text
        if use_cache:
            _write_cache(path, {"text": text})
        return text, None
    except (requests.RequestException, FetchError) as exc:
        if use_cache:
            hit = _read_cache(path)
            if hit and isinstance(hit[0].get("text"), str) and hit[1] <= max_cache_age:
                return hit[0]["text"], hit[1]
        raise FetchError(_short_error(exc)) from exc


def _api_error_reason(data: Any) -> str | None:
    """Extract an error message from Open-Meteo or NOAA error payloads."""
    if not isinstance(data, dict):
        return None
    if data.get("error") is True:
        return str(data.get("reason", "API error"))
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message", "API error"))
    return None


def _short_error(exc: Exception) -> str:
    if isinstance(exc, requests.Timeout):
        return "request timed out"
    if isinstance(exc, requests.ConnectionError):
        return "network unreachable"
    return str(exc)


def _age_note(what: str, age: timedelta) -> str:
    mins = int(age.total_seconds() // 60)
    when = f"{mins} min" if mins < 120 else f"{mins // 60} h"
    return f"{what}: live request failed — using cached data from {when} ago."


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _series(block: dict[str, Any], name: str, n: int) -> list[Any]:
    """Return a hourly series padded/truncated to ``n`` (missing -> None)."""
    vals = block.get(name)
    if not isinstance(vals, list):
        return [None] * n
    return (vals + [None] * n)[:n]


def _parse_times(block: dict[str, Any]) -> list[datetime]:
    times = block.get("time")
    if not isinstance(times, list) or not times:
        raise FetchError("response is missing hourly time series")
    try:
        return [datetime.fromisoformat(t) for t in times]
    except (TypeError, ValueError) as exc:
        raise FetchError(f"unparseable timestamps: {exc}") from exc


def _m_to_mi(v: Any) -> float | None:
    f = _num(v)
    return None if f is None else f / M_PER_MILE


def _m_to_ft(v: Any) -> float | None:
    f = _num(v)
    return None if f is None else f * FT_PER_M


# --------------------------------------------------------------------------- #
# Fetchers
# --------------------------------------------------------------------------- #


def fetch_forecast(
    loc: Location, tz: str, *, use_cache: bool, max_age: timedelta
) -> tuple[list[HourData], dict[str, Any], list[tuple[datetime, datetime]], timedelta | None]:
    """Fetch hourly + current weather.

    Returns (hours, current_block, [(sunrise, sunset), ...], cache_age).
    """
    params = {
        "latitude": round(loc.lat, 4),
        "longitude": round(loc.lon, 4),
        "hourly": ",".join(HOURLY_VARS),
        "current": ",".join(CURRENT_VARS),
        "daily": ",".join(DAILY_VARS),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": tz,
        "forecast_days": FORECAST_DAYS,
    }
    res = get_json(FORECAST_URL, params, use_cache=use_cache, max_cache_age=max_age)
    hourly = res.data.get("hourly")
    if not isinstance(hourly, dict):
        raise FetchError("forecast response has no hourly block")
    times = _parse_times(hourly)
    n = len(times)
    s = {name: _series(hourly, name, n) for name in HOURLY_VARS}
    hours: list[HourData] = []
    for i, t in enumerate(times):
        code = _num(s["weather_code"][i])
        hours.append(
            HourData(
                time=t,
                temp_f=_num(s["temperature_2m"][i]),
                heat_index_f=_num(s["apparent_temperature"][i]),
                humidity_pct=_num(s["relative_humidity_2m"][i]),
                precip_prob_pct=_num(s["precipitation_probability"][i]),
                precip_in=_num(s["precipitation"][i]),
                weather_code=None if code is None else int(code),
                cloud_cover_pct=_num(s["cloud_cover"][i]),
                cloud_low_pct=_num(s["cloud_cover_low"][i]),
                cloud_mid_pct=_num(s["cloud_cover_mid"][i]),
                cloud_high_pct=_num(s["cloud_cover_high"][i]),
                dew_point_f=_num(s["dew_point_2m"][i]),
                visibility_mi=_m_to_mi(s["visibility"][i]),
                wind_mph=_num(s["wind_speed_10m"][i]),
                wind_dir=_num(s["wind_direction_10m"][i]),
                gust_mph=_num(s["wind_gusts_10m"][i]),
                wind80_mph=_num(s["wind_speed_80m"][i]),
                wind80_dir=_num(s["wind_direction_80m"][i]),
                wind120_mph=_num(s["wind_speed_120m"][i]),
                uv_index=_num(s["uv_index"][i]),
                cape=_num(s["cape"][i]),
                is_day=_num(s["is_day"][i]) != 0,
            )
        )
    current = res.data.get("current")
    return hours, current if isinstance(current, dict) else {}, _parse_sun(res.data), res.cached_age


def _parse_sun(payload: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    """(sunrise, sunset) pairs from the optional daily block."""
    daily = payload.get("daily")
    if not isinstance(daily, dict):
        return []
    out = []
    for rise, sset in zip(daily.get("sunrise") or [], daily.get("sunset") or []):
        try:
            out.append((datetime.fromisoformat(rise), datetime.fromisoformat(sset)))
        except (TypeError, ValueError):
            continue
    return out


def merge_marine(
    loc: Location,
    hours: list[HourData],
    tz: str,
    *,
    use_cache: bool,
    max_age: timedelta,
) -> tuple[bool, timedelta | None]:
    """Fetch marine data and merge into ``hours`` in place.

    Returns (marine_available, cache_age). ``marine_available`` is False when
    the model has no ocean data for the point (all nulls).
    """
    lat, lon = loc.marine_point
    params = {
        "latitude": round(lat, 4),
        "longitude": round(lon, 4),
        "hourly": ",".join(MARINE_VARS),
        "timezone": tz,
        "forecast_days": FORECAST_DAYS,
    }
    res = get_json(MARINE_URL, params, use_cache=use_cache, max_cache_age=max_age)
    hourly = res.data.get("hourly")
    if not isinstance(hourly, dict):
        raise FetchError("marine response has no hourly block")
    times = _parse_times(hourly)
    n = len(times)
    s = {name: _series(hourly, name, n) for name in MARINE_VARS}
    by_time = {t: i for i, t in enumerate(times)}
    found = False
    for h in hours:
        i = by_time.get(h.time)
        if i is None:
            continue
        h.wave_ft = _m_to_ft(s["wave_height"][i])
        h.wave_dir = _num(s["wave_direction"][i])
        h.wave_period_s = _num(s["wave_period"][i])
        h.wind_wave_ft = _m_to_ft(s["wind_wave_height"][i])
        h.swell_ft = _m_to_ft(s["swell_wave_height"][i])
        h.swell_dir = _num(s["swell_wave_direction"][i])
        h.swell_period_s = _num(s["swell_wave_period"][i])
        found = found or h.wave_ft is not None
    return found, res.cached_age


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in statute miles."""
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_tide_station(
    lat: float, lon: float, max_mi: float, *, use_cache: bool
) -> TideStation | None:
    """Find the nearest NOAA tide-prediction station (station list cached 30 days)."""
    res = get_json(
        NOAA_STATIONS_URL,
        {"type": "tidepredictions"},
        use_cache=use_cache,
        max_cache_age=timedelta(days=30),
        prefer_cache=True,
    )
    best: TideStation | None = None
    for st in res.data.get("stations") or []:
        slat, slon = _num(st.get("lat")), _num(st.get("lng"))
        if slat is None or slon is None or not st.get("id"):
            continue
        d = haversine_mi(lat, lon, slat, slon)
        if d <= max_mi and (best is None or d < (best.distance_mi or 1e9)):
            best = TideStation(str(st["id"]), str(st.get("name", st["id"])), d)
    return best


def fetch_tides(
    station: TideStation, start: datetime, hours: int, *, use_cache: bool
) -> tuple[list[TideEvent], timedelta | None]:
    """Fetch high/low tide predictions starting at ``start`` (local time)."""
    params = {
        "station": station.id,
        "product": "predictions",
        "datum": "MLLW",
        "interval": "hilo",
        "units": "english",
        "time_zone": "lst_ldt",
        "format": "json",
        "begin_date": start.strftime("%Y%m%d %H:%M"),
        "range": max(hours, 1),
        "application": "flWeatherOps",
    }
    res = get_json(
        NOAA_DATA_URL, params, use_cache=use_cache, max_cache_age=timedelta(hours=24)
    )
    events: list[TideEvent] = []
    for p in res.data.get("predictions") or []:
        try:
            t = datetime.strptime(p["t"], "%Y-%m-%d %H:%M")
            v = float(p["v"])
        except (KeyError, TypeError, ValueError):
            continue
        events.append(TideEvent(t, v, "High" if p.get("type") == "H" else "Low"))
    return events, res.cached_age


def geocode(query: str) -> Location:
    """Resolve a place name (e.g. ``"Stuart, FL"``) via Open-Meteo geocoding.

    A trailing ", XX" or ", State" is used to prefer matches in that state
    (defaults to Florida).
    """
    name, _, state = (part.strip() for part in query.partition(","))
    state = state or "FL"
    res = get_json(
        GEOCODE_URL,
        {"name": name, "count": 20, "language": "en", "format": "json", "countryCode": "US"},
        use_cache=True,
        max_cache_age=timedelta(days=30),
        prefer_cache=True,
    )
    results = [r for r in res.data.get("results") or [] if isinstance(r, dict)]
    if not results:
        raise FetchError(f"no geocoding match for '{query}'")
    wanted = _US_STATES.get(state.upper(), state).lower()
    match = next(
        (r for r in results if str(r.get("admin1", "")).lower() == wanted), results[0]
    )
    try:
        lat, lon = float(match["latitude"]), float(match["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FetchError(f"malformed geocoding result for '{query}'") from exc
    label = f"{match.get('name', name)}, {match.get('admin1', '')}".strip(", ")
    key = name.lower().replace(" ", "-")
    return Location(key=key, name=label, lat=lat, lon=lon, coastal=True)


_US_STATES = {"FL": "Florida", "GA": "Georgia", "AL": "Alabama", "SC": "South Carolina"}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def _apply_current(base: HourData, cur: dict[str, Any]) -> HourData:
    """Overlay Open-Meteo ``current`` (15-min) values onto the current hour row."""
    now = dataclasses.replace(base)
    mapping = {
        "temperature_2m": "temp_f",
        "apparent_temperature": "heat_index_f",
        "relative_humidity_2m": "humidity_pct",
        "precipitation": "precip_in",
        "cloud_cover": "cloud_cover_pct",
        "wind_speed_10m": "wind_mph",
        "wind_direction_10m": "wind_dir",
        "wind_gusts_10m": "gust_mph",
    }
    for src, dst in mapping.items():
        v = _num(cur.get(src))
        if v is not None:
            setattr(now, dst, v)
    code = _num(cur.get("weather_code"))
    if code is not None:
        now.weather_code = int(code)
    return now


def load_location(
    loc: Location,
    cfg: dict[str, Any],
    hours_ahead: int,
    *,
    use_cache: bool = True,
    with_marine: bool = True,
    with_tides: bool = True,
) -> LocationData:
    """Fetch and merge all data for ``loc``.

    Raises:
        FetchError: The core forecast could not be obtained. Marine and tide
            failures are reported in ``LocationData.notes`` instead.
    """
    tz = cfg.get("timezone", "America/New_York")
    max_age = timedelta(hours=float(cfg.get("cache", {}).get("max_age_hours", 6)))
    use_cache = use_cache and bool(cfg.get("cache", {}).get("enabled", True))
    notes: list[str] = []

    all_hours, cur_block, sun, age = fetch_forecast(
        loc, tz, use_cache=use_cache, max_age=max_age
    )
    if age is not None:
        notes.append(_age_note("Forecast", age))

    generated = datetime.now(ZoneInfo(tz))
    now = generated.replace(tzinfo=None, minute=0, second=0, microsecond=0)
    window = [h for h in all_hours if h.time >= now][:hours_ahead]
    if not window:
        raise FetchError("forecast contains no future hours (stale data?)")

    marine_ok = False
    if with_marine and loc.coastal:
        try:
            marine_ok, mage = merge_marine(
                loc, window, tz, use_cache=use_cache, max_age=max_age
            )
            if mage is not None:
                notes.append(_age_note("Marine", mage))
            if not marine_ok:
                notes.append("Marine: no wave data for this point (inland or model gap).")
        except FetchError as exc:
            notes.append(f"Marine data unavailable ({exc}); waves not evaluated.")

    station: TideStation | None = None
    tides: list[TideEvent] = []
    tide_cfg = cfg.get("tides", {})
    if with_tides and loc.coastal and tide_cfg.get("enabled", True):
        try:
            if loc.tide_station:
                station = TideStation(str(loc.tide_station), f"NOAA {loc.tide_station}")
            else:
                station = nearest_tide_station(
                    loc.lat,
                    loc.lon,
                    float(tide_cfg.get("max_station_distance_mi", 25)),
                    use_cache=use_cache,
                )
            if station:
                # Start 12 h back so the current tide stage (rising/falling) is known.
                tides, tage = fetch_tides(
                    station,
                    window[0].time - timedelta(hours=12),
                    len(window) + 13,
                    use_cache=use_cache,
                )
                if tage is not None:
                    notes.append(_age_note("Tides", tage))
        except FetchError as exc:
            notes.append(f"Tide data unavailable ({exc}).")

    current = _apply_current(window[0], cur_block)
    return LocationData(
        location=loc,
        current=current,
        hours=window,
        marine_available=marine_ok,
        tide_station=station,
        tides=tides,
        notes=notes,
        generated=generated,
        sun=sun,
    )
