"""Astrophotography conditions: cloud layers, transparency, moon phase and altitude.

Open-Meteo has no moon data, so it is computed here:

* the moon phase uses the mean synodic month;
* the moon's position uses the low-precision lunar series from the Astronomical
  Almanac, accurate to about 1°. That is plenty to tell whether the moon is up.

Each night hour gets a 0-100 score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flweatherops.data import HourData

SYNODIC_DAYS = 29.530588853
KNOWN_NEW_MOON_JD = 2451550.26  # 2000-01-06 18:14 UTC


@dataclass
class MoonInfo:
    """Moon phase/position at an instant."""

    phase: float  # 0 = new, 0.5 = full
    illumination: float  # 0..1
    altitude_deg: float

    @property
    def phase_name(self) -> str:
        p = self.phase
        names = [
            (0.03, "New Moon"), (0.22, "Waxing Crescent"), (0.28, "First Quarter"),
            (0.47, "Waxing Gibbous"), (0.53, "Full Moon"), (0.72, "Waning Gibbous"),
            (0.78, "Last Quarter"), (0.97, "Waning Crescent"), (1.01, "New Moon"),
        ]
        return next(n for limit, n in names if p < limit)

    @property
    def is_up(self) -> bool:
        return self.altitude_deg > 0


@dataclass
class AstroHour:
    """Astro rating for one (night) hour."""

    time: datetime
    score: int
    rating: str
    cloud_pct: float | None
    moon: MoonInfo
    notes: list[str]
    hour: HourData


def julian_day(t_utc: datetime) -> float:
    """Julian day for an aware UTC datetime."""
    return t_utc.timestamp() / 86400.0 + 2440587.5


def moon_info(t_utc: datetime, lat: float, lon: float) -> MoonInfo:
    """Moon phase, illuminated fraction and altitude (degrees) for an observer."""
    jd = julian_day(t_utc)
    phase = ((jd - KNOWN_NEW_MOON_JD) / SYNODIC_DAYS) % 1.0
    illum = (1 - math.cos(2 * math.pi * phase)) / 2

    T = (jd - 2451545.0) / 36525.0
    s = lambda deg: math.sin(math.radians(deg))  # noqa: E731
    lam = (
        218.32 + 481267.881 * T
        + 6.29 * s(135.0 + 477198.87 * T) - 1.27 * s(259.3 - 413335.36 * T)
        + 0.66 * s(235.7 + 890534.22 * T) + 0.21 * s(269.9 + 954397.74 * T)
        - 0.19 * s(357.5 + 35999.05 * T) - 0.11 * s(186.5 + 966404.03 * T)
    )
    beta = (
        5.13 * s(93.3 + 483202.02 * T) + 0.28 * s(228.2 + 960400.89 * T)
        - 0.28 * s(318.3 + 6003.15 * T) - 0.17 * s(217.6 - 407332.21 * T)
    )
    lam_r, beta_r, eps = math.radians(lam), math.radians(beta), math.radians(23.44)
    ra = math.atan2(
        math.sin(lam_r) * math.cos(eps) - math.tan(beta_r) * math.sin(eps), math.cos(lam_r)
    )
    dec = math.asin(
        math.sin(beta_r) * math.cos(eps) + math.cos(beta_r) * math.sin(eps) * math.sin(lam_r)
    )
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360
    ha = math.radians((gmst + lon) % 360) - ra
    phi = math.radians(lat)
    alt = math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(ha))
    return MoonInfo(phase, illum, math.degrees(alt))


def rate_hour(h: HourData, moon: MoonInfo) -> tuple[int, list[str]]:
    """0-100 score: clear, transparent, moonless skies score highest."""
    notes: list[str] = []
    cloud = h.cloud_cover_pct if h.cloud_cover_pct is not None else 50
    score = 100 - cloud
    if (h.cloud_high_pct or 0) >= 50 and cloud < 60:
        score -= 10
        notes.append("high cirrus")
    if moon.is_up:
        penalty = round(45 * moon.illumination)
        score -= penalty
        if penalty >= 10:
            notes.append(f"moon up ({moon.illumination:.0%} lit)")
    if h.visibility_mi is not None and h.visibility_mi < 6:
        score -= 15
        notes.append("haze")
    if (h.humidity_pct or 0) >= 90:
        score -= 10
        notes.append("dew likely")
    if (h.precip_prob_pct or 0) >= 40:
        score -= 15
        notes.append("showers possible")
    return max(0, min(100, round(score))), notes


def rating_label(score: int) -> str:
    return "Excellent" if score >= 75 else "Good" if score >= 55 else "Fair" if score >= 35 else "Poor"


def astro_hours(hours: list[HourData], lat: float, lon: float, tz: str) -> list[AstroHour]:
    """Rate every night hour in ``hours``."""
    zone = ZoneInfo(tz)
    out = []
    for h in hours:
        if h.is_day:
            continue
        moon = moon_info(h.time.replace(tzinfo=zone).astimezone(timezone.utc), lat, lon)
        score, notes = rate_hour(h, moon)
        out.append(AstroHour(h.time, score, rating_label(score), h.cloud_cover_pct, moon, notes, h))
    return out


def moon_track(hours: list[HourData], lat: float, lon: float, tz: str) -> list[tuple[datetime, float]]:
    """(local time, moon altitude °) for every hour, day and night (for charts)."""
    zone = ZoneInfo(tz)
    return [
        (h.time, moon_info(h.time.replace(tzinfo=zone).astimezone(timezone.utc), lat, lon).altitude_deg)
        for h in hours
    ]


def best_astro_block(rated: list[AstroHour], min_score: int = 55) -> list[AstroHour]:
    """Longest run of consecutive night hours scoring at least ``min_score``."""
    best: list[AstroHour] = []
    run: list[AstroHour] = []
    for a in rated:
        contiguous = run and (a.time - run[-1].time).total_seconds() == 3600
        if a.score >= min_score and (not run or contiguous):
            run.append(a)
        else:
            run = [a] if a.score >= min_score else []
        if len(run) > len(best) or (len(run) == len(best) and run and
                                     sum(x.score for x in run) > sum(x.score for x in best)):
            best = list(run)
    return best
