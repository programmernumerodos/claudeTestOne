"""Tests for the Safe Window Finder, tropical awareness, advice, astro, history,
charts, the new CLI views and the Streamlit dashboard (all offline)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from fakes import fake_tcm, fake_tcp
from flweatherops import cli, history
from flweatherops.advice import describe_sea, drone_advice, marine_advice, shore_relation
from flweatherops.astro import astro_hours, best_astro_block, moon_info
from flweatherops.config import Location, locations_from_config
from flweatherops.data import HourData, LocationData, TideEvent, TideStation
from flweatherops.safety import Status, Verdict
from flweatherops.service import build_briefing, get_tropical
from flweatherops.tropical import (
    Alert,
    Storm,
    TrackPoint,
    TropicalReport,
    _segment_covers,
    assess_location,
    parse_alerts,
    parse_forecast_track,
    strip_html,
)
from flweatherops.windows import find_windows, format_span, rank_windows

VERO = Location("vero-beach", "Vero Beach, FL", 27.6386, -80.3973, coastal=True, shore_facing_deg=80)
OKEE = Location("okeechobee", "Okeechobee, FL (inland)", 27.2439, -80.8298, coastal=False)


def hour(t: datetime, **kw: Any) -> HourData:
    base = dict(time=t, temp_f=78, heat_index_f=80, precip_prob_pct=5, precip_in=0,
                weather_code=1, visibility_mi=15, wind_mph=6, gust_mph=10, wind80_mph=9,
                wind120_mph=11, cape=200, wave_ft=1.5, is_day=True)
    base.update(kw)
    return HourData(**base)


# --------------------------------------------------------------------------- #
# Safe Window Finder
# --------------------------------------------------------------------------- #


def _series(statuses: str, start: datetime = datetime(2026, 10, 27, 6)) -> tuple[list[HourData], list[Verdict]]:
    m = {"S": Status.SAFE, "C": Status.CAUTION, "N": Status.NOGO}
    hs = [hour(start + timedelta(hours=i)) for i in range(len(statuses))]
    return hs, [Verdict(m[c]) for c in statuses]


def test_find_windows_min_length_and_order() -> None:
    hs, vs = _series("SSSCSSNSSSS")
    wins = find_windows(hs, vs, min_hours=2)
    assert [w.duration_h for w in wins] == [3, 2, 4]
    assert wins[0].start == hs[0].time and wins[0].end == hs[3].time  # end exclusive
    assert rank_windows(wins)[0].duration_h == 4
    assert [w.duration_h for w in find_windows(hs, vs, min_hours=4)] == [4]


def test_find_windows_daylight_and_gaps() -> None:
    hs, vs = _series("SSSS")
    hs[1].is_day = False
    assert [w.duration_h for w in find_windows(hs, vs, daylight_only=True)] == [1, 2]
    hs, vs = _series("SSSS")
    hs[2].time += timedelta(hours=5)  # missing hours break a run
    hs[3].time += timedelta(hours=5)
    assert [w.duration_h for w in find_windows(hs, vs)] == [2, 2]


def test_format_span() -> None:
    today = date(2026, 10, 27)
    assert format_span(datetime(2026, 10, 27, 14), datetime(2026, 10, 27, 17), today) == "14:00–17:00 today"
    assert format_span(datetime(2026, 10, 28, 9), datetime(2026, 10, 28, 12), today) == "09:00–12:00 tomorrow"
    assert format_span(datetime(2026, 10, 27, 21), datetime(2026, 10, 28, 3), today) == "today 21:00 – tomorrow 03:00"
    assert format_span(datetime(2026, 10, 29, 7), datetime(2026, 10, 29, 9), today) == "07:00–09:00 Thu Oct 29"


# --------------------------------------------------------------------------- #
# Tropical
# --------------------------------------------------------------------------- #


def test_parse_forecast_track_and_month_rollover() -> None:
    text = """FORECAST VALID 30/1800Z 25.6N  77.2W
MAX WIND  90 KT...GUSTS 110 KT.
FORECAST VALID 31/0600Z 26.0N  78.0W
MAX WIND  95 KT...GUSTS 115 KT.
OUTLOOK VALID 02/0600Z 28.5N  80.5W
MAX WIND  60 KT...GUSTS  75 KT."""
    pts = parse_forecast_track(text, datetime(2026, 10, 30, 15, tzinfo=timezone.utc))
    assert [(p.time.month, p.time.day, p.time.hour) for p in pts] == [(10, 30, 18), (10, 31, 6), (11, 2, 6)]
    assert pts[0].lat == 25.6 and pts[0].lon == -77.2 and pts[0].max_wind_kt == 90
    assert pts[2].max_wind_kt == 60


def test_parse_fake_advisories() -> None:
    pts = parse_forecast_track(strip_html(fake_tcm()), datetime.now(timezone.utc))
    assert len(pts) == 5
    alerts, raw = parse_alerts(strip_html(fake_tcp()))
    assert [a.kind for a in alerts] == ["Hurricane Watch", "Tropical Storm Warning"]
    assert alerts[0].areas == ["Deerfield Beach to Sebastian Inlet", "Lake Okeechobee"]
    assert "DISCUSSION" not in raw


def test_parse_alerts_real_world_shape() -> None:
    text = """SUMMARY OF WATCHES AND WARNINGS IN EFFECT:

A Storm Surge Warning is in effect for...
* Flamingo northward to Yankeetown
* Sebastian Inlet to Altamaha Sound

A Hurricane Warning is in effect for...
* Bonita Beach northward to Suwannee River, including Tampa Bay
* Sebastian Inlet to St. Marys River

Interests elsewhere in Florida should monitor the progress of Milton.
"""
    alerts, _ = parse_alerts(text)
    assert [a.kind for a in alerts] == ["Storm Surge Warning", "Hurricane Warning"]
    assert alerts[1].areas[1] == "Sebastian Inlet to St. Marys River"


def test_segment_coverage() -> None:
    assert _segment_covers("Jupiter Inlet to Sebastian Inlet", VERO)
    assert not _segment_covers("Sebastian Inlet to St. Marys River", VERO)  # Vero is south of it
    assert _segment_covers("North of Deerfield Beach to Jupiter Inlet",
                           Location("x", "Boynton Beach, FL", 26.53, -80.06))
    assert _segment_covers("Lake Okeechobee", OKEE)
    assert not _segment_covers("Jupiter Inlet to Sebastian Inlet", OKEE)  # inland
    assert not _segment_covers("Bonita Beach northward to Suwannee River", VERO)


def _storm(lat: float, lon: float, track: list[tuple[int, float, float]], alerts: list[Alert] | None = None) -> Storm:
    t0 = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return Storm(
        id="al012026", name="Test", classification="HU", lat=lat, lon=lon, intensity_kt=100,
        last_update=t0, track=[TrackPoint(t0 + timedelta(hours=h), la, lo) for h, la, lo in track],
        track_source="forecast advisory", alerts=alerts or [],
    )


def test_assess_location_hourly_levels(cfg: dict[str, Any]) -> None:
    # Storm 500+ mi east now, passing ~30 mi from Vero in 24 h.
    storm = _storm(27.0, -72.0, [(12, 27.3, -76.0), (24, 27.6, -80.0), (48, 28.0, -84.0)])
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    hours = [hour(now + timedelta(hours=i)) for i in range(0, 72, 6)]
    lt = assess_location(TropicalReport(ok=True, storms=[storm]), VERO, hours, cfg)
    assert lt.level == Status.NOGO
    assert lt.impacts[0].closest_mi < 50
    assert lt.hour_reasons[0] == []  # far away at first
    assert any(r.level == Status.NOGO for r in lt.hour_reasons[4])  # ~24 h
    assert lt.hour_reasons[-1] == []  # beyond the forecast track -> unknown


def test_assess_watch_and_distance_filter(cfg: dict[str, Any]) -> None:
    watch = Alert("Hurricane Watch", ["Jupiter Inlet to Sebastian Inlet"])
    storm = _storm(24.0, -60.0, [], alerts=[watch])  # ~1300 mi away, no track
    hours = [hour(datetime.now().replace(minute=0, second=0, microsecond=0))]
    lt = assess_location(TropicalReport(ok=True, storms=[storm]), VERO, hours, cfg)
    assert lt.level == Status.CAUTION  # watch covers Vero
    assert lt.hour_reasons[0][0].note.startswith("Hurricane Watch")
    far = _storm(15.0, -40.0, [])
    lt = assess_location(TropicalReport(ok=True, storms=[far]), VERO, hours, cfg)
    assert lt.impacts == [] and lt.level == Status.SAFE


def test_tropical_integrated_into_briefing(fake_http: Any, cfg: dict[str, Any]) -> None:
    locs = locations_from_config(cfg)
    rep = get_tropical(cfg)
    assert rep is not None and rep.ok and [s.name for s in rep.storms] == ["Testy"]  # EP storm filtered
    assert rep.storms[0].track_source == "forecast advisory"
    b = build_briefing(locs["boynton-beach"], cfg, tropical=rep)
    assert b.tropical is not None and b.tropical.level == Status.NOGO
    # Hurricane Watch covers Boynton -> no hour can be Safe.
    assert all(v.status >= Status.CAUTION for v in b.hourly["drone"])
    assert b.windows["drone"] == [] and b.windows["marine"] == []


def test_nhc_down_degrades_gracefully(fake_http: Any, cfg: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    fake_http.nhc_fails = True
    rep = get_tropical(cfg, use_cache=False)
    assert rep is not None and not rep.ok and "NHC unreachable" in (rep.error or "")
    assert cli.main(["vero", "--no-cache", "--hours", "3"]) == 0
    assert "Tropical check skipped" in capsys.readouterr().out


def test_tropical_disabled(cfg: dict[str, Any]) -> None:
    cfg["tropical"]["enabled"] = False
    assert get_tropical(cfg) is None


# --------------------------------------------------------------------------- #
# Advice
# --------------------------------------------------------------------------- #


def _data(hours: list[HourData], loc: Location = VERO, **kw: Any) -> LocationData:
    return LocationData(location=loc, current=hours[0], hours=hours, marine_available=True, **kw)


def test_shore_relation_and_sea_state() -> None:
    assert shore_relation(90, 90) == "onshore"
    assert shore_relation(270, 90) == "offshore"
    assert shore_relation(0, 90) == "cross-shore"
    assert shore_relation(None, 90) is None
    assert "Flat" in describe_sea(0.5, 8)
    assert "Short period" in describe_sea(2.5, 4)
    assert "Long-period" in describe_sea(1.5, 12)
    assert describe_sea(2.96, 7).startswith("Rough")  # classified as displayed (3.0 ft)


def test_drone_advice_battery_gusts_and_storms(cfg: dict[str, Any]) -> None:
    t = datetime(2026, 7, 1, 12)
    hs = [hour(t, temp_f=97, gust_mph=12, wind_mph=8)] + [
        hour(t + timedelta(hours=i), gust_mph=22, cape=2000, precip_prob_pct=50) for i in range(1, 4)
    ]
    topics = {a.topic: a for a in drone_advice(_data(hs), cfg)}
    assert topics["Battery"].level == "warn" and "97°F" in topics["Battery"].text
    assert "Gusts rising" in topics["Wind trend"].text
    assert topics["Lightning"].level == "alert"
    assert "Wind aloft" in topics


def test_marine_advice_tide_against_onshore_wind(cfg: dict[str, Any]) -> None:
    t = datetime(2026, 10, 27, 10)
    hs = [hour(t, wind_dir=80, wind_mph=14, wave_ft=2.5, wave_period_s=5)]
    tides = [TideEvent(t - timedelta(hours=2), 3.0, "High"), TideEvent(t + timedelta(hours=4), 0.4, "Low")]
    d = _data(hs, tides=tides, tide_station=TideStation("1", "Test"))
    topics = {a.topic: a for a in marine_advice(d, cfg)}
    assert topics["Wind vs shore"].text.startswith("Onshore")
    assert topics["Tide"].level == "warn" and "outgoing" in topics["Tide"].text
    assert "Short period" in topics["Sea state"].text


# --------------------------------------------------------------------------- #
# Astro
# --------------------------------------------------------------------------- #


def test_moon_phase_known_dates() -> None:
    full = moon_info(datetime(2024, 4, 23, 23, 49, tzinfo=timezone.utc), 27.6, -80.4)
    new = moon_info(datetime(2024, 4, 8, 18, 21, tzinfo=timezone.utc), 27.6, -80.4)
    assert full.phase_name == "Full Moon" and full.illumination > 0.98
    assert new.phase_name == "New Moon" and new.illumination < 0.02


def test_astro_scores_prefer_clear_moonless() -> None:
    t = datetime(2024, 4, 8, 22)  # new moon night
    hs = [hour(t + timedelta(hours=i), is_day=False, cloud_cover_pct=c) for i, c in enumerate([5, 5, 90, 5])]
    rated = astro_hours(hs, 27.6, -80.4, "America/New_York")
    assert [a.rating for a in rated] == ["Excellent", "Excellent", "Poor", "Excellent"]
    assert [a.time for a in best_astro_block(rated)] == [hs[0].time, hs[1].time]


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #


def test_history_log_rate_limit_and_read(tmp_path: Path) -> None:
    db = tmp_path / "h.db"
    rec = {"logged_at": "2026-10-27T10:00:00", "location_key": "vero", "location_name": "Vero",
           "drone_status": "SAFE", "wind_mph": 8.0, "reasons": {"drone": []}}
    assert history.log_briefing(rec, path=db)
    assert not history.log_briefing({**rec, "logged_at": "2026-10-27T10:10:00"}, path=db)
    assert history.log_briefing({**rec, "logged_at": "2026-10-27T11:00:00", "drone_status": "NO-GO"}, path=db)
    rows = history.recent(10, "vero", path=db)
    assert [r.drone_status for r in rows] == ["NO-GO", "SAFE"]
    assert history.recent(10, "other", path=db) == []


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #


def test_charts_build(fake_http: Any, cfg: dict[str, Any]) -> None:
    pytest.importorskip("plotly")
    pytest.importorskip("pandas")
    from flweatherops import charts
    from flweatherops.astro import moon_track

    locs = locations_from_config(cfg)
    rep = get_tropical(cfg)
    b = build_briefing(locs["vero-beach"], cfg, hours=48, tropical=rep)
    th = cfg["thresholds"]
    for fig in (charts.wind_chart(b, "kn", 48, th, "marine"), charts.precip_chart(b, 48, th),
                charts.wave_chart(b, 48, th), charts.temp_chart(b, 48), charts.status_timeline(b, 72),
                charts.storm_map(rep, list(locs.values()), highlight=locs["vero-beach"])):
        assert fig.data  # has traces
    df = charts.hourly_frame(b, "mph", 24)
    assert list(df.columns[:3]) == ["Time", "Drone", "Marine"] and len(df) == 24
    charts.style_hourly(df).to_html()  # styler renders
    rated = astro_hours(b.data.hours, 27.6, -80.4, "America/New_York")
    assert charts.astro_chart(rated, moon_track(b.data.hours, 27.6, -80.4, "America/New_York")).data


# --------------------------------------------------------------------------- #
# CLI views
# --------------------------------------------------------------------------- #


def test_cli_windows_view(fake_http: Any, capsys: pytest.CaptureFixture[str]) -> None:
    fake_http.storms = False
    assert cli.main(["vero", "--windows", "--min-window", "3"]) == 0
    out = capsys.readouterr().out
    assert "Drone Safe windows:" in out and "Best drone window:" in out
    assert "tomorrow" in out


def test_cli_tropical_view(fake_http: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["boynton", "--tropical"]) == 0
    out = capsys.readouterr().out
    assert "Hurricane Testy" in out and "Deerfield Beach to Sebastian Inlet" in out
    fake_http.storms = False
    assert cli.main(["--tropical", "--no-cache"]) == 0
    assert "No active Atlantic tropical cyclones" in capsys.readouterr().out


def test_cli_astro_and_history(fake_http: Any, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["vero", "--astro"]) == 0
    assert "Astro conditions" in capsys.readouterr().out
    assert cli.main(["vero", "--history"]) == 0
    out = capsys.readouterr().out
    assert "Briefing history" in out and "Vero Beach" in out
    assert cli.main(["vero", "--no-log", "--hours", "2"]) == 0  # still works


def test_cli_rejects_bad_min_window(fake_http: Any) -> None:
    assert cli.main(["--min-window", "0"]) == 2


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #


def test_dashboard_renders(fake_http: Any) -> None:
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    import streamlit as st

    st.cache_data.clear()
    app = str(Path(__file__).resolve().parent.parent / "dashboard.py")
    at = AppTest.from_file(app, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.subheader[0].value == "Boynton Beach, FL"
    assert [t.label for t in at.tabs][:4] == ["Overview", "Charts", "Hourly", "Safe windows"]
    assert any("Tropical alert" in e.value for e in at.error)

    at.sidebar.radio[0].set_value("Lat/Lon").run()
    assert not at.exception
    at.sidebar.radio[0].set_value("Search").run()
    at.sidebar.text_input[0].set_value("Jupiter, FL").run()
    assert not at.exception and at.subheader[0].value == "Jupiter, Florida"

    fake_http.storms = False
    st.cache_data.clear()
    at.sidebar.radio[0].set_value("Saved").run()
    assert not at.exception
    assert any("No active Atlantic" in s.value for s in at.success)
    assert any("Best drone window" in s.value for s in at.success)
