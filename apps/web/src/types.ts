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
  score: number
  score_components: ScoreComponents
  visible_targets: VisibleTarget[]
  active_showers: ActiveShower[]
  sat_passes: SatPass[]
  sat_stale: boolean
  sat_future_stale: boolean
  sat_future_warn: boolean
  sat_tle_stale: boolean
  sat_network_error: boolean
  starlink_trains: StarlinkTrain[]
  sat_starlink_unavailable: boolean
}

/** A FastAPI error body: {"detail": "..."} */
export interface ApiError {
  detail: string
}
