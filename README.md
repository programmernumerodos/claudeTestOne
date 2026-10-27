# flWeatherOps

A personal operations tool for **drone flying** and **coastal marine / fishing / Sea-Doo** trips in
South and Central Florida. It answers three questions:

- **Can I go now?** You get **SAFE / CAUTION / NO-GO** for each activity, plus the exact reasons.
- **When can I go?** The *Safe Window Finder* scans the next 72 hours.
- **Is anything tropical coming?** It checks the NHC for active Atlantic storms. A nearby storm, or a watch or warning for your area, forces Caution or No-Go.

There are two front ends, and both use the same data layer and safety rules:

| | Command | Best for |
|---|---|---|
| **Streamlit dashboard** | `streamlit run dashboard.py` | Status cards, interactive charts, storm map, history. Works on a phone. |
| **CLI** | `python -m flweatherops` | Quick terminal check, scripting. |

Data sources need no API keys:
- weather and waves: [Open-Meteo](https://open-meteo.com)
- tides: NOAA CO-OPS
- tropical systems: NOAA NHC

## Install

Requires Python 3.10+.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # CLI + dashboard
# or: pip install .                      # CLI only (requests + rich)
#     pip install ".[dashboard]"         # CLI + dashboard
```

## Dashboard

```bash
streamlit run dashboard.py               # opens http://localhost:8501
```

To check it from your phone on the same Wi-Fi, run `streamlit run dashboard.py --server.address 0.0.0.0` and open `http://<your-computer's-IP>:8501`.

**Sidebar**
- Pick a location three ways: a saved location, a place search (e.g. "Jupiter, FL"), or raw lat/lon. Search and lat/lon have an *inland* toggle that skips waves and tides.
- Set the hours to show (12–72) and the wind units (mph or knots).
- **↻ Refresh** clears the caches.

**Top of the page**
- A **🌀 tropical alert** banner appears when a storm is relevant to this location.
- Two **status cards**, Drone and Marine, each show:
  - the big SAFE / CAUTION / NO-GO;
  - the top reasons right now;
  - when the status changes next;
  - the best Safe window in the next 72 h;
  - how many hours ahead are safe, caution and no-go.

**Tabs**

| Tab | What's there |
|---|---|
| **Overview** | Current conditions (wind, upper-level wind, rain, visibility, clouds, UV, CAPE, waves); practical **drone and marine notes**; upcoming **tides**. |
| **Charts** (Plotly) | A status-by-hour strip; wind at 10 m, 80 m and 120 m plus gusts, with gust limits and Caution/No-Go shading; precipitation; waves with swell, wind-wave and period; temperature and feels-like with night shading. |
| **Hourly** | A color-coded table. Drone and Marine status come first; the top reasons are at the end. |
| **Safe windows** | A slider for the minimum continuous hours and a daylight-only toggle for drones. Shows the best window and all windows (with peak wind, gust and waves), and the 72 h status strip. |
| **🌀 Tropical** | A map of active storms (forecast track, 200/400 mi rings around your location); one expander per storm with intensity, pressure, motion, the impact on your location, and NHC watch/warning text. If no storms are active, it says so. |
| **🌌 Astro** | Moon phase and illumination, next sunset, and the best astro block. A night-sky score chart (clouds plus moon altitude), and a table with low, mid and high clouds, visibility and notes. |
| **History** | Logged briefings for this location (or all locations), with a wind, gust and upper-level wind trend line. |

**Caching:**
- forecasts are cached for 10 min, the NHC check for 15 min, and geocoding for 1 day;
- forecasts also use the on-disk fallback cache described below.

## CLI

```bash
python -m flweatherops                      # default locations (Boynton Beach + Vero Beach)
python -m flweatherops vero okeechobee      # specific saved locations (key or name prefix)
python -m flweatherops --all --summary      # one-line overview of every saved location
python -m flweatherops --place "Jupiter, FL"
python -m flweatherops --lat 27.20 --lon -80.17 --name "St. Lucie Inlet"
python -m flweatherops --units kn --hours 36

# Focused views. They replace the full briefing and can be combined.
python -m flweatherops --windows                 # Safe Window Finder, next 72 h
python -m flweatherops vero --windows --min-window 3
python -m flweatherops --tropical                # NHC: active storms, impact per location, watches/warnings
python -m flweatherops vero --astro              # night-sky table for astrophotography
python -m flweatherops --history 30              # last 30 logged briefings (add a location to filter)
```

Other flags:
- `--activity drone|marine|both`, `--no-tropical`, `--no-tides`, `--no-cache`
- `--no-log`: don't record this run in history
- `--config PATH`, `--list`, `--dump-config`

**Full briefing**, from top to bottom:
1. Tropical alert banner, when relevant.
2. Recommendation table.
3. Current conditions and tides.
4. **Operational notes**.
5. **Safe Window Finder** summary.
6. Hourly table, color-coded.

The layout adapts to terminals 80 columns wide or more.

**Example Safe Window Finder output:**
```
Drone Safe windows: 07:00–13:00 tomorrow, 07:00–13:00 Thu Oct 29
  Best drone window: 07:00–13:00 tomorrow (6h; max gust 12 mph)
Marine Safe windows: today 18:00 – tomorrow 13:00, ...
  Best marine window: today 18:00 – tomorrow 13:00 (19h; max gust 12 mph, waves ≤ 3.0 ft)
```

**Exit codes:**
- `0`: at least one briefing was produced;
- `1`: every location failed;
- `2`: usage or config error.

## How the safety calls work

Each hour is checked against every rule for an activity, and the **worst** result wins. Each hit is listed as a reason, e.g. `Gusts 22 mph ≥ 18 mph`.
- A "max" rule trips when the value is ≥ its threshold.
- Visibility is a "min" rule: it trips when the value is < its threshold.

| Rule (config key) | Drone caution / no-go | Marine caution / no-go |
|---|---|---|
| Wind 10 m (`wind_speed_mph`) | 12 / 18 mph | 15 / 21 mph |
| Gusts 10 m (`wind_gust_mph`) | 18 / 25 mph | 20 / 28 mph |
| Wind aloft, max of 80 m and 120 m ≈ 400 ft (`wind_aloft_mph`) | 18 / 26 mph | – |
| Wave height (`wave_height_ft`) | – | 3 / 5 ft |
| Rain chance (`precip_prob_pct`) | 25 / 50 % | 40 / 70 % |
| Rain amount (`precip_in`) | 0.01 / 0.03 in/h | 0.05 / 0.25 in/h |
| Visibility (`visibility_mi`, min) | < 5 / < 3 mi (the Part 107 minimum is 3 SM) | < 3 / < 1 mi |
| Heat index (`heat_index_f`) | 100 / 108 °F | 103 / 110 °F |

**Lightning proxy** (both activities):
- A thunderstorm weather code (WMO 95/96/99) is always **NO-GO**.
- Otherwise, if rain chance is ≥ 30 %:
  - CAPE ≥ 1000 J/kg gives **CAUTION**;
  - CAPE ≥ 2500 J/kg gives **NO-GO**.

**Tropical** (both activities). Each forecast hour is checked against each storm's position at that hour, interpolated along the NHC forecast track:
- within `nogo_radius_mi` (200 mi): **NO-GO**;
- within `caution_radius_mi` (400 mi): **CAUTION**;
- an NHC **warning** that appears to cover the location: **NO-GO** for every hour;
- an NHC **watch** that appears to cover it: **CAUTION** for every hour.

Missing wind data counts as CAUTION, never as safe.

## Safe Window Finder

- A window is a run of **consecutive SAFE hours** at least `windows.min_hours` long (default **2**). The CLI's `--min-window` overrides this, and the dashboard has a slider.
- It scans `windows.scan_hours` ahead (default **72**).
- Drone windows are daylight-only by default (`drone_daylight_only`).
- A gap in the hourly data breaks a window.
- The "best" window is the longest one; ties go to the one with the lowest peak gust, then the earliest.
- Times are local, the end time is exclusive, and the day is written as *today*, *tomorrow* or a date.

## Tropical / hurricane awareness

It reads these public NHC products:
1. `https://www.nhc.noaa.gov/CurrentStorms.json`: active storms, current position, intensity and motion. Only Atlantic (`al…`) systems are used.
2. Each storm's **forecast advisory**: the 5-day forecast positions (`FORECAST VALID 28/0000Z 26.5N 79.0W`) are parsed into a track. If that text can't be fetched, the track is extrapolated from the current motion and marked as such.
3. Each storm's **public advisory**: the *Summary of watches and warnings*. A segment counts as covering a location when:
   - the location's name appears in it; or
   - for Atlantic-coast spots, the location lies between the segment's two Florida east-coast breakpoints, e.g. `Jupiter Inlet to Sebastian Inlet` (there is a built-in latitude table); or
   - the segment is `Lake Okeechobee` and the location is Okeechobee.

   Any watch or warning that mentions Florida is also highlighted.

Storms more than `list_radius_mi` (1500 mi) away are ignored.

If NHC can't be reached:
- the last cached response (up to 6 h old) is used;
- failing that, briefings continue without the tropical check, and a note says so.

> This is a **distance check, not the official cone or wind field**. Always follow NHC and your county's emergency management.

## Operational notes

These are practical notes that add context. They **don't change** the verdict.

**Drone**
- Estimated wind at about 260 ft (80 m) and 400 ft (120 m), and how it compares to the surface wind (fly upwind first, keep RTH margin).
- Gust spread (turbulence) and **gusts rising** in the next 3 h, e.g. "shorter flights or a heavier airframe".
- Gusts or upper-level wind above typical sub-250 g drone ratings.
- **LiPo battery notes**: hot at 95 °F and above; cool below 60 °F; ideal is 68–86 °F.
- Lens fogging near the dew point.
- Haze or low visibility, and low cloud ceilings (VLOS/FPV).
- UV glare.
- **High CAPE + rain = elevated lightning risk**.

**Marine**
- Wave height + period in plain language for a Sea-Doo or small boat: "short period: steep chop that rides rougher than the height suggests", "long-period swell breaks hard on inlet bars", and whether it is mostly wind chop or swell.
- **Wind relative to the shore**: onshore, offshore ("a stalled PWC drifts out to sea") or cross-shore. This uses each location's `shore_facing_deg`.
- **Tide stage** from NOAA: rising or falling, the next high/low, and a warning when an **outgoing tide meets onshore wind or swell** at inlets.
- Rising wind, heat and UV, and lightning.

## Astrophotography

- Every night hour gets a 0–100 score.
- Penalties: cloud cover, high cirrus, the moon above the horizon (scaled by how much of it is lit), haze, dew and showers.
- Open-Meteo has no moon data, so the moon phase and altitude are computed locally from the low-precision lunar series, accurate to about 1°.
- Ratings: Excellent ≥ 75, Good ≥ 55, Fair ≥ 35.

## Briefing history

- Every briefing, from the CLI or the dashboard, is logged to SQLite at `~/.local/share/flweatherops/history.db` (or `$XDG_DATA_HOME/flweatherops/history.db`).
- Each row has the timestamp, location, Drone, Marine and Tropical status, wind, gust, upper-level wind, waves, rain, temperature, CAPE and the reasons.
- A location is logged at most once every `history.min_interval_min` (30 min).
- To see it: `--history` in the CLI, or the dashboard's History tab.
- `--no-log` skips logging for a run; `history.enabled: false` turns it off entirely.

## Configuration (`config.json`)

flWeatherOps reads the first config file it finds from:
1. `--config PATH`
2. `./config.json`
3. `~/.config/flweatherops/config.json`

The file is **deep-merged** over the defaults, so you only write what you change. Run `python -m flweatherops --dump-config` to see every option. See `config.example.json`:

```json
{
  "locations": {
    "stuart": {"name": "Stuart / St. Lucie Inlet, FL", "lat": 27.1975, "lon": -80.2528,
               "marine_lat": 27.165, "marine_lon": -80.14, "coastal": true,
               "shore_facing_deg": 75, "default": true},
    "vero-beach": {"default": false}
  },
  "thresholds": {
    "drone":  {"wind_gust_mph":  {"caution": 15, "nogo": 22}},
    "marine": {"wave_height_ft": {"caution": 2,  "nogo": 4}}
  },
  "windows":  {"min_hours": 3},
  "tropical": {"caution_radius_mi": 500, "nogo_radius_mi": 250},
  "advice":   {"light_drone_max_wind_mph": 27}
}
```

| Section | Keys |
|---|---|
| `locations.<key>` | `name`, `lat`, `lon`, `coastal`, `marine_lat`/`marine_lon` (a wave-model point a mile or two offshore), `shore_facing_deg` (bearing from the beach out to open water; 90 = due east), `tide_station` (NOAA ID; nearest station by default), `default` |
| `thresholds.drone` / `thresholds.marine` | `{"caution": x, "nogo": y}` per rule above. `null` turns a level off. Always in mph, ft, mi, in/h and °F, whatever display units you use. |
| `thresholds.lightning` | `cape_caution`, `cape_nogo`, `precip_prob_min` |
| `windows` | `min_hours`, `scan_hours` (≤ 72), `drone_daylight_only` |
| `tropical` | `enabled`, `caution_radius_mi`, `nogo_radius_mi`, `list_radius_mi`, `lookahead_hours`, `warning_status` / `watch_status` (`"nogo"`, `"caution"` or `"safe"`) |
| `advice` | `lipo_cool_f`, `lipo_hot_f`, `gust_spread_mph`, `light_drone_max_wind_mph` |
| `history` | `enabled`, `min_interval_min` |
| `cache` | `enabled`, `max_age_hours` (offline fallback, default 6) |
| `tides` | `enabled`, `max_station_distance_mi` |
| `timezone` | default `America/New_York` |

Built-in locations:
- **Boynton Beach** ★
- **Palm Beach / Lake Worth Inlet**
- **Vero Beach** ★
- **Sebastian Inlet**
- **Fort Pierce Inlet**
- **Okeechobee** (inland)

★ = included in a plain run.

## Caching and graceful degradation

| Failure | What happens |
|---|---|
| Forecast request fails | The last good response, up to 6 h old, is used, with a "cached data from N min ago" note. If there's none, the location shows an error and others continue. |
| Marine API fails, or there is no wave data for the point | Waves are skipped with a note; the marine verdict uses wind, rain and lightning only. |
| NOAA tides fail | The tide panel and tide advice are omitted, with a note. |
| NHC unreachable or malformed | The cached NHC data is used. Failing that, briefings run without the tropical check and say so. The tropical subsystem can never break a briefing. |

## Project layout

```
dashboard.py              Streamlit app (layout only)
flweatherops/
  config.py    defaults (locations, thresholds, windows, tropical, advice, history) + config.json merge
  data.py      HTTP + disk cache; Open-Meteo forecast/marine/geocoding, NOAA tides -> HourData
  safety.py    threshold rules -> Verdict(status, reasons); window summaries
  windows.py   Safe Window Finder
  tropical.py  NHC CurrentStorms.json + advisories -> tracks, watches/warnings, per-hour impact
  advice.py    drone/marine operational notes
  astro.py     moon phase/altitude + night-sky scoring
  history.py   SQLite briefing log
  service.py   build_briefing(): the shared pipeline used by CLI and dashboard
  charts.py    Plotly figures + pandas tables for the dashboard
  render.py    rich terminal output
  cli.py       argparse entry point
tests/         offline tests (fake Open-Meteo/NOAA/NHC in tests/fakes.py)
```

## Development

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

The tests run fully offline:
- `tests/fakes.py` serves fake Open-Meteo, NOAA and NHC data, including a hurricane approaching SE Florida and the NHC-down case.
- They cover the safety rules, Safe Window Finder, tropical parsing and assessment, advice, astro, history, charts, every CLI view, and the dashboard (through Streamlit's `AppTest`).

## Attribution and disclaimer

- Weather data: [Open-Meteo.com](https://open-meteo.com), CC BY 4.0. The free API is for **non-commercial use**.
- Tides: NOAA CO-OPS. Tropical: NOAA National Hurricane Center. Both are public domain.
- flWeatherOps shows **model guidance, not official forecasts**. Before you go, check:
  - the NWS forecast and marine warnings (e.g. NWS Miami / Melbourne);
  - NHC advisories;
  - airspace and TFRs (B4UFLY / LAANC);
  - conditions on site.
