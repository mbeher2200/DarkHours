import type { AuroraForecast, NightReport } from '../types'
import { formatTime } from '../format'
import { InfoTip } from '../shared'
import { BlockerBadge } from './Targets'

// ── Aurora alert (scorecard banner) ───────────────────────────────────────────
//
// Backend (aurora.py nightly_aurora) already applied the photographic floor and
// the darkness gate, so `r.aurora != null` IS the banner condition. The copy
// must qualify the visibility tier — a Kp 5 night is a sky-filling display in
// Fairbanks and a camera-only horizon glow in Denver.

export interface AuroraAlert {
  aurora: AuroraForecast
  state: 'ok' | 'degraded' | 'blocked'
  prefix: string           // plain-color lead-in — Kp figure + tier-qualified visibility
  warning: string | null   // red clause — leading comma through trailing period; null for 'ok'
  suffix: string | null    // plain-color trailing CTA sentence; null for 'ok'
  peakWindowLocal: string | null  // formatted local peak window, or null
}

// Tier phrases shared (in wording) with the CLI line in render_report.py.
const _TIER_CLAUSE: Record<AuroraForecast['tier'], (dir: string) => string> = {
  overhead:     () => ' — strong enough to fill the sky overhead at this latitude.',
  naked_eye:    dir => ` — visible to the naked eye low on the ${dir} horizon.`,
  photographic: dir => ` — too faint for the naked eye here, but a camera on a tripod should catch the glow on the ${dir} horizon.`,
}

export function auroraAlert(r: NightReport): AuroraAlert | null {
  const a = r.aurora
  if (!a) return null

  const kpStr = `Kp ${a.kp_max.toFixed(1)}${a.noaa_scale ? ` (${a.noaa_scale})` : ''}`
  const lead  = a.kp_source === 'outlook'
    ? `PREDICTED SKY EVENT: NOAA's 27-day outlook calls for aurora at ${kpStr}`
    : `PREDICTED SKY EVENT: Aurora ${a.kp_source === 'predicted' ? 'forecast' : a.kp_source} at ${kpStr}`
  const tierClause = _TIER_CLAUSE[a.tier](a.look_direction)

  const peakWindowLocal =
    a.peak_start_utc && a.peak_end_utc
      ? `${formatTime(a.peak_start_utc, r.tz_name)} – ${formatTime(a.peak_end_utc, r.tz_name)}`
      : null

  if (a.viability === 'blocked') {
    // Full cloud block prevents everything else from mattering tonight, but a
    // standing light dome is still worth naming — it won't clear with the sky.
    const domeAside = a.light_dome_caution
      ? ` The light dome toward the ${a.look_direction} would degrade the view even under clear sky.`
      : ''
    return {
      aurora: a, state: 'blocked',
      prefix: lead + tierClause.replace(/\.$/, ''),
      warning: ', but cloud cover during the aurora window will prevent visibility.',
      suffix: `${domeAside} Consider another location for better viewing.`,
      peakWindowLocal,
    }
  }

  if (a.viability === 'degraded') {
    // Name EVERY impeding factor — listing only the transient one (clouds)
    // implies the standing one (the light dome) isn't a problem tonight.
    const factors: string[] = []
    if (a.blockers.includes('cloud')) factors.push('partial cloud cover during the aurora window')
    if (a.light_dome_caution) factors.push(`the light dome toward the ${a.look_direction}`)
    return {
      aurora: a, state: 'degraded',
      prefix: lead + tierClause.replace(/\.$/, ''),
      warning: `, though ${factors.join(' and ')} will degrade the view.`,
      suffix: null,
      peakWindowLocal,
    }
  }

  return {
    aurora: a, state: 'ok',
    prefix: lead + tierClause,
    warning: null,
    suffix: null,
    peakWindowLocal,
  }
}

export function AuroraAlertBanner({ alert }: { alert: AuroraAlert }) {
  // Reuses the meteor banner classes wholesale — normal, red-mode, and
  // responsive CSS all inherit; .aurora-alert-banner is a discriminator only.
  return (
    <div className={`meteor-alert-banner aurora-alert-banner state-${alert.state}`}>
      <div className="mab-headline">
        {alert.prefix}
        {alert.warning && <span className="mab-warning">{alert.warning}</span>}
        {alert.suffix}
      </div>
      <div className="mab-sub-row">
        {alert.peakWindowLocal && (
          <span className="mab-peak-time">
            <InfoTip tip={<>Highest forecast Kp bins overlapping tonight's darkness — NOAA's 3-hour planetary index, so treat the bounds as soft. Substorms flare and fade on minutes inside a window like this.</>}>
              Peak window {alert.peakWindowLocal} local
            </InfoTip>
          </span>
        )}
      </div>
    </div>
  )
}

// ── Aurora card (Prominent Sky Features) ─────────────────────────────────────

const _TIER_LABEL: Record<AuroraForecast['tier'], string> = {
  overhead:     'Sky-filling display overhead',
  naked_eye:    'Naked eye, low on the horizon',
  photographic: 'Camera-only glow on the horizon',
}

export function AuroraCard({ aurora, report }: { aurora: AuroraForecast; report: NightReport }) {
  const a  = aurora
  const tz = report.tz_name
  const sourceLabel =
    a.kp_source === 'outlook' ? '27-day outlook'
    : a.kp_source === 'predicted' ? 'forecast'
    : a.kp_source === 'estimated' ? 'estimated' : 'observed'

  return (
    <div className="ms-card">
      <div className="ms-header-row">
        <span className="ms-name">Aurora</span>
        <span className="ms-zhr">
          <InfoTip tip={<>Kp — planetary geomagnetic index (0–9). Higher Kp pushes the auroral oval toward the equator; this site's geomagnetic latitude of {a.maglat_deg.toFixed(1)}° needs the viewline at {a.viewline_maglat_deg.toFixed(1)}° or lower.</>}>
            Kp {a.kp_max.toFixed(1)}{a.noaa_scale ? ` · ${a.noaa_scale}` : ''}
          </InfoTip>
        </span>
        <span className="ms-local-rate">{sourceLabel}{a.stale ? ' · stale' : ''}</span>
      </div>
      <div className="mw-row">
        <span className="mw-label">Visibility</span>
        <span>{_TIER_LABEL[a.tier]}</span>
      </div>
      <div className="mw-row">
        <span className="mw-label">Look</span>
        <span>{a.look_direction} ({Math.round(a.look_bearing_deg)}°)</span>
      </div>
      {a.peak_start_utc && a.peak_end_utc && (
        <div className="mw-row">
          <span className="mw-label">Peak window</span>
          <span>{formatTime(a.peak_start_utc, tz)} – {formatTime(a.peak_end_utc, tz)}</span>
        </div>
      )}
      {a.light_dome_caution && (
        <div className="mw-row">
          <span className="mw-label">Caution</span>
          <span>Light dome toward the {a.look_direction} washes out the lower glow</span>
        </div>
      )}
      {a.blockers.length > 0 && (
        <div className="mw-row">
          <BlockerBadge blockers={a.blockers} />
        </div>
      )}
    </div>
  )
}
