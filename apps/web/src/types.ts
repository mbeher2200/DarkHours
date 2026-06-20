// Shapes mirror apps/api/serializers.py (night_report_to_dict). Only the fields
// the SPA renders are typed; the API may include more (we ignore the rest).

export interface SkyEvent {
  time: string // ISO 8601
  label: string
}

export interface LightPollution {
  sqm: number | null
  bortle_class: number | null
  bortle_desc: string | null
  lp_zone: string | null
  below_detection: boolean
  source: string | null
}

export interface ScoreComponents {
  moon?: number
  dark?: number
  bortle?: number
  weather?: number
}

export interface DarkCycle {
  tonight_hours: number
  mean_hours: number
  stdev_hours: number
  score: number
}

export interface TargetWindow {
  start: string
  end: string
  start_alt_deg: number
  end_alt_deg: number
  peak_time: string | null
  peak_alt_deg: number | null
  peak_az_deg: number
  arch_angle_deg: number | null
  moon_interference: boolean
  moon_sep_at_peak_deg: number | null
  moon_alt_at_peak_deg: number | null
  photo_cutoff: string | null   // last moment viable for astrophotography
  visual_cutoff: string | null  // last moment viable for visual observation
}

export interface VisibleTarget {
  name: string
  type: string
  windows: TargetWindow[]
  note: string | null
}

export interface WeatherPoint {
  time: string
  cloud_cover_pct: number
  seeing_arcsec: number | null
  transparency: string | null
  humidity_pct: number | null
  wind_speed_ms: number | null
  wind_direction_deg: number | null
  lifted_index: number | null
  precip_type: string | null
  temperature_c: number | null
  dew_point_c: number | null
  feels_like_c: number | null
  precip_probability_pct: number | null
  weather_code: number | null
}

export interface MoonEclipse {
  kind: string          // 'penumbral' | 'partial' | 'total'
  time: string          // ISO 8601
  umbral_magnitude: number | null
  penumbral_magnitude: number | null
}

export interface ActiveShower {
  name: string
  note: string
  zhr: number
}

export interface SatPass {
  satellite_name: string
  rise_time: string
  peak_time: string
  set_time: string
  peak_alt_deg: number
  peak_az_deg: number
  rise_az_deg: number
  rise_alt_deg: number
  set_az_deg: number
  set_alt_deg: number
  duration_min: number
  in_sunlight: boolean
  ends_in_shadow: boolean
  sky_dark: boolean
  moon_sep_deg: number | null
  moon_transit: boolean
  moon_transit_sep_deg: number | null
}

export interface StarlinkTrain {
  satellite_count: number
  first_rise: string
  last_rise: string
  peak_alt_deg: number
  lead_az_deg: number
  moon_sep_deg: number | null
  sky_dark: boolean
  launch_date: string | null   // ISO date or null
}

export interface MilkyWaySummary {
  arch_start:           string        // ISO 8601
  arch_end:             string        // ISO 8601
  arch_hours:           number
  moon_limited:         boolean
  moon_penalised:       boolean
  n_visible:            number
  n_max_possible:       number
  n_total:              number
  local_score:          number        // 0–10
  alt_score:            number
  cov_score:            number
  win_score:            number
  core_peak_time:       string        // ISO 8601
  core_peak_in_window:  boolean
  core_peak_alt_deg:    number
  core_peak_az_deg:     number
  arch_angle_deg:       number | null
  farthest_name:        string | null
  farthest_peak_alt_deg: number | null
  farthest_peak_az_deg:  number | null
  core_max_alt_deg:     number
}

// ── Light dome (horizon glow) ─────────────────────────────────────────────────
// Mirrors summarize_horizons() in PyNightSkyPredictor/light_dome.py. The per-direction
// horizon-glow analysis served on the initial /night response (drives the score-card
// fisheye panel; null outside CONUS coverage). Distinct from the find_nearby `light_domes`
// list below, which names the actual bright cities (VIIRS blobs) you can see glowing.

export type Direction = 'N' | 'NE' | 'E' | 'SE' | 'S' | 'SW' | 'W' | 'NW'

export interface LightDome {
  direction: Direction
  severity: 'minor' | 'major'
  score: number
  label: string
  mean_distance_mi: number | null
  dome_height_deg: number
}

export interface LightDomeSummary {
  // Site-level classification the UI branches on (see light_dome.py).
  sky_state: 'dark' | 'bright' | 'domed' | 'urban'
  scores: Record<Direction, number>        // glow index per cardinal direction
  dome_heights: Record<Direction, number>  // apparent dome height θ (degrees) per direction
  darkest_direction: Direction
  darkest_score: number
  domes: LightDome[]                       // worst-first; [] when none stand out
}

export interface NightReport {
  date: string
  lat: number
  lon: number
  display_name: string
  tz_name: string
  events: SkyEvent[]
  sunset: string | null
  sunrise: string | null
  night_start: string | null
  night_end: string | null
  moonrise: string | null
  moonset: string | null
  phase_name: string
  illumination_pct: number
  moon_score: number
  moon_distance_km: number
  moon_special: string | null
  moon_eclipses: MoonEclipse[]
  dark_intervals: [string, string][]
  dark_hours: number
  dark_cycle: DarkCycle | null
  dark_score: number
  light_pollution: LightPollution
  bortle_score: number
  weather_points: WeatherPoint[]
  weather_score: number | null
  wx_source: string | null
  wx_pending: boolean
  wx_no_data: boolean
  wx_error: string | null
  score: number
  score_components: ScoreComponents
  visible_targets: VisibleTarget[]
  mw_summary: MilkyWaySummary | null
  active_showers: ActiveShower[]
  sat_passes: SatPass[]
  sat_stale: boolean
  sat_future_stale: boolean
  sat_future_warn: boolean
  sat_tle_stale: boolean
  sat_network_error: boolean
  starlink_trains: StarlinkTrain[]
  sat_starlink_unavailable: boolean
  light_dome: LightDomeSummary | null
}

/** A FastAPI error body: {"detail": "..."} */
export interface ApiError {
  detail: string
}

// ── Nearby dark-sky search ────────────────────────────────────────────────────

// poi_type values mirror PyNightSkyPredictor _POI_TYPE_LABELS / osm_poi_builder.
export type PoiType =
  | 'parking' | 'viewpoint' | 'camp_site' | 'rest_area'
  | 'caravan_site' | 'picnic_site' | 'ranger_station' | 'observatory' | 'attraction'
  | 'information' | 'tourism' | 'pier' | 'lighthouse' | 'tower'
  | 'summer_camp' | 'firepit' | 'beach_resort' | 'historic'

export interface NearbyPlace {
  name: string | null
  bortle_class: number
  sqm: number | null
  distance_miles: number
  direction: string
  lat: number
  lon: number
  drive_minutes: number | null
  // Road distance (miles) from the routing API; null when not routed (raw fallback).
  drive_miles?: number | null
  // POI-first reachability: true = a routable, pre-named OSM POI (show drive time + badge);
  // false/undefined = a raw backcountry pixel with no road access (hide drive time, offer a
  // map link to the raw coordinate). poi_type is the OSM category when is_poi is true.
  is_poi?: boolean
  poi_type?: PoiType | null
  area_name?: string | null
}

export interface NearbyResult {
  origin_bortle: number
  origin_sqm: number | null
  radius_miles: number
  results: NearbyPlace[]
  light_domes: NearbyPlace[]
  has_dark_sky: boolean
  best_available: NearbyPlace | null
}

export type NearbyJobRecord =
  | { status: 'pending' }
  | { status: 'done'; result: NearbyResult }
  | { status: 'error'; error: string }
