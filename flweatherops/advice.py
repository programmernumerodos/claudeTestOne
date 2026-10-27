"""Practical, activity-specific advice for drone pilots and boaters.

These notes add context such as "gusts are rising", "outgoing tide against
onshore wind" and "LiPo packs run hot". They do not change the Safe/Caution/No-Go
verdicts, which come only from safety.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from flweatherops import units as U
from flweatherops.data import HourData, LocationData

# Look this far ahead (hours) for trend and storm notes.
TREND_HOURS = 3
STORM_LOOKAHEAD_HOURS = 8


@dataclass
class Advice:
    """One piece of advice. ``level`` is 'info', 'warn' or 'alert'."""

    topic: str
    text: str
    level: str = "info"


def _t(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _wind(mph: float | None, units: str) -> str:
    return f"{U.wind(mph, units)} {U.wind_unit(units)}"


def _upcoming(data: LocationData, n: int) -> list[HourData]:
    return data.hours[1 : n + 1]


def _gust_trend(data: LocationData, units: str, audience: str) -> Advice | None:
    now = data.current.gust_mph
    ahead = [h for h in _upcoming(data, TREND_HOURS) if h.gust_mph is not None]
    if now is None or not ahead:
        return None
    peak = max(ahead, key=lambda h: h.gust_mph or 0)
    if (peak.gust_mph or 0) - now < 5:
        return None
    tail = (
        "plan shorter flights and land early, or bring a heavier airframe."
        if audience == "drone"
        else "plan the ride back (often into the wind) before it builds."
    )
    return Advice(
        "Wind trend",
        f"Gusts rising from {_wind(now, units)} to {_wind(peak.gust_mph, units)} by "
        f"{_t(peak.time)}. {tail[0].upper()}{tail[1:]}",
        "warn",
    )


def _storm_risk(data: LocationData, cfg: dict[str, Any], audience: str) -> Advice | None:
    """High CAPE + rain chance in the next few hours = elevated lightning risk."""
    lc = cfg["thresholds"].get("lightning", {})
    cape_c = float(lc.get("cape_caution") or 1000)
    pp_min = float(lc.get("precip_prob_min") or 30)
    window = data.hours[: STORM_LOOKAHEAD_HOURS + 1]
    risky = [
        h for h in window
        if (h.weather_code in (95, 96, 99))
        or ((h.cape or 0) >= cape_c and (h.precip_prob_pct or 0) >= pp_min)
    ]
    if not risky:
        return None
    first = risky[0]
    peak_cape = max((h.cape or 0) for h in risky)
    action = (
        "Land and pack up at the first thunder (30/30 rule)."
        if audience == "drone"
        else "Be off the water before then; on a boat you are the tallest object around."
    )
    when = "now" if first is data.hours[0] else f"from about {_t(first.time)}"
    return Advice(
        "Lightning",
        f"Elevated lightning risk {when}: CAPE up to {peak_cape:.0f} J/kg with "
        f"{first.precip_prob_pct or 0:.0f}% rain chance. Florida storms build fast. {action}",
        "alert",
    )


# --------------------------------------------------------------------------- #
# Drone
# --------------------------------------------------------------------------- #


def drone_advice(data: LocationData, cfg: dict[str, Any], units: str = "mph") -> list[Advice]:
    """Advice for drone/FPV flying, based on current + near-term hours."""
    acfg = cfg.get("advice", {})
    h = data.current
    out: list[Advice] = []

    # Wind at altitude
    if h.wind80_mph is not None or h.wind120_mph is not None:
        surface = h.wind_mph or 0
        aloft = h.wind_aloft_mph or 0
        ratio = aloft / surface if surface >= 3 else None
        text = (
            f"Estimated wind aloft: ~260 ft (80 m) {_wind(h.wind80_mph, units)} "
            f"from {U.compass(h.wind80_dir)}, ~400 ft (120 m) {_wind(h.wind120_mph, units)}."
        )
        level = "info"
        if ratio and ratio >= 1.5:
            text += (
                f" That is {ratio:.1f}× the surface wind. Fly upwind first so the trip "
                "home is downwind, and keep extra battery margin for RTH."
            )
            level = "warn"
        limit = cfg["thresholds"]["drone"].get("wind_aloft_mph", {}).get("caution")
        if limit is not None and aloft >= limit:
            level = "warn"
        out.append(Advice("Wind aloft", text, level))

    # Gustiness
    spread = float(acfg.get("gust_spread_mph", 10))
    if h.gust_mph is not None and h.wind_mph is not None and h.gust_mph - h.wind_mph >= spread:
        out.append(Advice(
            "Turbulence",
            f"Gusty: gusts {_wind(h.gust_mph - h.wind_mph, units)} above sustained. "
            "Expect sudden drift. Keep wide margins from trees, buildings and people.",
            "warn",
        ))
    if a := _gust_trend(data, units, "drone"):
        out.append(a)

    light_max = acfg.get("light_drone_max_wind_mph")
    worst = max(v for v in (h.gust_mph, h.wind_aloft_mph, 0) if v is not None)
    if light_max is not None and worst >= float(light_max):
        out.append(Advice(
            "Airframe",
            f"Gusts/aloft up to {_wind(worst, units)} exceed what sub-250 g drones are "
            f"rated for (~{_wind(float(light_max), units)}). A heavier drone is recommended.",
            "warn",
        ))

    # Battery
    if h.temp_f is not None:
        hot, cool = float(acfg.get("lipo_hot_f", 95)), float(acfg.get("lipo_cool_f", 60))
        if h.temp_f >= hot:
            out.append(Advice(
                "Battery",
                f"{h.temp_f:.0f}°F: LiPo packs and motors run hot. Fly shorter, keep packs "
                "shaded (not in a closed car), and let them cool before recharging.",
                "warn",
            ))
        elif h.temp_f < cool:
            out.append(Advice(
                "Battery",
                f"{h.temp_f:.0f}°F: cool packs sag under load. Keep them warm (~68°F+) until "
                "takeoff, and hover for a minute before pushing hard.",
                "warn",
            ))
        else:
            out.append(Advice(
                "Battery",
                f"{h.temp_f:.0f}°F is fine for LiPo packs (ideal is 68–86°F).",
            ))
    if h.temp_f is not None and h.dew_point_f is not None and h.temp_f - h.dew_point_f <= 4:
        out.append(Advice(
            "Condensation",
            "Air is near its dew point. Lenses and gimbal will fog, especially coming out "
            "of an air-conditioned car. Let gear acclimate for 10–15 min.",
            "warn",
        ))

    # Visibility / clouds for VLOS & FPV
    if h.visibility_mi is not None and h.visibility_mi < 5:
        out.append(Advice(
            "Visibility",
            f"Visibility {h.visibility_mi:.1f} mi (haze, fog or showers). VLOS is harder; "
            "FPV video links may break up sooner. Stay close.",
            "warn",
        ))
    if (h.cloud_low_pct or 0) >= 70:
        out.append(Advice(
            "Ceiling",
            f"Low cloud cover {h.cloud_low_pct:.0f}%: the cloud base may be low. "
            "Keep well below it and stay in visual line of sight.",
            "warn",
        ))
    if (h.uv_index or 0) >= 8:
        out.append(Advice(
            "Sun", f"UV {h.uv_index:.0f}: screen glare is likely. Use a sunshade and sunscreen."
        ))
    if a := _storm_risk(data, cfg, "drone"):
        out.append(a)
    return out


# --------------------------------------------------------------------------- #
# Marine
# --------------------------------------------------------------------------- #


def describe_sea(height_ft: float | None, period_s: float | None) -> str | None:
    """Plain-language wave height + period interpretation for PWC/small boats."""
    if height_ft is None:
        return None
    height_ft = round(height_ft, 1)  # classify what the user sees displayed
    if height_ft < 1:
        text = "Flat to light ripple. Ideal for Sea-Doo, paddling and fishing."
    elif height_ft < 2:
        text = "Light chop. Comfortable for PWC and small boats."
    elif height_ft < 3:
        text = "Moderate chop. Fine for experienced PWC riders; bumpy and wet in small boats."
    elif height_ft < 5:
        text = "Rough. PWC and small boats should stay inshore or in the Intracoastal; inlets can be dangerous."
    else:
        text = "Very rough. Stay off the ocean in a PWC or small boat."
    if period_s is not None:
        if period_s < 6:
            text += f" Short period ({period_s:.0f}s): steep, tightly spaced chop that rides rougher than the height suggests."
        elif period_s >= 10:
            text += f" Long-period swell ({period_s:.0f}s): smoother offshore, but it breaks hard on inlet bars and sandbars."
        else:
            text += f" Moderate period ({period_s:.0f}s)."
    return text


def shore_relation(wind_from_deg: float | None, shore_facing_deg: float | None) -> str | None:
    """'onshore' / 'offshore' / 'cross-shore' given the beach's seaward bearing."""
    if wind_from_deg is None or shore_facing_deg is None:
        return None
    diff = abs((wind_from_deg - shore_facing_deg + 180) % 360 - 180)
    if diff <= 45:
        return "onshore"
    if diff >= 135:
        return "offshore"
    return "cross-shore"


def tide_stage(data: LocationData, at: datetime) -> tuple[str, Any, Any] | None:
    """('rising'|'falling', previous event, next event) at ``at``."""
    nxt, prv = data.next_tide(at), data.prev_tide(at)
    if nxt is None:
        return None
    return ("falling" if nxt.kind == "Low" else "rising"), prv, nxt


def marine_advice(data: LocationData, cfg: dict[str, Any], units: str = "mph") -> list[Advice]:
    """Advice for Sea-Doo, boating and fishing, based on current + near-term hours."""
    h = data.current
    loc = data.location
    out: list[Advice] = []

    if data.marine_available:
        sea = describe_sea(h.wave_ft, h.wave_period_s)
        if sea:
            level = "warn" if round(h.wave_ft or 0, 1) >= 3 or (h.wave_period_s or 99) < 6 else "info"
            parts = f"{h.wave_ft:.1f} ft @ {U.num(h.wave_period_s)}s from {U.compass(h.wave_dir)}"
            if h.wind_wave_ft is not None and h.swell_ft is not None:
                parts += (
                    " (mostly wind chop)" if h.wind_wave_ft > h.swell_ft else " (mostly swell)"
                )
            out.append(Advice("Sea state", f"{parts}. {sea}", level))

    rel = shore_relation(h.wind_dir, loc.shore_facing_deg)
    if rel and h.wind_mph is not None and h.wind_mph >= 5:
        w = f"{U.compass(h.wind_dir)} {_wind(h.wind_mph, units)}"
        msg = {
            "onshore": (f"Onshore wind ({w}). Chop builds near the beach and inlets, and "
                        "launching or beaching through the shore break is wetter.", "info"),
            "offshore": (f"Offshore wind ({w}). Calmer near the beach, but seas build farther "
                         "out, and a stalled PWC or kayak drifts out to sea. Stay close in and "
                         "carry a VHF or PLB.", "warn"),
            "cross-shore": (f"Cross-shore wind ({w}). Expect side chop and drift along the "
                            "beach.", "info"),
        }[rel]
        out.append(Advice("Wind vs shore", *msg))

    stage = tide_stage(data, h.time)
    if stage and data.tide_station:
        kind, prv, nxt = stage
        flow = "outgoing (ebb)" if kind == "falling" else "incoming (flood)"
        text = f"Tide {kind}, {flow}: {nxt.kind} {nxt.height_ft:.1f} ft at {_t(nxt.time)}"
        if prv:
            text += f" (last {prv.kind.lower()} {_t(prv.time)})"
        text += ". Fishing is often best with moving water around the tide change."
        level = "info"
        onshore_push = rel == "onshore" and (h.wind_mph or 0) >= 10
        if kind == "falling" and (onshore_push or (h.wave_ft or 0) >= 2):
            text += (
                " Outgoing tide against onshore wind or swell stacks up steep waves in inlets. "
                f"Run inlets near slack or on the incoming tide (after {_t(nxt.time)})."
            )
            level = "warn"
        out.append(Advice("Tide", text, level))

    if a := _gust_trend(data, units, "marine"):
        out.append(a)
    if (h.heat_index_f or 0) >= 100:
        out.append(Advice(
            "Heat",
            f"Heat index {h.heat_index_f:.0f}°F. Hydrate and take shade breaks. "
            "Glare off the water adds to the UV.",
            "warn",
        ))
    elif (h.uv_index or 0) >= 8:
        out.append(Advice("Sun", f"UV {h.uv_index:.0f}. Glare off the water adds to it; cover up."))
    if a := _storm_risk(data, cfg, "marine"):
        out.append(a)
    return out
