import type { NightReport, WeatherPoint } from './types'
import { formatDayTime, formatTime, scoreBand, scoreLabel } from './format'

function cloudIcon(pct: number): string {
  if (pct <= 10) return '☀️'
  if (pct <= 30) return '🌤️'
  if (pct <= 60) return '⛅'
  if (pct <= 85) return '🌥️'
  return '☁️'
}

function WeatherTable({ points, tz }: { points: WeatherPoint[]; tz: string }) {
  return (
    <details className="wx-details" open>
      <summary>Hourly forecast ({points.length} points)</summary>
      <div className="wx-table-wrap">
        <table className="wx-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Cloud</th>
              <th>Seeing</th>
              <th>Transp.</th>
              <th>Humidity</th>
              <th>Wind</th>
              <th>Precip</th>
            </tr>
          </thead>
          <tbody>
            {points.map((p, i) => (
              <tr key={i}>
                <td className="wx-time">{formatTime(p.time, tz)}</td>
                <td>
                  {cloudIcon(p.cloud_cover_pct)}{' '}
                  <span className="wx-num">{p.cloud_cover_pct}%</span>
                </td>
                <td className="wx-num">
                  {p.seeing_arcsec != null ? `${p.seeing_arcsec.toFixed(2)}"` : '—'}
                </td>
                <td>{p.transparency ?? '—'}</td>
                <td className="wx-num">
                  {p.humidity_pct != null ? `${p.humidity_pct}%` : '—'}
                </td>
                <td className="wx-num">
                  {p.wind_speed_ms != null ? `${p.wind_speed_ms.toFixed(1)} m/s` : '—'}
                </td>
                <td>{p.precip_type && p.precip_type !== 'none' ? p.precip_type : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

function ScoreBar({ label, value }: { label: string; value: number }) {
  const pct = Math.max(0, Math.min(100, value * 10))
  return (
    <div className="bar-row">
      <span className="bar-label">{label}</span>
      <span className="bar-track">
        <span className={`bar-fill band-${scoreBand(value)}`} style={{ width: `${pct}%` }} />
      </span>
      <span className="bar-value">{value.toFixed(1)}</span>
    </div>
  )
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="stat">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  )
}

function weatherSummary(r: NightReport): { value: string; hint?: string } {
  if (r.weather_score != null) {
    return { value: `${r.weather_score.toFixed(1)} / 10`, hint: r.wx_source ?? undefined }
  }
  if (r.wx_pending) return { value: 'Pending', hint: 'beyond the ~7-day forecast horizon' }
  if (r.wx_no_data) return { value: 'No data', hint: 'not covered for this location/date' }
  return { value: 'Not requested' }
}

export default function ReportCard({ report }: { report: NightReport }) {
  const r = report
  const tz = r.tz_name
  const lp = r.light_pollution
  const wx = weatherSummary(r)

  return (
    <section className="card report">
      <header className="report-head">
        <h2 className="place">{r.display_name}</h2>
        <p className="when">
          {r.date} · {tz}
        </p>
      </header>

      <div className={`overall band-${scoreBand(r.score)}`}>
        <div className="overall-num">{r.score.toFixed(1)}</div>
        <div className="overall-meta">
          <div className="overall-label">{scoreLabel(r.score)}</div>
          <div className="overall-sub">Night quality · out of 10</div>
        </div>
      </div>

      <div className="bars">
        <ScoreBar label="Moon" value={r.score_components.moon} />
        <ScoreBar label="Dark time" value={r.score_components.dark} />
        <ScoreBar label="Light pollution" value={r.score_components.bortle} />
        {r.weather_score != null && (
          <ScoreBar label="Weather" value={r.weather_score} />
        )}
      </div>

      <div className="stats">
        <Stat
          label="Moon"
          value={r.phase_name}
          hint={`${r.illumination_pct.toFixed(0)}% illuminated`}
        />
        <Stat
          label="Dark window"
          value={`${r.dark_hours.toFixed(1)} h`}
          hint={`${formatTime(r.night_start, tz)} – ${formatTime(r.night_end, tz)}`}
        />
        <Stat
          label="Light pollution"
          value={lp.bortle_class != null ? `Bortle ${lp.bortle_class}` : '—'}
          hint={lp.bortle_desc ?? undefined}
        />
        <Stat
          label="Sky brightness"
          value={lp.sqm != null ? `${lp.sqm.toFixed(2)} SQM` : '—'}
          hint={lp.source ?? undefined}
        />
        <Stat
          label="Weather"
          value={wx.value}
          hint={wx.hint ?? (r.weather_points.length > 0 ? r.wx_source ?? undefined : undefined)}
        />
        <Stat
          label="Sun"
          value={`${formatTime(r.sunset, tz)} – ${formatTime(r.sunrise, tz)}`}
          hint="set – rise"
        />
      </div>

      {r.weather_points.length > 0 && (
        <WeatherTable points={r.weather_points} tz={tz} />
      )}

      {r.events.length > 0 && (
        <details className="events">
          <summary>Night timeline ({r.events.length} events)</summary>
          <ul>
            {r.events.map((e, i) => (
              <li key={i}>
                <span className="ev-time">{formatDayTime(e.time, tz)}</span>
                <span className="ev-label">{e.label}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {r.visible_targets.length > 0 && (
        <details className="targets">
          <summary>Visible targets ({r.visible_targets.length})</summary>
          <ul>
            {r.visible_targets.map((t, i) => (
              <li key={i}>
                <span className="tg-name">{t.name}</span>
                <span className="tg-type">{t.type}</span>
                {t.windows[0]?.peak_time && (
                  <span className="tg-peak">peak {formatTime(t.windows[0].peak_time, tz)}</span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
