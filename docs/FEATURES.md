# DarkHours / darkhours — Features

What the app actually does, in user terms. Every item below is derived from the
current codebase (source modules noted in comments for future audits). Web
features live at [darkhours.app](https://darkhours.app); the engine is also fully
usable from the command line — CLI-only extras are labeled.

<!-- source: scoring.py, predictor.py -->
## Night Quality Score
One 0–10 score that answers "is tonight worth it?" — a weighted blend of weather
(40%), lunar interference (25%), clear dark hours (25%), and light pollution
(10%). A geometric mean, so one ruined factor can't be averaged away: a
clouded-out night scores like a clouded-out night.

<!-- source: moonlight.py -->
## Moonlight modeled, not feared
Most tools treat the moon as binary — up means ruined. DarkHours computes actual
scattered-moonlight sky brightening at every target's position using Krisciunas &
Schaefer (1991) photometry through a Winkler (2022) scattering kernel, fed by
**live atmospheric aerosol data**. A 5% crescent barely registers; a 75% gibbous
near your target is called out as severe. Imaging windows are clipped exactly
where the physics says the contrast dies.

<!-- source: predictor.py (_apply_condition_vectors), targets.py -->
## Prime targets with honest viability
Tonight's best deep-sky objects, planets, and showers — each with an effective
imaging window and a straight answer when something is wrong: **Clouded out**,
**Moon washout**, **Lost in light dome**, or **Low radiant**. Blocked targets are
demoted with the reason, not silently hidden.

<!-- source: apps/web/src/report/skydome/ -->
## A 360° simulated night sky
Drag around a rendered dome of tonight's actual sky: ~12,000 stars with correct
brightness and color, the Milky Way band, the moon with its real phase, light
domes on your horizon, even zodiacal light. Scrub the time slider from sunset to
sunrise and watch star visibility change with the conditions — including an
estimated visible-star count and limiting magnitude for your exact sky.

<!-- source: milky_way.py, apps/web/src/report/MilkyWay.tsx -->
## Milky Way planning
Arch visibility window, galactic-core altitude and direction, arch angle
(steep/moderate/flat), best viewing time, and per-waypoint conditions along the
galactic plane — clipped by moonlight and weather like every other target.

<!-- source: weather.py, apps/web/src/report/NightTimeline.tsx -->
## Astronomy-grade weather, hour by hour
Cloud cover split by altitude, astronomical seeing and transparency (7Timer
ASTRO), wind and gusts, dew point, humidity — each hour rated for
astrophotography with hard gates on precipitation. A live **▶ Now** row tracks
the current hour through the night.

<!-- source: aqicn.py -->
## Live smoke & haze cross-check
The "Now" row is cross-checked against real-time ground-station PM2.5/PM10
readings (WAQI), catching fast-moving smoke and haze events the forecast hasn't
caught up to yet.

<!-- source: darksky.py (lookup, find_nearby), light_dome.py -->
## Find darker sky nearby
Your sky's SQM and Bortle class from VIIRS 2025 satellite radiance (with Falchi
2016 fallback for the darkest sites), then a search for genuinely darker sky
within 60–120 miles — surfaced as **named, reachable places** (trailhead parking,
viewpoints, campsites, observatories) on public lands, with drive times, road
distances, ferry/unpaved-road warnings, and one-tap driving directions.

<!-- source: light_dome.py, apps/web/src/report/LightDomePanel.tsx -->
## See your horizon glow
An all-sky fisheye map of the light domes around you — which direction is
darkest, where the nearest city glow sits, and how high it reaches into your sky.

<!-- source: aurora.py -->
## Aurora forecast
NOAA space-weather Kp forecasts run through a geomagnetic-latitude visibility
model for your exact location, tiered honestly: overhead, naked-eye on the
horizon, or photographic-only — with the compass bearing to look toward.

<!-- source: targets.py (effective_zhr), docs/TARGETS.md -->
## Meteor showers that decay like real showers
Eleven cataloged showers with peak-night alerts and rates that decay properly
away from the peak (IMO log-linear model), corrected for your radiant altitude
and sky brightness — so "ZHR 100" doesn't get promised on a washed-out night
three days late.

<!-- source: satellites.py, tle_provider.py -->
## Satellite passes — including Starlink trains
Visible passes for the ISS, Hubble, and Tiangong with rise/peak/set, duration,
and moon separation; twilight and shadow-exit passes flagged. Newly launched
Starlink trains are tracked while they're still bunched and bright.

<!-- source: apps/web/src/OutlookTelemetryRibbon.tsx, trip.py -->
## 30-day outlook
A calendar heatmap of the next month's night scores with moon phases and
meteor/aurora markers — find the best night at a glance, then drill into its full
report.

<!-- source: sky_events.py, moon_events.py -->
## The night's timeline, precisely
Sunset, astronomical darkness, moonrise/set, sunrise — plus lunar-cycle context
(tonight's dark hours vs. the cycle's best), supermoon/micromoon flags, and lunar
eclipse detection.

<!-- source: apps/web/src/styles/02-red-mode.css -->
## Red night-vision mode
One tap re-renders the entire interface — every color, chart, and image — in pure
red, so checking the forecast at 2 AM doesn't cost you your dark adaptation.

<!-- source: apps/web/src/App.tsx, api.ts -->
## Built for the field
Shareable permalinks for any location and date, imperial/SI toggle, place
autocomplete with recent searches, use-my-location, mobile-friendly layout. Free
and open source; no account, no cookies.

<!-- source: darkhours.py, tripbuilder.py, weather.py (ERA5) — CLI only -->
## Command-line extras
The same engine runs fully offline-cached from the terminal: single-night
reports, monthly calendars, satellite tables — plus **historical weather back to
1940** (ERA5 reanalysis) for scouting how a site behaves in any season, and a
**trip builder** that scores multiple locations across a date range and ranks the
best nights (command-line only; not part of the web app).

---

Built on open data — NOAA (SWPC, NWS), Open-Meteo, NASA VIIRS, Falchi et al.
2016, 7Timer, OpenStreetMap, CelesTrak, WAQI, USGS PAD-US, the HYG star database,
and ESO imagery. Full attribution: [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md).
