import React, { useState, useRef, useEffect, useMemo } from 'react'
import type { NightReport, VisibleTarget, MilkyWaySummary, Direction } from '../types'
import { formatTime, cardinal, rateConditions, scoreBand, scoreLabel, resolveMoonSeverity, showAodAmplifyTip, AOD_AMPLIFY_TIP_COPY } from '../format'
import { ScoreBar, InfoTip } from '../shared'
import { WmoIcon } from './icons'
import { fmtPos } from './common'
import { LD_DIRS, LD_DIR_AZ, LD_MINOR, glowToward, glowLabel, glowStyle, archGlowAt, archSegmentBrightness } from './glow'
import { bestWindow, skyCondition, skyClass, wxAtTime } from './Targets'

// ── Milky Way card ───────────────────────────────────────────────────────────

// Waypoints disclosure — closed by default with Phase 3 density reductions applied inside.
export function WaypointsAccordion({ waypoints, summary, report }: {
  waypoints: VisibleTarget[]
  summary: MilkyWaySummary
  report: NightReport
}) {
  const tz = report.tz_name
  const detailsRef = useRef<HTMLDetailsElement>(null)

  useEffect(() => {
    const el = detailsRef.current
    if (!el) return
    const handler = () => {
      if (el.open) el.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
    el.addEventListener('toggle', handler)
    return () => el.removeEventListener('toggle', handler)
  }, [])

  return (
    <details ref={detailsRef} className="mw-waypoints-detail">
      <summary className="mw-waypoints-summary">
        Galactic Plane Waypoints ({summary.n_visible})
      </summary>
      <div className="tg-table-wrap mw-waypoints-table-wrap">
        <table className="tg-table">
          <thead>
            <tr>
              <th>Waypoint</th>
              <th>Best</th>
              <th></th>
              <th>Window</th>
            </tr>
          </thead>
          <tbody>
            {waypoints.map(t => {
              const w = bestWindow(t)
              if (!w.peak_time || w.peak_alt_deg == null) {
                return (
                  <tr key={t.name}>
                    <td>{t.name}</td>
                    <td className="wx-num">—</td>
                    <td className="wx-num">—</td>
                  </tr>
                )
              }
              const archAngle = w.arch_angle_deg
              const archBadge = archAngle != null && (archAngle < 35 || archAngle >= 60)
                ? <span className="tg-note"> · {archAngle.toFixed(0)}° {archAngle >= 60 ? 'steep' : 'flat'}</span>
                : null
              const glow = report.light_dome
                ? glowToward(report.light_dome, w.peak_az_deg, w.peak_alt_deg)
                : null
              const showGlow = glow != null && glow >= 0.03
              const bestT = w.best_time ?? w.peak_time
              const wxPt = !report.wx_no_data && !report.wx_pending
                ? wxAtTime(report.weather_points || [], bestT)
                : null
              const waypointCloudy = wxPt != null && wxPt.cloud_cover_pct != null && wxPt.cloud_cover_pct > 70
              if (waypointCloudy) return (
                <tr key={t.name} className="tg-row-blocked">
                  <td>{t.name}</td>
                  <td className="wx-num" colSpan={3} style={{textAlign: 'center'}}>
                    <span className="mw-moon-badge badge-poor">Clouded out</span>
                  </td>
                </tr>
              )
              const sky = skyCondition(
                bestT, report.dark_intervals, report.night_start, report.night_end,
                report.illumination_pct, report.moonrise, report.moonset,
                w.moon_sep_at_peak_deg, w.moon_alt_at_peak_deg, w.moon_wash_severity,
              )
              const wpAodTip = showAodAmplifyTip(
                resolveMoonSeverity(w.moon_wash_severity, report.illumination_pct,
                                    w.moon_sep_at_peak_deg, w.moon_alt_at_peak_deg),
                report.night_aod,
              )
              const moonBadgeSpan = <span className={`tg-sky-inline ${skyClass(sky)}`}>{' '}{sky}</span>
              const moonBadge = sky.startsWith('Moon')
                ? (wpAodTip ? <InfoTip tip={<>{AOD_AMPLIFY_TIP_COPY}</>}>{moonBadgeSpan}</InfoTip> : moonBadgeSpan)
                : null
              return (
                <tr key={t.name}>
                  <td>
                    {t.name}
                    {showGlow && (
                      <span className="tg-glow-inline cond-glow" style={glowStyle(glow!)}>
                        {` · glow ${glowLabel(glow!)}`}
                      </span>
                    )}
                  </td>
                  <td className="wx-num">
                    <span className="tg-t">{formatTime(bestT, tz)}</span>
                    <span className="tg-p"> · Alt </span>
                    <span className="tg-alt">{Math.round(w.peak_alt_deg)}°</span>
                    <span className="tg-p"> · Az </span>
                    <span className="tg-az">{Math.round(w.peak_az_deg)}°</span>
                    <span className="tg-p"> </span>
                    <span className="tg-dir">{cardinal(w.peak_az_deg)}</span>
                    {archBadge}
                    {moonBadge}
                  </td>
                  <td className="wx-num tg-cond-col">
                    {wxPt && (
                      <span className={`tg-wx-inline wx-rating-${scoreBand(rateConditions(wxPt))}`}>
                        <WmoIcon code={wxPt.weather_code} size={12} />
                      </span>
                    )}
                  </td>
                  <td className="wx-num wp-window-td">
                    <span className="tg-t">{formatTime(w.start, tz)}</span>
                    <span className="tg-p"> – </span>
                    <span className="tg-t">{formatTime(w.end, tz)}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </details>
  )
}

export function MoonBadge({ type, severity, aodTip }: { type: 'penalty' | 'limited'; severity?: string | null; aodTip?: boolean }) {
  const base = type === 'penalty' ? 'Moon interference' : 'Moon limited'
  const text = severity ? `${base}: ${severity}` : base
  return (
    <InfoTip tip={<>Moon wash — scattered moonlight brightening the sky along this line of sight (Krisciunas &amp; Schaefer 1991; Winkler 2022). Severity comes from phase, moon altitude, angular separation, and aerosols, not illumination % alone.{aodTip ? <> {AOD_AMPLIFY_TIP_COPY}</> : null}</>}>
      <span className="mw-moon-badge">{text}</span>
    </InfoTip>
  )
}


// IAU (1958) galactic → ICRS rotation matrix (mirrors milky_way.py exactly).
const _GAL_TO_ICRS = [
  [-0.0548755604, +0.4941094279, -0.8676661490],
  [-0.8734370902, -0.4448296300, -0.1980763734],
  [-0.4838350155, +0.7469822445, +0.4559837762],
]




// Full galactic → horizontal (Alt/Az) transformation at a given UTC instant.
// l_deg, b_deg   — galactic coordinates
// lat_deg, lon_deg — observer position
// utcMs          — UTC milliseconds since Unix epoch
export function galToAltAz(l_deg: number, b_deg: number, lat_deg: number, lon_deg: number, utcMs: number) {
  const toRad = (d: number) => d * Math.PI / 180
  const l = toRad(l_deg), b = toRad(b_deg)
  const xg = Math.cos(b) * Math.cos(l)
  const yg = Math.cos(b) * Math.sin(l)
  const zg = Math.sin(b)
  const R  = _GAL_TO_ICRS
  const xi = R[0][0]*xg + R[0][1]*yg + R[0][2]*zg
  const yi = R[1][0]*xg + R[1][1]*yg + R[1][2]*zg
  const zi = R[2][0]*xg + R[2][1]*yg + R[2][2]*zg
  const ra_rad  = Math.atan2(yi, xi)
  const dec_rad = Math.asin(Math.max(-1, Math.min(1, zi)))
  // GMST (degrees) via Meeus Ch.12 — valid at any time of day, not just 0h UT
  const jd      = utcMs / 86_400_000 + 2_440_587.5
  const D       = jd - 2_451_545.0                    // days from J2000.0
  const T       = D / 36_525.0
  const gmst_deg = ((280.46061837 + 360.98564736629 * D + 0.000387933 * T * T - T * T * T / 38_710_000) % 360 + 360) % 360
  const lst_rad  = toRad(gmst_deg + lon_deg)
  const ha_rad  = lst_rad - ra_rad
  const lat_rad = toRad(lat_deg)
  const alt = Math.asin(
    Math.sin(dec_rad) * Math.sin(lat_rad) +
    Math.cos(dec_rad) * Math.cos(lat_rad) * Math.cos(ha_rad)
  )
  const az = Math.atan2(
    -Math.cos(dec_rad) * Math.sin(ha_rad),
    Math.sin(dec_rad) * Math.cos(lat_rad) - Math.cos(dec_rad) * Math.sin(lat_rad) * Math.cos(ha_rad)
  )
  return {
    alt: alt * 180 / Math.PI,
    az:  ((az  * 180 / Math.PI) + 360) % 360,
  }
}

// Simplified moon position (Meeus Ch.47, largest perturbation terms).
// Accurate to ~0.3° — sufficient for the dome visualization glow blob.
export function moonAltAz(lat: number, lon: number, utcMs: number): { alt: number; az: number } {
  const r   = (d: number) => d * Math.PI / 180
  const mod = (x: number) => ((x % 360) + 360) % 360
  const JD  = utcMs / 86_400_000 + 2_440_587.5
  const D   = JD - 2_451_545.0
  const T   = D / 36_525.0

  // Fundamental arguments (degrees)
  const Lp = mod(218.3164477 + 481267.88123421 * T)
  const Mp = mod(134.9633964 + 477198.8675055  * T)
  const M  = mod(357.5291092 + 35999.0502909   * T)
  const Dg = mod(297.8501921 + 445267.1114034  * T)
  const F  = mod(93.2720950  + 483202.0175233  * T)

  // Ecliptic longitude (10 largest terms, coefficients ×1e-6 degrees)
  const ΣL = (
    + 6288774 * Math.sin(r(Mp))
    + 1274027 * Math.sin(r(2*Dg - Mp))
    +  658314 * Math.sin(r(2*Dg))
    +  213618 * Math.sin(r(2*Mp))
    -  185116 * Math.sin(r(M))
    -  114332 * Math.sin(r(2*F))
    +   58793 * Math.sin(r(2*Dg - 2*Mp))
    +   57066 * Math.sin(r(2*Dg - M - Mp))
    +   53322 * Math.sin(r(2*Dg + Mp))
    +   45758 * Math.sin(r(2*Dg - M))
  ) / 1e6

  // Ecliptic latitude (6 largest terms)
  const ΣB = (
    + 5128122 * Math.sin(r(F))
    +  280602 * Math.sin(r(Mp + F))
    +  277693 * Math.sin(r(Mp - F))
    +  173237 * Math.sin(r(2*Dg - F))
    +   55413 * Math.sin(r(2*Dg - Mp + F))
    +   46271 * Math.sin(r(2*Dg - Mp - F))
  ) / 1e6

  const λ = r(mod(Lp + ΣL))
  const β = r(ΣB)
  const ε = r(23.4393 - 0.013004 * T)  // obliquity of ecliptic

  // Ecliptic → equatorial
  const ra  = Math.atan2(Math.sin(λ) * Math.cos(ε) - Math.tan(β) * Math.sin(ε), Math.cos(λ))
  const dec = Math.asin(Math.sin(β) * Math.cos(ε) + Math.cos(β) * Math.sin(ε) * Math.sin(λ))

  // GMST (Meeus Ch.12) → HA — same formula as galToAltAz
  const gmst = r(mod(280.46061837 + 360.98564736629 * D + 0.000387933 * T * T - T * T * T / 38_710_000))
  const ha   = gmst + r(lon) - ra
  const latR = r(lat)

  const sinAlt = Math.sin(dec) * Math.sin(latR) + Math.cos(dec) * Math.cos(latR) * Math.cos(ha)
  const alt    = Math.asin(Math.max(-1, Math.min(1, sinAlt))) * 180 / Math.PI
  const az     = (Math.atan2(
    -Math.cos(dec) * Math.sin(ha),
    Math.sin(dec) * Math.cos(latR) - Math.cos(dec) * Math.sin(latR) * Math.cos(ha),
  ) * 180 / Math.PI + 360) % 360
  return { alt, az }
}

// The arch traces the galactic equator (b=0), sampled every 5° of galactic longitude.
export function MilkyWayDome({ summary, waypoints, report }: { summary: MilkyWaySummary; waypoints: VisibleTarget[]; report: NightReport }) {
  const [heading, setHeading] = useState<number>(
    summary.core_peak_az_deg != null ? Math.round(summary.core_peak_az_deg) : 180
  );
  const [tilt, setTilt] = useState<number>(0);
  const pointerRef = useRef<{ x: number; y: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  // 1. ADDED HERE: State to track which dot is currently being hovered
  const [hoveredDot, setHoveredDot] = useState<{name: string, x: number, y: number} | null>(null);

  // Native touch handlers for iOS Safari — PointerEvents + setPointerCapture can be
  // unreliable on iOS when touch-action isn't applied before the first touch. Native
  // listeners with passive:false let us call preventDefault() to block page scroll.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const fConst = 100 / Math.tan(60 * Math.PI / 180);
    let lastX = 0, lastY = 0;
    const onTouchStart = (e: TouchEvent) => {
      e.preventDefault();
      e.stopPropagation();
      lastX = e.touches[0].clientX;
      lastY = e.touches[0].clientY;
    };
    const onTouchMove = (e: TouchEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (!e.touches[0]) return;
      const dx = e.touches[0].clientX - lastX;
      const dy = e.touches[0].clientY - lastY;
      lastX = e.touches[0].clientX;
      lastY = e.touches[0].clientY;
      const rect = svg.getBoundingClientRect();
      const sens = (180 / Math.PI) / fConst;
      setHeading(h => ((h + dx * (180 / rect.width) * sens) % 360 + 360) % 360);
      setTilt(t => Math.max(0, Math.min(45, t - dy * (120 / rect.height) * sens)));
    };
    svg.addEventListener('touchstart', onTouchStart, { passive: false });
    svg.addEventListener('touchmove', onTouchMove, { passive: false });
    return () => {
      svg.removeEventListener('touchstart', onTouchStart);
      svg.removeEventListener('touchmove', onTouchMove);
    };
  }, []);

  if (summary.core_peak_alt_deg == null || summary.core_peak_alt_deg <= 0) {
    return (
      <div className="mw-dome-absent">
        Arch below horizon tonight
        <div className="mw-absent-reason">The Galactic Core is currently out of view.</div>
      </div>
    );
  }

  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const peakTimeMs = new Date(summary.core_peak_time).getTime();

  type EqSample = { l: number; alt: number; az: number; }
  type ArchSegment = { x1: number; y1: number; x2: number; y2: number; glowOpacity: number; coreOpacity: number }

  // Galactic-to-horizontal conversion depends only on location and peak time —
  // memoised so it does NOT recompute on every heading/tilt drag frame.
  const allEquatorSamples = useMemo<EqSample[]>(() =>
    Array.from({ length: 72 }, (_, i) => {
      const l = i * 5;
      const { alt, az } = galToAltAz(l, 0, report.lat, report.lon, peakTimeMs);
      return { l, alt, az };
    }),
    [report.lat, report.lon, peakTimeMs]
  );

  // Arch segment brightness only depends on sky position + light dome — memoised
  // separately so it survives heading/tilt state changes.
  const equatorBrightness = useMemo(() =>
    allEquatorSamples.map(s =>
      archSegmentBrightness(s.l, s.alt, s.az, report.light_dome ?? null)
    ),
    [allEquatorSamples, report.light_dome]
  );

  // Gnomonic (rectilinear/perspective) projection — models a wide-angle camera
  // pointed at the horizon along `heading`. Great circles project to straight lines.
  const FOV_HALF_DEG = 60;                        // 120° total horizontal FoV
  const f = 100 / Math.tan(toRad(FOV_HALF_DEG)); // focal length ~57.7 SVG units

  const project = (alt: number, az: number) => {
    const altR = toRad(alt);
    const azR  = toRad(az - heading);
    const tiltR = toRad(tilt);
    const cosAlt = Math.cos(altR), sinAlt = Math.sin(altR);
    const cosAzR = Math.cos(azR), sinAzR = Math.sin(azR);
    const cosT = Math.cos(tiltR), sinT = Math.sin(tiltR);
    const dx = cosAlt * sinAzR;
    const dy = sinAlt * cosT - cosAlt * cosAzR * sinT;
    const dz = cosAlt * cosAzR * cosT + sinAlt * sinT;
    if (dz <= 0) return { x: 100, y: 100, isFront: false };
    return {
      x: 100 + f * (dx / dz),
      y: 100 - f * (dy / dz),
      isFront: true,
    };
  };

  // Horizon y-position in SVG coords — moves down as camera tilts up; clips to frame bottom.
  const horizonY = Math.min(120, 100 + f * Math.tan(toRad(tilt)));

  // Map the known Milky Way waypoint names to their exact Galactic Longitude (l)
  const WAYPOINT_L: Record<string, number> = {
    'Galactic Anticenter': 180,
    'Cassiopeia/Perseus': 135,
    'Cepheus Cloud': 105,
    'Cygnus Star Cloud': 80,
    'Aquila Rift': 45,
    'Scutum Star Cloud': 27,
    'Galactic Core': 0,
    'Scorpius Star Cloud': 347,
    'Norma Star Cloud': 330,
    'Crux & Coalsack': 302,
    'Carina Nebula & Cloud': 287,
    'Vela Supernova Region': 265,
    'Puppis Star Cloud': 245,
    'Monoceros': 210,
  };

  // Waypoint alt/az is also stable wrt heading/tilt — memoised separately.
  const wpDotsRaw = useMemo(() =>
    waypoints
      .map(wp => {
        const l = WAYPOINT_L[wp.name];
        if (l != null) {
          const { alt, az } = galToAltAz(l, 0, report.lat, report.lon, peakTimeMs);
          return { name: wp.name, alt, az };
        }
        const w = bestWindow(wp);
        return { name: wp.name, alt: w.peak_alt_deg ?? -1, az: w.peak_az_deg ?? 0 };
      })
      .filter(p => p.alt > 0),
    [waypoints, report.lat, report.lon, peakTimeMs]
  );

  // Project waypoints using current heading/tilt (view-dependent).
  const wpDots = wpDotsRaw.map(p => ({ ...p, proj: project(p.alt, p.az) }));

  const doubled = [...allEquatorSamples, ...allEquatorSamples];
  let longestStreak: EqSample[] = [];
  let currentStreak: EqSample[] = [];
  for (const s of doubled) {
    if (s.alt > -2) currentStreak.push(s);
    else {
      if (currentStreak.length > longestStreak.length) longestStreak = currentStreak;
      currentStreak = [];
    }
  }
  const visibleArc = longestStreak.slice(0, 73).map(s => ({ ...s, proj: project(s.alt, s.az) }))

  // Build per-segment brightness using the pre-computed equatorBrightness cache.
  // l is always a multiple of 5 in [0,355], so index = (l/5) % 72.
  const visibleSegments: ArchSegment[] = []
  for (let i = 0; i < visibleArc.length - 1; i++) {
    const a = visibleArc[i], b = visibleArc[i + 1]
    if (!a.proj.isFront || !b.proj.isFront) continue
    const bA = equatorBrightness[Math.round(a.l / 5) % 72]
    const bB = equatorBrightness[Math.round(b.l / 5) % 72]
    visibleSegments.push({
      x1: a.proj.x, y1: a.proj.y,
      x2: b.proj.x, y2: b.proj.y,
      glowOpacity: (bA.glowOpacity + bB.glowOpacity) / 2,
      coreOpacity: (bA.coreOpacity + bB.coreOpacity) / 2,
    })
  }
  const corePos = project(summary.core_peak_alt_deg, summary.core_peak_az_deg ?? 0);

  // Altitude reference rings for photographer framing (20° and 40°).
  // In gnomonic, constant-altitude loci curve upward at the edges — rendered as polylines.
  const ALT_RINGS = [20, 40] as const;
  const ringPolylines = ALT_RINGS.map(alt => {
    const pts: string[] = [];
    for (let dAz = -FOV_HALF_DEG; dAz <= FOV_HALF_DEG; dAz += 1) {
      const p = project(alt, heading + dAz);
      if (p.isFront) pts.push(`${p.x.toFixed(1)},${p.y.toFixed(1)}`);
    }
    return { alt, points: pts.join(' ') };
  });

  // Left-edge label anchor for each ring — sampled ~54° left of center so x≈20 near frame edge.
  const ringLabels = ALT_RINGS.map(alt => {
    const p = project(alt, heading - 54);
    if (!p.isFront || p.x < 11 || p.x > 100) return null;
    return { alt, x: p.x, y: p.y };
  });

  // Sky glow blobs: one per dome direction, anchored 5° above horizon so the gradient
  // peak sits just inside the dome rather than exactly on the arc edge.
  type DomeGlow = { dir: Direction; x: number; y: number; r: number; op: number }
  const domeGlows: DomeGlow[] = report.light_dome
    ? LD_DIRS.flatMap(d => {
        const score = report.light_dome!.scores[d] ?? 0
        if (score < LD_MINOR) return []
        const pos = project(5, LD_DIR_AZ[d])
        if (!pos.isFront) return []
        const r  = Math.min(85, 30 + 18 * Math.log1p(score))
        const op = Math.min(0.50, 0.12 + 0.15 * Math.log1p(score))
        return [{ dir: d, x: pos.x, y: pos.y, r, op }]
      })
    : []

  // Moon glow blob: position at arch peak time, brightness scales with illumination.
  // Only shown when moon is above the horizon and meaningfully illuminated (≥5%).
  const moonGlowPos = (() => {
    if (report.illumination_pct < 5) return null
    const { alt, az } = moonAltAz(report.lat, report.lon, peakTimeMs)
    if (alt <= 0) return null
    const pos = project(alt, az)
    if (!pos.isFront) return null
    const illumFrac = report.illumination_pct / 100
    return {
      x: pos.x, y: pos.y,
      r:  Math.min(70, 15 + 55 * illumFrac),
      op: Math.min(0.45, 0.05 + 0.40 * illumFrac),
    }
  })()

  const cardinals = [
    { deg: 0, label: 'N' }, { deg: 45, label: 'NE' }, { deg: 90, label: 'E' },
    { deg: 135, label: 'SE' }, { deg: 180, label: 'S' }, { deg: 225, label: 'SW' },
    { deg: 270, label: 'W' }, { deg: 315, label: 'NW' }
  ];

  // 1px drag → (180/displayWidth) SVG units → (180/π)/f degrees of heading/tilt change.
  const handlePointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    pointerRef.current = { x: e.clientX, y: e.clientY };
    (e.currentTarget as SVGSVGElement).setPointerCapture(e.pointerId);
  };
  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!pointerRef.current) return;
    const dx = e.clientX - pointerRef.current.x;
    const dy = e.clientY - pointerRef.current.y;
    pointerRef.current = { x: e.clientX, y: e.clientY };
    const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
    const sens = (180 / Math.PI) / f;                    // ~1°/SVG unit
    const dxSvg = dx * (180 / rect.width) * sens;        // heading degrees
    const dySvg = dy * (120 / rect.height) * sens;        // tilt degrees
    setHeading(h => ((h + dxSvg) % 360 + 360) % 360);
    setTilt(t => Math.max(0, Math.min(45, t - dySvg)));
  };
  const handlePointerUp = () => { pointerRef.current = null; };

  return (
    <div className="mw-dome-wrap">
      <div className="mw-dome-title">360° Sky Dome</div>
      <svg
        ref={svgRef}
        viewBox="10 0 180 120"
        xmlns="http://www.w3.org/2000/svg"
        className="mw-dome-svg"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerUp}
      >
        <defs>
          <clipPath id="mw-half-dome-clip">
            <rect x="0" y="0" width="200" height={horizonY} />
          </clipPath>
          <filter id="mw-f-band" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur in="SourceGraphic" stdDeviation="3" />
          </filter>
          {domeGlows.map(g => (
            <radialGradient key={g.dir} id={`mw-ldg-${g.dir}`}
              cx={g.x} cy={g.y} r={g.r} gradientUnits="userSpaceOnUse">
              <stop offset="0%"   stopColor="currentColor" stopOpacity={g.op} />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </radialGradient>
          ))}
          {moonGlowPos && (
            <radialGradient id="mw-moon-g"
              cx={moonGlowPos.x} cy={moonGlowPos.y} r={moonGlowPos.r} gradientUnits="userSpaceOnUse">
              <stop offset="0%"   stopColor="currentColor" stopOpacity={moonGlowPos.op} />
              <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
            </radialGradient>
          )}
        </defs>

        <rect x="0" y="0" width="200" height="120" fill="rgba(10, 15, 30, 0.4)" />
        {horizonY < 120 && (
          <rect className="mw-dome-ground" x="0" y={horizonY} width="200" height={120 - horizonY} />
        )}

        {/* Sky glow from light domes — color controlled via .mw-dome-glow for red-mode compliance */}
        {domeGlows.length > 0 && (
          <g className="mw-dome-glow" clipPath="url(#mw-half-dome-clip)">
            {domeGlows.map(g => (
              <circle key={g.dir} cx={g.x} cy={g.y} r={g.r}
                fill={`url(#mw-ldg-${g.dir})`} />
            ))}
            {domeGlows.map(g => (
              <circle key={`${g.dir}-hit`} cx={g.x} cy={g.y} r="20"
                fill="transparent" pointerEvents="all" style={{ cursor: 'pointer' }}
                onPointerDown={(e) => e.stopPropagation()}
                onMouseEnter={() => setHoveredDot({ name: `${g.dir} sky glow`, x: g.x, y: g.y })}
                onMouseLeave={() => setHoveredDot(null)} />
            ))}
          </g>
        )}

        {/* Moon glow — color controlled via .mw-moon-glow for red-mode compliance */}
        {moonGlowPos && (
          <g className="mw-moon-glow" clipPath="url(#mw-half-dome-clip)">
            <circle cx={moonGlowPos.x} cy={moonGlowPos.y} r={moonGlowPos.r} fill="url(#mw-moon-g)" />
            <circle cx={moonGlowPos.x} cy={moonGlowPos.y} r="2" fill="currentColor" opacity="0.75" />
            <circle cx={moonGlowPos.x} cy={moonGlowPos.y} r="18"
              fill="transparent" pointerEvents="all" style={{ cursor: 'pointer' }}
              onPointerDown={(e) => e.stopPropagation()}
              onMouseEnter={() => setHoveredDot({ name: 'Moon', x: moonGlowPos.x, y: moonGlowPos.y })}
              onMouseLeave={() => setHoveredDot(null)} />
          </g>
        )}

        <g clipPath="url(#mw-half-dome-clip)">
          {/* GLOW LAYER: blurred wide band; filter on the group blends joints between segments */}
          <g filter="url(#mw-f-band)" className="mw-arch-glow">
            {visibleSegments.map((seg, i) => (
              <line
                key={i}
                x1={seg.x1} y1={seg.y1} x2={seg.x2} y2={seg.y2}
                stroke="currentColor"
                strokeWidth="14"
                strokeOpacity={seg.glowOpacity}
              />
            ))}
          </g>
          {/* CORE LAYER: thin bright stripe, variable opacity per segment */}
          <g className="mw-arch-core">
            {visibleSegments.map((seg, i) => (
              <line
                key={i}
                x1={seg.x1} y1={seg.y1} x2={seg.x2} y2={seg.y2}
                stroke="currentColor"
                strokeWidth="1.5"
                strokeOpacity={seg.coreOpacity}
              />
            ))}
          </g>
          {/* Altitude reference rings — perspective curves at 20° and 40° */}
          {ringPolylines.map(ring => ring.points && (
            <polyline key={ring.alt} className="mw-dome-ring" points={ring.points} fill="none" />
          ))}
          {ringLabels.map(lbl => lbl && (
            <text key={`rl-${lbl.alt}`} className="mw-dome-ring-label"
              x={lbl.x + 2} y={lbl.y - 2} textAnchor="start">{lbl.alt}°</text>
          ))}
        </g>

        {/* 2. REPLACED HERE: The Galactic Core */}
        {corePos.isFront && (
          <g>
            <circle className="mw-dome-core" cx={corePos.x} cy={corePos.y} r="3.5" pointerEvents="none" />
            <circle
              cx={corePos.x} cy={corePos.y} r="12" fill="transparent" pointerEvents="all" style={{ cursor: 'pointer' }}
              onPointerDown={(e) => e.stopPropagation()}
              onMouseEnter={() => setHoveredDot({ name: 'Galactic Core', x: corePos.x, y: corePos.y })}
              onMouseLeave={() => setHoveredDot(null)}
            />
          </g>
        )}

        {/* 3. REPLACED HERE: Waypoints Array */}
        {wpDots.map((wp, i) => (
          wp.proj.isFront && (
            <g key={i}>
              <circle cx={wp.proj.x} cy={wp.proj.y} r="2" className="mw-dome-waypoint" pointerEvents="none" />
              <circle
                cx={wp.proj.x} cy={wp.proj.y} r="12" fill="transparent" pointerEvents="all" style={{ cursor: 'pointer' }}
                onPointerDown={(e) => e.stopPropagation()}
                onMouseEnter={() => setHoveredDot({ name: wp.name, x: wp.proj.x, y: wp.proj.y })}
                onMouseLeave={() => setHoveredDot(null)}
              />
            </g>
          )
        ))}

        {horizonY < 120 && (
          <line className="mw-dome-horizon" x1="0" y1={horizonY} x2="200" y2={horizonY} />
        )}
        <rect className="mw-dome-frame" x="10.5" y="0.5" width="179" height="119" fill="none" />

        {horizonY < 120 && cardinals.map(c => {
          let relAz = c.deg - heading;
          while (relAz <= -180) relAz += 360;
          while (relAz > 180) relAz -= 360;

          if (Math.abs(relAz) < FOV_HALF_DEG) {
            // Gnomonic + tilt: horizon objects at azimuth relAz project to:
            // x = 100 + f * tan(relAz) / cos(tilt)
            const x = 100 + f * Math.tan(toRad(relAz)) / Math.cos(toRad(tilt));
            const labelY = horizonY + 14;
            if (x < 11 || x > 189 || labelY > 119) return null;
            return (
              <g key={c.label}>
                <line className="mw-dome-tick" x1={x} y1={horizonY} x2={x} y2={horizonY + 3} />
                <text className="mw-dome-label" x={x} y={labelY} textAnchor="middle">{c.label}</text>
              </g>
            );
          }
          return null;
        })}

        {/* 4. ADDED HERE: The Custom Tooltip directly inside the SVG */}
        {hoveredDot && (
          <foreignObject x={hoveredDot.x - 75} y={hoveredDot.y - 35} width="150" height="30" pointerEvents="none">
            <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'flex-end', height: '100%' }}>
              <div style={{
                background: 'rgba(8, 13, 28, 0.95)',
                color: 'rgba(200, 212, 238, 0.95)',
                border: '1px solid rgba(90, 120, 200, 0.25)',
                borderRadius: '2px',
                padding: '3px 7px',
                fontSize: '7px',
                fontFamily: 'var(--mono)',
                letterSpacing: '0.05em',
                textTransform: 'uppercase',
                whiteSpace: 'nowrap',
                boxShadow: '0 2px 8px rgba(0,0,0,0.8)'
              }}>
                {hoveredDot.name}
              </div>
            </div>
          </foreignObject>
        )}
      </svg>
      <div className="mw-dome-subtitle">Light pollution vs. Milky Way plane</div>
    </div>
  );
}

export function MilkyWayAbsent({ report: r }: { report: NightReport }) {
  const coreMaxAlt = Math.max(0, 90 - Math.abs(r.lat - (-28.9)))
  const bortle     = r.light_pollution?.bortle_class ?? 0
  const bortleDesc = r.light_pollution?.bortle_desc  ?? 'bright sky'

  let reason: string
  if (coreMaxAlt < 10) {
    reason = `Galactic core never rises above 10° from this latitude (max ${coreMaxAlt.toFixed(0)}° altitude)`
  } else if (bortle >= 6) {
    reason = `${bortleDesc} (Bortle ${bortle}) — light pollution prevents Milky Way visibility here`
  } else if (bortle >= 4) {
    reason = `Suburban skies (Bortle ${bortle}) are too bright for Milky Way visibility here`
  } else if (r.dark_intervals.length === 0) {
    reason = `Bright moon (${r.illumination_pct.toFixed(0)}%) is up all night — no dark sky window`
  } else {
    reason = 'Galactic core is below the horizon during tonight\'s dark window'
  }

  return <p className="mw-absent-reason">{reason}</p>
}

export function MilkyWayCard({ summary, waypoints, report }: {
  summary: MilkyWaySummary
  waypoints: VisibleTarget[]
  report: NightReport
}) {
  const tz = report.tz_name
  const s  = summary

  const archQuality = s.arch_angle_deg != null
    ? (s.arch_angle_deg >= 60 ? 'steep' : s.arch_angle_deg >= 35 ? 'moderate' : 'flat')
    : null

  // Directions where dome glow visibly dims the arch at peak time.
  // Uses archGlowAt (40° characteristic alt) at each dome direction within ±90° of the
  // core az; flags it when the resulting glow ≥ LD_MINOR (≥18% brightness reduction).
  const domeSections: { dir: Direction; glow: number }[] = (() => {
    if (!report.light_dome || s.core_peak_az_deg == null) return []
    const coreAz   = s.core_peak_az_deg
    const proxyAlt = Math.max(5, s.core_peak_alt_deg ?? 25)
    return LD_DIRS.flatMap(d => {
      const score = report.light_dome!.scores[d] ?? 0
      if (score < LD_MINOR) return []
      const dirAz = LD_DIR_AZ[d]
      let delta = ((dirAz - coreAz) + 360) % 360
      if (delta > 180) delta = 360 - delta
      if (delta > 90) return []
      const glow = archGlowAt(report.light_dome!, dirAz, proxyAlt)
      if (glow < LD_MINOR) return []
      return [{ dir: d, glow }]
    })
  })()

  const bestLabel = 'Best time'
  const bestTime  = s.best_viewing_time ?? (s.core_peak_in_window ? s.core_peak_time : s.arch_end)

  const moonSeverity = resolveMoonSeverity(
    s.core_moon_severity,
    report.illumination_pct,
    s.core_moon_sep_deg ?? null,
    s.core_moon_alt_deg ?? null,
  )
  const moonAodTip = showAodAmplifyTip(moonSeverity, report.night_aod)

      return (
    <div className="mw-card">
      <div className="mw-meta-block">
        {/* Unified Score Row */}
        <div className="meta-row">
          <span className="meta-k">Score</span>
            {/* Using the standard meta-v class for uniformity */}
          <span className={`meta-v mw-score mw-score-band-${scoreBand(s.local_score)}`}>
            {s.local_score.toFixed(1)}
          <span className="mw-score-denom">{scoreLabel(s.local_score)}</span>
            {s.weather_blocked && <span className="mw-moon-badge badge-poor" style={{marginLeft: 8}}>Clouded out</span>}
            {!s.weather_blocked && s.weather_limited && <span className="mw-moon-badge" style={{marginLeft: 8}}>Partly cloudy</span>}
  </span>
</div>
        {/* Unified Metadata List */}
        <div className="meta-row">
          <span className="meta-k">Arch window</span>
          <span className="meta-v">
            {formatTime(s.arch_start, tz)} – {formatTime(s.arch_end, tz)}
            {'  ·  '}{Math.floor(s.arch_hours)}h {Math.round((s.arch_hours % 1) * 60).toString().padStart(2,'0')}m
            {s.moon_limited && !s.arch_moon_washout && <MoonBadge type="limited" severity={moonSeverity} aodTip={moonAodTip} />}
            {s.weather_limited && !s.weather_blocked && <span className="mw-moon-badge">{`${s.clear_arch_hours.toFixed(1)}h clear`}</span>}
          </span>
        </div>
        <div className="meta-row">
          <span className="meta-k">Galactic core</span>
          <span className="meta-v">
            {fmtPos(s.core_peak_alt_deg, s.core_peak_az_deg)} (max {s.core_max_alt_deg}° alt)
            {archQuality && s.arch_angle_deg != null && `  ·  arch ${s.arch_angle_deg.toFixed(0)}° (${archQuality})`}
          </span>
        </div>
        <div className="meta-row">
          <span className="meta-k">{bestLabel}</span>
          <span className="meta-v">
            {formatTime(bestTime, tz)} — core @ {fmtPos(s.core_peak_alt_deg, s.core_peak_az_deg)}
          </span>
        </div>
      </div>

      {/* New Title Centered Above Both */}
      <div className="mw-group-title">360° Sky View</div>

      {/* 3 & 4: Skydome (Left) and Notes (Right) */}
      <div className="mw-mid-section">

        <div className="mw-dome-container">
          <MilkyWayDome summary={s} waypoints={waypoints} report={report} />
        </div>

        <div className="mw-notes-container">
          {s.moon_penalised && !s.arch_moon_washout && <MoonBadge type="penalty" severity={moonSeverity} aodTip={moonAodTip} />}
          {s.arch_moon_washout && <span className="mw-moon-badge">Moon washout</span>}
          {domeSections.length > 0 && (() => {
            const maxGlow  = Math.max(...domeSections.map(ds => ds.glow))
            return (
              <span className="mw-moon-badge cond-glow" style={glowStyle(maxGlow)}>
                {`Dome glow: ${glowLabel(maxGlow)}`}
              </span>
            )
          })()}
        </div>
      </div>

      <div className="mw-bars-section telemetry-mini-bars">
        <ScoreBar label="Altitude" value={s.alt_score} />
        <ScoreBar label="Coverage" value={s.cov_score} />
        <ScoreBar label="Window" value={s.win_score} />
      </div>

      {waypoints.length > 0 && (
        <WaypointsAccordion waypoints={waypoints} summary={s} report={report} />
      )}
    </div>
  )
}
