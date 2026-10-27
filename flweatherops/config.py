"""Default locations and safety thresholds, plus optional ``config.json`` loading.

All thresholds are stored in *internal* units regardless of display preference:

* wind: mph
* waves: feet
* visibility: statute miles
* precipitation: inches per hour
* temperature / heat index: °F
* CAPE: J/kg
* distances: statute miles

Each numeric rule has a ``caution`` and a ``nogo`` level. For "max" rules
(wind, gusts, waves, precip...) a value **at or above** the level triggers it.
For "min" rules (visibility) a value **below** the level triggers it.
Set a level to ``null``/``None`` to disable it.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "timezone": "America/New_York",
    "locations": {
        "boynton-beach": {
            "name": "Boynton Beach, FL",
            "lat": 26.5453,
            "lon": -80.0436,
            "marine_lat": 26.545,
            "marine_lon": -80.02,
            "coastal": True,
            # Direction from the beach toward open water (east coast = 90°).
            "shore_facing_deg": 90,
            "default": True,
        },
        "palm-beach": {
            "name": "Palm Beach / Lake Worth Inlet, FL",
            "lat": 26.7720,
            "lon": -80.0370,
            "marine_lat": 26.77,
            "marine_lon": -80.01,
            "coastal": True,
            "shore_facing_deg": 90,
            "default": False,
        },
        "vero-beach": {
            "name": "Vero Beach, FL",
            "lat": 27.6386,
            "lon": -80.3973,
            # Marine point slightly offshore so the wave model has an ocean cell.
            "marine_lat": 27.64,
            "marine_lon": -80.34,
            "coastal": True,
            "shore_facing_deg": 80,
            "default": True,
        },
        "sebastian-inlet": {
            "name": "Sebastian Inlet, FL",
            "lat": 27.8603,
            "lon": -80.4473,
            "marine_lat": 27.86,
            "marine_lon": -80.40,
            "coastal": True,
            "shore_facing_deg": 70,
            "default": False,
        },
        "fort-pierce": {
            "name": "Fort Pierce Inlet, FL",
            "lat": 27.4706,
            "lon": -80.2948,
            "marine_lat": 27.47,
            "marine_lon": -80.26,
            "coastal": True,
            "shore_facing_deg": 75,
            "default": False,
        },
        "okeechobee": {
            "name": "Okeechobee, FL (inland)",
            "lat": 27.2439,
            "lon": -80.8298,
            "coastal": False,
            "default": False,
        },
    },
    "thresholds": {
        "drone": {
            "wind_speed_mph": {"caution": 12, "nogo": 18},
            "wind_gust_mph": {"caution": 18, "nogo": 25},
            # Max of the 80 m / 120 m (~400 ft AGL) model winds.
            "wind_aloft_mph": {"caution": 18, "nogo": 26},
            "precip_prob_pct": {"caution": 25, "nogo": 50},
            "precip_in": {"caution": 0.01, "nogo": 0.03},
            # Part 107 requires 3 SM visibility.
            "visibility_mi": {"caution": 5, "nogo": 3},
            "heat_index_f": {"caution": 100, "nogo": 108},
        },
        "marine": {
            "wind_speed_mph": {"caution": 15, "nogo": 21},
            "wind_gust_mph": {"caution": 20, "nogo": 28},
            "wave_height_ft": {"caution": 3, "nogo": 5},
            "precip_prob_pct": {"caution": 40, "nogo": 70},
            "precip_in": {"caution": 0.05, "nogo": 0.25},
            "visibility_mi": {"caution": 3, "nogo": 1},
            "heat_index_f": {"caution": 103, "nogo": 110},
        },
        # Lightning proxy (applies to both activities). Thunderstorm weather
        # codes (95-99) are always No-Go.
        "lightning": {
            "cape_caution": 1000,
            "cape_nogo": 2500,
            "precip_prob_min": 30,
        },
    },
    # Practical-advice tuning (does not change Safe/Caution/No-Go).
    "advice": {
        # LiPo packs are happiest around 68-86 °F; outside this range we add notes.
        "lipo_cool_f": 60,
        "lipo_hot_f": 95,
        # Gust spread (gust - sustained) that suggests turbulence.
        "gust_spread_mph": 10,
        # Sub-250 g drones are rated for roughly this much wind (DJI Mini "Level 5").
        "light_drone_max_wind_mph": 22,
    },
    # Safe Window Finder.
    "windows": {
        "min_hours": 2,
        "scan_hours": 72,
        "drone_daylight_only": True,
    },
    # NHC tropical-cyclone awareness. Each hour is checked against the storm's
    # (interpolated) forecast position.
    "tropical": {
        "enabled": True,
        "caution_radius_mi": 400,
        "nogo_radius_mi": 200,
        # Storms beyond this are listed but ignored for the safety assessment.
        "list_radius_mi": 1500,
        "lookahead_hours": 120,
        # A hurricane/TS *warning* naming your area -> No-Go; a *watch* -> Caution.
        "warning_status": "nogo",
        "watch_status": "caution",
    },
    "history": {
        "enabled": True,
        # Don't log the same location more often than this (dashboard reruns).
        "min_interval_min": 30,
    },
    "cache": {
        "enabled": True,
        # Cached data is only used as a fallback when the live request fails.
        "max_age_hours": 6,
    },
    "tides": {
        "enabled": True,
        # Search radius for the nearest NOAA tide-prediction station.
        "max_station_distance_mi": 25,
    },
}


@dataclass
class Location:
    """A place to brief."""

    key: str
    name: str
    lat: float
    lon: float
    coastal: bool = True
    marine_lat: float | None = None
    marine_lon: float | None = None
    tide_station: str | None = None
    shore_facing_deg: float | None = None
    default: bool = False

    @property
    def marine_point(self) -> tuple[float, float]:
        """Coordinates used for the marine (wave) model."""
        return (
            self.marine_lat if self.marine_lat is not None else self.lat,
            self.marine_lon if self.marine_lon is not None else self.lon,
        )

    @classmethod
    def from_dict(cls, key: str, data: dict[str, Any]) -> "Location":
        """Build a Location from a config entry."""
        return cls(
            key=key,
            name=str(data.get("name", key)),
            lat=float(data["lat"]),
            lon=float(data["lon"]),
            coastal=bool(data.get("coastal", True)),
            marine_lat=_opt_float(data.get("marine_lat")),
            marine_lon=_opt_float(data.get("marine_lon")),
            tide_station=data.get("tide_station"),
            shore_facing_deg=_opt_float(data.get("shore_facing_deg")),
            default=bool(data.get("default", False)),
        )


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def default_config_path() -> Path | None:
    """Return the first existing config file among the standard locations."""
    candidates = [
        Path.cwd() / "config.json",
        Path.home() / ".config" / "flweatherops" / "config.json",
    ]
    return next((p for p in candidates if p.is_file()), None)


def load_config(path: Path | None = None) -> dict[str, Any]:
    """Load defaults merged with an optional JSON config file.

    Locations in the user file are merged by key, so you can tweak one
    threshold or add one location without repeating everything.
    """
    path = path or default_config_path()
    if path is None:
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        user = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Could not read config {path}: {exc}") from exc
    if not isinstance(user, dict):
        raise ConfigError(f"Config {path} must contain a JSON object")
    return _deep_merge(DEFAULT_CONFIG, user)


def locations_from_config(cfg: dict[str, Any]) -> dict[str, Location]:
    """Parse the ``locations`` section into Location objects."""
    out: dict[str, Location] = {}
    for key, data in cfg.get("locations", {}).items():
        try:
            out[key] = Location.from_dict(key, data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"Invalid location '{key}': {exc}") from exc
    return out


class ConfigError(Exception):
    """Raised for unreadable or invalid configuration."""
