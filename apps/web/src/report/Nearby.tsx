import type { NearbyPlace, NearbyResult } from '../types'
import { fmtDist } from '../format'
import { Navigation } from 'lucide-react'

// ── Nearby dark-sky results ──────────────────────────────────────────────────

export function nearbyBortleClass(bortleClass: number | null): string {
  if (bortleClass == null) return 'nearby-bortle'
  let colorClass = 'nearby-bortle-excellent'
  if (bortleClass <= 2) colorClass = 'nearby-bortle-excellent'
  else if (bortleClass <= 4) colorClass = 'nearby-bortle-good'
  else if (bortleClass <= 6) colorClass = 'nearby-bortle-fair'
  else colorClass = 'nearby-bortle-poor'
  return `nearby-bortle ${colorClass}`
}

export function NearbyResults(
  { data, imperial, originLat, originLon, onSelectLocation }:
  { data: NearbyResult; imperial: boolean; originLat: number; originLon: number; onSelectLocation?: (lat: number, lon: number) => void },
) {
  const { origin_bortle, origin_sqm, radius_miles, results, light_domes, best_available } = data
  const sqmStr = origin_sqm != null ? ` (SQM ${origin_sqm.toFixed(1)})` : ''
  // Drive-time routing only runs on the AWS backend; on local it's never attempted and
  // every place carries drive_minutes=null for that reason alone. Only treat a null as
  // "routing tried and failed" (e.g. ferry-only crossing) when routing is active at all —
  // otherwise every local-backend POI would wrongly lose its Maps directions link below.
  const routingActive = [...results, ...light_domes, ...(best_available ? [best_available] : [])]
    .some(p => p.drive_minutes != null)

  // Convert stored miles to the active unit system
  const fmtMi = (mi: number) => fmtDist(mi * 1.60934, imperial)
  // Prefer actual road distance from the routing API; fall back to straight-line when a
  // candidate wasn't routed (raw "Remote" fallbacks).
  const distOf = (p: NearbyPlace) => fmtMi(p.drive_miles ?? p.distance_miles)
  // Google Maps driving directions, origin → location (falls back to a place pin).
  const dirLink = (p: NearbyPlace) =>
    `https://www.google.com/maps/dir/?api=1&origin=${originLat},${originLon}` +
    `&destination=${p.lat},${p.lon}&travelmode=driving`

  const placeStr = (p: NearbyPlace) =>
    p.name ?? `${p.lat.toFixed(2)}°, ${p.lon.toFixed(2)}°`
  const formatDriveTime = (minutes: number | null): string | null => {
    if (minutes == null) return null
    const hrs = Math.floor(minutes / 60)
    const mins = minutes % 60
    return hrs > 0 ? `${hrs} hr ${mins} min` : `${mins} min`
  }
  const POI_TYPE_LABEL: Record<string, string> = {
    parking: 'Parking', viewpoint: 'Viewpoint', camp_site: 'Campsite', rest_area: 'Rest area',
    caravan_site: 'RV park', picnic_site: 'Picnic area', ranger_station: 'Ranger station',
    observatory: 'Observatory', attraction: 'Attraction', information: 'Info point',
    tourism: 'Tourism', pier: 'Pier', lighthouse: 'Lighthouse', tower: 'Observation tower',
    summer_camp: 'Summer camp', firepit: 'Fire pit', beach_resort: 'Beach resort',
    historic: 'Historic site',
  }
  // A Google Maps driving-directions link, or a "no road access" notice when routing
  // was attempted but couldn't reach this POI (e.g. a ferry-only crossing).
  const mapLinkNode = (p: NearbyPlace) =>
    routingActive && p.is_poi && p.drive_minutes == null ? (
      <span className="poi-unroutable" title="No direct road access — routing avoided a ferry-only crossing">No road access</span>
    ) : (
      <a className="poi-maplink" href={dirLink(p)} target="_blank" rel="noopener noreferrer" aria-label="Directions" onClick={(e) => e.stopPropagation()}><Navigation size={12} strokeWidth={2} /></a>
    )
  // Category badge (routable POIs) or "Remote" tag (off-road fallbacks), any routing
  // warnings, and a Maps link — shared by the prose call sites and the results table.
  // `showMapLink` is false for the table, which renders its own Maps link in the Dir
  // column instead, next to the drive-time estimate.
  const poiMeta = (p: NearbyPlace, showMapLink: boolean) => (
    <span className="poi-type-link">
      {p.is_poi
        ? (p.poi_type && <span className="poi-badge">{POI_TYPE_LABEL[p.poi_type] ?? p.poi_type}</span>)
        : <span className="poi-remote">Remote</span>}
      {p.warnings?.map((warn, idx) => (
        <span key={idx} className="poi-warning">{warn}</span>
      ))}
      {p.tail_miles != null && (
        <span className="poi-warning">Last {fmtMi(p.tail_miles)} not drivable</span>
      )}
      {showMapLink && mapLinkNode(p)}
    </span>
  )
  // Render a place name (as a link) with its category badge/warnings and a Maps link —
  // used by the prose highlight/empty-state lines, which have no table row to attach a
  // click handler to and so keep the original click-through-to-app-link behavior.
  const placeNode = (p: NearbyPlace) => {
    // No name/area passed here — the destination report re-derives the same POI name
    // itself from lat/lon against the trusted local index (darkhours.location
    // .reverse_geocode → darksky._poi_reverse_name), not from anything in the URL.
    const appLink = `?lat=${p.lat.toFixed(5)}&lon=${p.lon.toFixed(5)}`
    return (
      <>
        <a className="poi-namelink" href={appLink}>{placeStr(p)}</a>
        {p.area_name && <span className="poi-area">{p.area_name}</span>}
        {poiMeta(p, true)}
      </>
    )
  }

  return (
    <>
      <div className="meta-row">
        <span className="meta-k">Origin:</span>
        <span className="meta-v"><span className={nearbyBortleClass(origin_bortle)}>Bortle {origin_bortle}</span>{sqmStr}  ·  {fmtMi(radius_miles)} radius</span>
      </div>

      {/* 1. Note when already at Bortle 1 — results still shown below */}
      {origin_bortle <= 1 && results.length > 0 && (
        <p className="sat-notice">
          Already at Bortle {origin_bortle}{sqmStr} — showing other Bortle 1 sites within {fmtMi(radius_miles)}.
        </p>
      )}

      {/* 2. Empty state */}
      {results.length === 0 && (
        <p className="sat-notice">
          {origin_bortle <= 1
            ? `No other Bortle 1 sites found within ${fmtMi(radius_miles)}.`
            : `No significantly darker sky found within ${fmtMi(radius_miles)}.`
          }
          {best_available && origin_bortle > 1 && (
            <> Closest darker spot: <span className={nearbyBortleClass(best_available.bortle_class)}>Bortle {best_available.bortle_class}</span>, {distOf(best_available)} {best_available.direction}{formatDriveTime(best_available.drive_minutes) ? ` · ${formatDriveTime(best_available.drive_minutes)} drive` : ''}  ({placeNode(best_available)})</>
          )}
        </p>
      )}

      {/* 3. Results table */}
      {results.length > 0 && (() => {
        // New Tiered Drive-Time Sort
        const sortedByDriveTime = [...results].sort((a, b) => {
          const bothHaveDrive = a.drive_minutes != null && b.drive_minutes != null;

          // Group pristine skies (Bortle 1 & 2) at the top
          const aIsTopTier = a.bortle_class <= 2;
          const bIsTopTier = b.bortle_class <= 2;

          if (aIsTopTier !== bIsTopTier) {
            return aIsTopTier ? -1 : 1;
          }

          // Sort by drive time (or distance fallback) within tiers
          if (bothHaveDrive) {
            if (a.drive_minutes !== b.drive_minutes) {
              return a.drive_minutes! - b.drive_minutes!;
            }
          } else {
            if (a.distance_miles !== b.distance_miles) {
              return a.distance_miles - b.distance_miles;
            }
          }

          // Tie-breaker: Darkest sky
          return a.bortle_class - b.bortle_class;
        });

        const nearest = sortedByDriveTime[0];

        // Keep darkest calculation as-is (strict Bortle-first sort)
        const darkest = [...results].sort((a, b) =>
          a.bortle_class !== b.bortle_class ? a.bortle_class - b.bortle_class : a.distance_miles - b.distance_miles
        )[0]

        const showDarkest = darkest !== nearest && darkest.bortle_class < nearest.bortle_class

        return (
          <>
            <div className="nearby-highlights">
              <div className="nearby-highlight-row">
                <span className="nearby-highlight-label">Nearest</span>
                <span><span className={nearbyBortleClass(nearest.bortle_class)}>Bortle {nearest.bortle_class}</span>  ·  {distOf(nearest)} {nearest.direction}{formatDriveTime(nearest.drive_minutes) ? `  ·  ${formatDriveTime(nearest.drive_minutes)} drive` : ''}  ({placeNode(nearest)})</span>
              </div>
              {showDarkest && (
                <div className="nearby-highlight-row">
                  <span className="nearby-highlight-label">Darkest</span>
                  <span><span className={nearbyBortleClass(darkest.bortle_class)}>Bortle {darkest.bortle_class}</span>  ·  {distOf(darkest)} {darkest.direction}{formatDriveTime(darkest.drive_minutes) ? `  ·  ${formatDriveTime(darkest.drive_minutes)} drive` : ''}  ({placeNode(darkest)})</span>
                </div>
              )}
            </div>
            <div className="wx-table-wrap">
              <table className="wx-table nearby-table">
                <thead>
                  <tr>
                    <th className="nearby-area-th">Area</th>
                    <th>Bortle</th>
                    <th>SQM</th>
                    <th>Dist</th>
                    {routingActive && <th>Drive</th>}
                    <th>Dir</th>
                  </tr>
                </thead>
                <tbody>
                  {[...results]
                    // Order by drive time, lowest first; unrouted (no ETA) last, then by distance.
                    .sort((a, b) => {
                      const ad = a.drive_minutes, bd = b.drive_minutes
                      if (ad == null && bd == null) return a.distance_miles - b.distance_miles
                      if (ad == null) return 1
                      if (bd == null) return -1
                      return ad - bd || a.bortle_class - b.bortle_class
                    })
                    .map((p, i) => {
                      const activate = () => onSelectLocation?.(p.lat, p.lon)
                      return (
                        <tr
                          key={i}
                          className="nearby-row-clickable"
                          onClick={activate}
                          tabIndex={0}
                          role="link"
                          aria-label={`View night report for ${placeStr(p)}`}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); activate() }
                          }}
                        >
                          <td className={`${nearbyBortleClass(p.bortle_class)} nearby-area-td`}>
                            <div className="nearby-area-inner">
                              <span className="poi-namelink">{placeStr(p)}</span>
                              {p.area_name && <span className="poi-area">{p.area_name}</span>}
                              {poiMeta(p, false)}
                            </div>
                          </td>
                          <td className={`wx-num nearby-bortle-col ${nearbyBortleClass(p.bortle_class)}`}>{p.bortle_class}</td>
                          <td className="wx-num nearby-sqm-col">{p.sqm != null ? p.sqm.toFixed(1) : '—'}</td>
                          <td className="wx-num nearby-dist-col">{distOf(p)}</td>
                          {routingActive && <td className="wx-num nearby-drive-col">{formatDriveTime(p.drive_minutes) ?? '—'}</td>}
                          <td className="wx-num nearby-dir-col">{p.direction} {mapLinkNode(p)}</td>
                        </tr>
                      )
                    })}
                </tbody>
              </table>
            </div>
          </>
        )
      })()}

      {/* 4. ALWAYS show domes if they exist, regardless of origin Bortle */}
      {light_domes.length > 0 && (
        <div className="nearby-domes">
          <div className="nearby-domes-label">Light domes</div>
          {light_domes.map((d, i) => (
            <div key={i} className="nearby-dome-row">
              <span className="nearby-dome-name">{placeStr(d)}</span>
              <span className={`nearby-dome-bortle ${nearbyBortleClass(d.bortle_class)}`}>Bortle {d.bortle_class}</span>
              <span className="nearby-dome-dist">{fmtMi(d.distance_miles)} {d.direction}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
