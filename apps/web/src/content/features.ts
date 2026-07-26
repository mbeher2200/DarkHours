// Single source of truth for the empty-state feature grid, shared with
// entry-server.tsx so the WebApplication JSON-LD's featureList (synced by
// scripts/prerender.mjs) can't silently drift from what's actually on the page.
//
// Ordered by differentiation, not data category: exclusive differentiators
// first, then distinctive modeling, then commodity metrics; the rest is
// swept into the "fundamentals" line in App.tsx rather than given tiles.
export const FEATURES: [string, string][] = [
  ['Target Windows',    'Every shootable target with its peak time and window, and why others are blocked due to clouds, moonwash, or light domes.'],
  ['Milky Way Planner', 'A 360° view of the galactic plane over your horizon—altitude, bearing, and the best minute to shoot.'],
  ['Sky & Horizon Glow', 'A horizon map of light domes around you. Know more than just the Bortle.'],
  ['Nearby Dark Sky',   'Search for low bortle locations you can actually reach, with routing to facilities like parking, campgrounds, and viewpoints. All sorted by drive times.'],
  ['Moonlight Modeling', 'Most tools treat the moon as binary: up means ruined. DarkHours models real scattered moonlight brightening at your target from live aerosol data. A crescent barely registers. A bright gibbous gets called out.'],
  ['360° Sky Simulation', "Drag around a rendered dome of tonight's actual sky: stars, Milky Way, moon phase, and light domes to scale. Scrub sunset to sunrise and watch visibility accurately change with conditions across the timeline"],
  ['Aurora Forecast', 'NOAA space-weather data run through a geomagnetic visibility model for your exact location. Tiered honestly as overhead, naked eye, camera capture only, with the bearing to look toward.'],
  ['Satellite Passes', "ISS, Hubble, and Tiangong passes with rise, peak, and set times. Newly launched Starlink trains are tracked while they're still bunched and bright."],
  ['30 Day Best Night', 'A 30 day outlook. Compare conditions across multiple nights to identify the best windows.'],
  ['Smoke & Haze Forecast',  'Wildfire smoke and upper-air aerosol data from ground sensors, and satellites.'],
  ['Meteor Shower Predictions', 'Know when the next meteor shower will peak, the estimated meteors per hour, and visibility from your location.'],
  ['Tailored Weather Forecasts', 'A two week, hour by hour forecast for: cloud cover, temperature, wind and humidity cloud layers, and wind - updated twice an hour. Plus three day atmospheric seeing, and transparency provided by 7Timer.'],
]
