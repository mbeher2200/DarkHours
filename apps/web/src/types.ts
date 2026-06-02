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
  moon: number
  dark: number
  bortle: number
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
  peak_time: string | null
  peak_alt_deg: number | null
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
  lifted_index: number | null
  precip_type: string | null
  temperature_c: number | null
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
}

/** A FastAPI error body: {"detail": "..."} */
export interface ApiError {
  detail: string
}
