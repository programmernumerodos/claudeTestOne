"""Tropical / hurricane awareness from public NHC products.

Data sources (no API key):

* ``CurrentStorms.json`` lists every active storm with its current position,
  intensity and motion, plus links to its text advisories.
* The **forecast advisory** (TCM) has the official 5-day forecast positions
  (``FORECAST VALID 28/0000Z 26.5N 79.0W``). These are parsed into a track.
* The **public advisory** (TCP) has a "SUMMARY OF WATCHES AND WARNINGS" section.
  It is checked against your location.

For each forecast hour, the storm's position is linearly interpolated along the
track, and its distance from the location gives a CAUTION / NO-GO reason. This
is a simple distance check, not a wind-field or cone model.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from flweatherops.config import Location
from flweatherops.data import FetchError, HourData, get_json, get_text, haversine_mi
from flweatherops.safety import Reason, Status

NHC_CURRENT_URL = "https://www.nhc.noaa.gov/CurrentStorms.json"

CLASS_NAMES = {
    "HU": "Hurricane",
    "TS": "Tropical Storm",
    "TD": "Tropical Depression",
    "STS": "Subtropical Storm",
    "STD": "Subtropical Depression",
    "PTC": "Potential Tropical Cyclone",
    "PC": "Post-Tropical Cyclone",
    "TC": "Tropical Cyclone",
}

# Approximate latitudes of NHC Florida east-coast watch/warning breakpoints,
# used to decide whether a segment like "Jupiter Inlet to Sebastian Inlet"
# covers a location. Elsewhere, matching falls back to keywords from the name.
EAST_COAST_BREAKPOINTS = {
    "card sound bridge": 25.29,
    "ocean reef": 25.31,
    "golden beach": 25.97,
    "hallandale beach": 25.98,
    "deerfield beach": 26.32,
    "boca raton": 26.36,
    "jupiter inlet": 26.94,
    "st. lucie inlet": 27.17,
    "fort pierce": 27.47,
    "sebastian inlet": 27.86,
    "port canaveral": 28.41,
    "flagler/volusia county line": 29.42,
    "ponte vedra beach": 30.24,
    "st. marys river": 30.71,
    "altamaha sound": 31.32,
}
EAST_COAST_MIN_LON = -80.9  # east of this counts as Atlantic-facing Florida

_FCST_RE = re.compile(
    r"(?:FORECAST|OUTLOOK) VALID (\d{2})/(\d{2})(\d{2})Z\s+"
    r"(\d+(?:\.\d+)?)([NS])\s+(\d+(?:\.\d+)?)([EW])"
)
_MAXWIND_RE = re.compile(r"MAX WIND\s+(\d+)\s*KT")
_WW_START_RE = re.compile(r"SUMMARY OF WATCHES AND WARNINGS IN EFFECT:?", re.I)
_WW_END_RE = re.compile(
    r"(DISCUSSION AND OUTLOOK|For storm information specific to your area|"
    r"Interests elsewhere|HAZARDS AFFECTING LAND)",
    re.I,
)
_ALERT_HEADER_RE = re.compile(
    r"An?\s+(Hurricane|Tropical Storm|Storm Surge)\s+(Warning|Watch)\s+is in effect for",
    re.I,
)


@dataclass
class TrackPoint:
    """A forecast position (UTC)."""

    time: datetime
    lat: float
    lon: float
    max_wind_kt: int | None = None


@dataclass
class Alert:
    """One watch/warning type and the coastal segments it covers."""

    kind: str  # e.g. "Hurricane Warning"
    areas: list[str]

    @property
    def is_warning(self) -> bool:
        return self.kind.lower().endswith("warning")


@dataclass
class Storm:
    """An active NHC system."""

    id: str
    name: str
    classification: str
    lat: float
    lon: float
    intensity_kt: int | None = None
    pressure_mb: int | None = None
    movement_dir: float | None = None
    movement_speed_mph: float | None = None
    last_update: datetime | None = None
    track: list[TrackPoint] = field(default_factory=list)
    track_source: str = "none"  # "forecast advisory" | "extrapolated" | "none"
    alerts: list[Alert] = field(default_factory=list)
    alerts_text: str = ""
    public_advisory_url: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def class_name(self) -> str:
        return CLASS_NAMES.get(self.classification, self.classification or "System")

    @property
    def title(self) -> str:
        return f"{self.class_name} {self.name}".strip()

    @property
    def category(self) -> int | None:
        """Saffir-Simpson category for hurricanes."""
        kt = self.intensity_kt
        if kt is None or kt < 64:
            return None
        return 1 if kt < 83 else 2 if kt < 96 else 3 if kt < 113 else 4 if kt < 137 else 5

    @property
    def intensity_text(self) -> str:
        if self.intensity_kt is None:
            return "intensity n/a"
        mph = round(self.intensity_kt * 1.15078)
        cat = f" (Cat {self.category})" if self.category else ""
        return f"{self.intensity_kt} kt / {mph} mph{cat}"

    def position_at(self, t_utc: datetime) -> tuple[float, float] | None:
        """Interpolated position at ``t_utc`` (None beyond the forecast track)."""
        pts = [TrackPoint(self.last_update or t_utc, self.lat, self.lon, self.intensity_kt)]
        pts += [p for p in self.track if p.time > pts[0].time]
        if t_utc <= pts[0].time:
            return pts[0].lat, pts[0].lon
        for a, b in zip(pts, pts[1:]):
            if a.time <= t_utc <= b.time:
                f = (t_utc - a.time) / (b.time - a.time)
                return a.lat + f * (b.lat - a.lat), a.lon + f * (b.lon - a.lon)
        return None


@dataclass
class TropicalReport:
    """Result of one NHC check."""

    ok: bool
    storms: list[Storm] = field(default_factory=list)
    error: str | None = None
    cached_age: timedelta | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class StormImpact:
    """How one storm relates to one location."""

    storm: Storm
    distance_now_mi: float
    closest_mi: float
    closest_time: datetime | None  # UTC
    level: Status
    alerts: list[Alert]  # watch/warnings that appear to cover the location
    summary: str


@dataclass
class LocationTropical:
    """Tropical assessment for a location over its forecast hours."""

    report: TropicalReport
    impacts: list[StormImpact]
    hour_reasons: list[list[Reason]]  # aligned with the evaluated hours
    florida_alerts: list[tuple[Storm, Alert]]

    @property
    def level(self) -> Status:
        return max((i.level for i in self.impacts), default=Status.SAFE)

    @property
    def relevant(self) -> list[StormImpact]:
        return [i for i in self.impacts if i.level > Status.SAFE]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def _f(v: Any) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _parse_latlon(text: Any) -> float | None:
    """'25.3N' -> 25.3, '76.9W' -> -76.9."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([NSEW])\s*", str(text or ""))
    if not m:
        return _f(text)
    v = float(m.group(1))
    return -v if m.group(2) in "SW" else v


def _parse_time(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def strip_html(text: str) -> str:
    """Extract the advisory body from an NHC .shtml page (or pass plain text)."""
    m = re.search(r"<pre[^>]*>(.*?)</pre>", text, re.S | re.I)
    body = m.group(1) if m else text
    return html.unescape(re.sub(r"<[^>]+>", "", body))


def parse_forecast_track(text: str, issued: datetime) -> list[TrackPoint]:
    """Forecast positions from a TCM forecast advisory.

    The day-of-month in ``DD/HHMMZ`` is resolved relative to ``issued``
    (rolling into the next month when the day is smaller).
    """
    points: list[TrackPoint] = []
    matches = list(_FCST_RE.finditer(text))
    for i, m in enumerate(matches):
        day, hh, mm = int(m.group(1)), int(m.group(2)), int(m.group(3))
        year, month = issued.year, issued.month
        if day < issued.day - 1:
            month += 1
            if month == 13:
                year, month = year + 1, 1
        try:
            t = datetime(year, month, day, hh, mm, tzinfo=timezone.utc)
        except ValueError:
            continue
        lat = float(m.group(4)) * (-1 if m.group(5) == "S" else 1)
        lon = float(m.group(6)) * (-1 if m.group(7) == "W" else 1)
        seg_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        w = _MAXWIND_RE.search(text, m.end(), seg_end)
        points.append(TrackPoint(t, lat, lon, int(w.group(1)) if w else None))
    return sorted(points, key=lambda p: p.time)


def parse_alerts(text: str) -> tuple[list[Alert], str]:
    """Watches/warnings from a public advisory. Returns (alerts, raw section)."""
    start = _WW_START_RE.search(text)
    if not start:
        return [], ""
    end = _WW_END_RE.search(text, start.end())
    section = text[start.end(): end.start() if end else len(text)].strip()
    alerts: list[Alert] = []
    headers = list(_ALERT_HEADER_RE.finditer(section))
    for i, h in enumerate(headers):
        body = section[h.end(): headers[i + 1].start() if i + 1 < len(headers) else len(section)]
        areas = [
            re.sub(r"\s+", " ", ln.strip(" *\t")).strip()
            for ln in body.splitlines()
            if ln.strip().startswith("*")
        ]
        kind = f"{h.group(1).title()} {h.group(2).title()}"
        alerts.append(Alert(kind, [a for a in areas if a]))
    return alerts, section


def _extrapolate(storm: Storm, hours: int = 72) -> list[TrackPoint]:
    """Dead-reckon from current motion when no forecast track is available."""
    if storm.movement_dir is None or not storm.movement_speed_mph or storm.last_update is None:
        return []
    pts = []
    rad = math.radians(storm.movement_dir)  # direction of travel (toward)
    for h in range(12, hours + 1, 12):
        dist = storm.movement_speed_mph * h
        dlat = dist * math.cos(rad) / 69.0
        dlon = dist * math.sin(rad) / (69.0 * max(math.cos(math.radians(storm.lat)), 0.2))
        pts.append(TrackPoint(storm.last_update + timedelta(hours=h), storm.lat + dlat, storm.lon + dlon))
    return pts


def _storm_from_json(d: dict[str, Any]) -> Storm | None:
    lat = _f(d.get("latitudeNumeric"))
    lon = _f(d.get("longitudeNumeric"))
    lat = lat if lat is not None else _parse_latlon(d.get("latitude"))
    lon = lon if lon is not None else _parse_latlon(d.get("longitude"))
    if lat is None or lon is None:
        return None
    inten, pres = _f(d.get("intensity")), _f(d.get("pressure"))
    pa = d.get("publicAdvisory") if isinstance(d.get("publicAdvisory"), dict) else {}
    return Storm(
        id=str(d.get("id", "")),
        name=str(d.get("name", "Unnamed")),
        classification=str(d.get("classification", "")),
        lat=lat,
        lon=lon,
        intensity_kt=None if inten is None else int(inten),
        pressure_mb=None if pres is None else int(pres),
        movement_dir=_f(d.get("movementDir")),
        movement_speed_mph=_f(d.get("movementSpeed")),
        last_update=_parse_time(d.get("lastUpdate")),
        public_advisory_url=pa.get("url"),
    )


def fetch_tropical(*, use_cache: bool = True, details: bool = True) -> TropicalReport:
    """Fetch active Atlantic systems from NHC (never raises).

    Args:
        use_cache: Use the on-disk cache as a fallback.
        details: Also fetch forecast/public advisories (track + watches/warnings).
    """
    try:
        res = get_json(NHC_CURRENT_URL, {}, use_cache=use_cache, max_cache_age=timedelta(hours=6))
    except FetchError as exc:
        return TropicalReport(ok=False, error=f"NHC unreachable ({exc})")

    storms: list[Storm] = []
    for d in res.data.get("activeStorms") or []:
        if not isinstance(d, dict):
            continue
        sid = str(d.get("id", "")).lower()
        if sid and not sid.startswith("al"):
            continue  # Atlantic basin only
        storm = _storm_from_json(d)
        if storm is None:
            continue
        if details:
            _add_details(storm, d, use_cache)
        storms.append(storm)
    return TropicalReport(ok=True, storms=storms, cached_age=res.cached_age)


def _add_details(storm: Storm, d: dict[str, Any], use_cache: bool) -> None:
    fa = d.get("forecastAdvisory") if isinstance(d.get("forecastAdvisory"), dict) else {}
    if fa.get("url"):
        try:
            text, _ = get_text(fa["url"], use_cache=use_cache)
            storm.track = parse_forecast_track(
                strip_html(text), storm.last_update or datetime.now(timezone.utc)
            )
            if storm.track:
                storm.track_source = "forecast advisory"
        except FetchError as exc:
            storm.notes.append(f"forecast advisory unavailable ({exc})")
    if not storm.track:
        storm.track = _extrapolate(storm)
        if storm.track:
            storm.track_source = "extrapolated"
    if storm.public_advisory_url:
        try:
            text, _ = get_text(storm.public_advisory_url, use_cache=use_cache)
            storm.alerts, storm.alerts_text = parse_alerts(strip_html(text))
        except FetchError as exc:
            storm.notes.append(f"public advisory unavailable ({exc})")


# --------------------------------------------------------------------------- #
# Assessment
# --------------------------------------------------------------------------- #


def _segment_covers(area: str, loc: Location) -> bool:
    """Does a watch/warning segment text appear to include ``loc``?"""
    text = area.lower()
    name_key = loc.name.split(",")[0].split("/")[0].strip().lower()
    if name_key and name_key in text:
        return True
    if "lake okeechobee" in text and "okeechobee" in loc.name.lower():
        return True
    if loc.lon < EAST_COAST_MIN_LON or not loc.coastal:
        return False
    for word in ("north of ", "south of ", " northward", " southward"):
        text = text.replace(word, "")
    m = re.match(r"(.+?)\s+to\s+(?:the\s+)?(.+?)(?:,.*)?$", text)
    if not m:
        return False
    a = EAST_COAST_BREAKPOINTS.get(m.group(1).strip(" ."))
    b = EAST_COAST_BREAKPOINTS.get(m.group(2).strip(" ."))
    if a is None or b is None:
        return False
    return min(a, b) <= loc.lat <= max(a, b)


def _mentions_florida(alert: Alert) -> bool:
    return any(
        "florida" in a.lower()
        or any(bp in a.lower() for bp in EAST_COAST_BREAKPOINTS)
        or "keys" in a.lower()
        for a in alert.areas
    )


def assess_location(
    report: TropicalReport,
    loc: Location,
    hours: list[HourData],
    cfg: dict[str, Any],
) -> LocationTropical:
    """Distance/alert assessment of every storm for ``loc`` and each forecast hour."""
    tcfg = cfg.get("tropical", {})
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))
    caution_r = float(tcfg.get("caution_radius_mi", 400))
    nogo_r = float(tcfg.get("nogo_radius_mi", 200))
    list_r = float(tcfg.get("list_radius_mi", 1500))
    lookahead = timedelta(hours=float(tcfg.get("lookahead_hours", 120)))
    level_of = {"nogo": Status.NOGO, "caution": Status.CAUTION, "safe": Status.SAFE}
    warn_level = level_of.get(str(tcfg.get("warning_status", "nogo")).lower(), Status.NOGO)
    watch_level = level_of.get(str(tcfg.get("watch_status", "caution")).lower(), Status.CAUTION)

    hour_reasons: list[list[Reason]] = [[] for _ in hours]
    impacts: list[StormImpact] = []
    fl_alerts: list[tuple[Storm, Alert]] = []
    now_utc = datetime.now(timezone.utc)

    for storm in report.storms:
        d_now = haversine_mi(loc.lat, loc.lon, storm.lat, storm.lon)
        # Closest approach over the track within the lookahead.
        closest, closest_t = d_now, storm.last_update
        for p in storm.track:
            if p.time - now_utc > lookahead:
                break
            d = haversine_mi(loc.lat, loc.lon, p.lat, p.lon)
            if d < closest:
                closest, closest_t = d, p.time
        if closest > list_r:
            continue

        my_alerts = [
            Alert(a.kind, [s for s in a.areas if _segment_covers(s, loc)]) for a in storm.alerts
        ]
        my_alerts = [a for a in my_alerts if a.areas]
        fl_alerts += [(storm, a) for a in storm.alerts if _mentions_florida(a)]

        level = Status.SAFE
        if closest <= nogo_r:
            level = Status.NOGO
        elif closest <= caution_r:
            level = Status.CAUTION
        for a in my_alerts:
            level = max(level, warn_level if a.is_warning else watch_level)

        # Per-hour reasons.
        for i, h in enumerate(hours):
            t_utc = h.time.replace(tzinfo=tz).astimezone(timezone.utc)
            pos = storm.position_at(t_utc)
            if pos is not None:
                d = haversine_mi(loc.lat, loc.lon, *pos)
                lvl = Status.NOGO if d <= nogo_r else Status.CAUTION if d <= caution_r else None
                if lvl is not None:
                    hour_reasons[i].append(
                        Reason("Tropical", lvl, note=f"{storm.title} ~{d:.0f} mi away")
                    )
            for a in my_alerts:
                lvl = warn_level if a.is_warning else watch_level
                if lvl > Status.SAFE:
                    hour_reasons[i].append(Reason("Tropical", lvl, note=f"{a.kind} ({storm.name})"))

        when = ""
        if closest_t and closest < d_now - 1:
            when = f", closest ~{closest:.0f} mi around {closest_t.astimezone(tz):%a %H:%M}"
        summary = (
            f"{storm.title}: {storm.intensity_text}, {d_now:.0f} mi away now{when}."
        )
        if my_alerts:
            summary += " " + "; ".join(f"{a.kind} appears to include this area" for a in my_alerts) + "."
        impacts.append(StormImpact(storm, d_now, closest, closest_t, level, my_alerts, summary))

    impacts.sort(key=lambda i: (-int(i.level), i.closest_mi))
    return LocationTropical(report, impacts, hour_reasons, fl_alerts)
