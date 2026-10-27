"""Display-unit conversion and formatting helpers."""

from __future__ import annotations

from datetime import datetime

MPH_PER_KNOT = 1.150779

COMPASS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def wind(mph: float | None, units: str) -> str:
    """Format a wind speed held in mph using the display unit ('mph' or 'kn')."""
    if mph is None:
        return "—"
    if units == "kn":
        return f"{mph / MPH_PER_KNOT:.0f}"
    return f"{mph:.0f}"


def wind_unit(units: str) -> str:
    """Label for the display wind unit."""
    return "kt" if units == "kn" else "mph"


def compass(deg: float | None) -> str:
    """Convert a bearing to a 16-point compass label (direction *from*)."""
    if deg is None:
        return "—"
    return COMPASS[int((deg % 360) / 22.5 + 0.5) % 16]


def num(value: float | None, fmt: str = "{:.0f}", suffix: str = "") -> str:
    """Format an optional number."""
    return "—" if value is None else fmt.format(value) + suffix


def hour_label(dt: datetime, with_day: bool = False) -> str:
    """'3 PM' (or 'Tue 3 PM') style label, portable across platforms."""
    label = dt.strftime("%I %p").lstrip("0")
    return f"{dt.strftime('%a')} {label}" if with_day else label
