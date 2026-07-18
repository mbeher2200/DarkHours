import { useState, useRef, useEffect, type FormEvent } from 'react'
import './App.css'
import { LocateFixed, ChevronLeft, ChevronRight, Clock, MapPin, X } from 'lucide-react'
import { ApiRequestError, fetchNight, fetchSuggestions, type NightQuery } from './api'
import { tonightIso, toIsoDate, defaultImperial, availabilityFor } from './format'
import ReportCard from './ReportCard'
import DatePicker from './DatePicker'
import type { NightReport } from './types'

type Mode = 'place' | 'coords'

interface HistoryEntry {
  label: string
  mode: Mode
  location?: string
  lat?: string
  lon?: string
}

const HISTORY_KEY = 'search-history'
const HISTORY_MAX = 12

function loadHistory(): HistoryEntry[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]') } catch { return [] }
}
function saveHistory(entries: HistoryEntry[]) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(entries))
}


export default function App() {
  const [mode, setMode] = useState<Mode>('place')
  const [place, setPlace] = useState('')
  const [lat, setLat] = useState('')
  const [lon, setLon] = useState('')
  const [date, setDate] = useState(tonightIso())
  const [imperial, setImperial] = useState<boolean>(defaultImperial)
  const [locating, setLocating] = useState(false)
  const [redMode, setRedMode] = useState<boolean>(() => localStorage.getItem('redMode') === '1')
  const [searchHistory, setSearchHistory] = useState<HistoryEntry[]>(loadHistory)
  const [placeDropdown, setPlaceDropdown] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const placeInputRef = useRef<HTMLInputElement>(null)
  const dropdownItemRefs = useRef<(HTMLButtonElement | null)[]>([])
  // The label most recently picked from the dropdown — used to suppress the
  // suggestion fetch that the resulting setPlace() would otherwise trigger.
  const lastPickedRef = useRef<string | null>(null)

  // Red Light (night-vision) mode: toggle a root class that filters the whole
  // app to red so blue/green light doesn't spoil dark adaptation. Persisted.
  useEffect(() => {
    document.documentElement.classList.toggle('red-mode', redMode)
    localStorage.setItem('redMode', redMode ? '1' : '0')
    const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
    if (link) {
      const svg = redMode
        ? `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#000" rx="4"/><rect x="4" y="5" width="14" height="4" fill="none" stroke="#F00" stroke-width="1.5" rx="1"/><rect x="4" y="11" width="20" height="4" fill="#F00" fill-opacity=".4" rx="1"/><rect x="4" y="17" width="10" height="4" fill="#F00" fill-opacity=".7" rx="1"/><rect x="4" y="23" width="24" height="4" fill="#F00" rx="1"/></svg>`
        : `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" fill="#1e1e1e" rx="4"/><rect x="4" y="5" width="14" height="4" fill="#C85656" rx="1"/><rect x="4" y="11" width="20" height="4" fill="#D99B41" rx="1"/><rect x="4" y="17" width="10" height="4" fill="#3A8772" rx="1"/><rect x="4" y="23" width="24" height="4" fill="#5BC0DE" rx="1"/></svg>`
      link.href = `data:image/svg+xml;base64,${btoa(svg)}`
    }
  }, [redMode])

  // Debounced autocomplete: fetch suggestions as the user types a place name.
  // A 300ms debounce + 3-char minimum keeps the per-keystroke request count (and
  // the AWS Location bill) low; each in-flight request is aborted when superseded.
  useEffect(() => {
    const q = place.trim()
    const ctrl = new AbortController()
    const timer = setTimeout(() => {
      if (mode !== 'place' || q.length < 3 || q === lastPickedRef.current) {
        setSuggestions([])
        return
      }
      fetchSuggestions(q, ctrl.signal)
        .then(setSuggestions)
        .catch(() => { /* aborted or failed — leave existing suggestions */ })
    }, 300)
    return () => { clearTimeout(timer); ctrl.abort() }
  }, [place, mode])

  function toggleUnits(imp: boolean) {
    setImperial(imp)
    localStorage.setItem('units', imp ? 'imperial' : 'si')
  }

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<NightReport | null>(null)
  const [reportWeather, setReportWeather] = useState(false)
  const [reportTargets, setReportTargets] = useState(false)
  const [reportSatellites, setReportSatellites] = useState(false)

  const { wxForecastUnavailable, satUnavailable, isPastDate } = (() => {
    if (!date) return { wxForecastUnavailable: false, satUnavailable: false, isPastDate: false }
    const { wxUnavail, satUnavail, days } = availabilityFor(date)
    return { wxForecastUnavailable: wxUnavail, satUnavailable: satUnavail, isPastDate: days < 0 }
  })()

  async function runQuery(q: NightQuery) {
    setLoading(true)
    setError(null)
    try {
      const r = await fetchNight(q)
      setReport(r)
      setReportWeather(!!q.weather)
      setReportTargets(!!q.targets)
      setReportSatellites(!!q.satellites)
      const entry: HistoryEntry = q.location
        ? { label: r.display_name, mode: 'place', location: q.location }
        : { label: r.display_name, mode: 'coords', lat: String(q.lat), lon: String(q.lon) }
      setSearchHistory(prev => {
        const next = [entry, ...prev.filter(h => h.label !== entry.label)].slice(0, HISTORY_MAX)
        saveHistory(next)
        return next
      })
      const p = new URLSearchParams()
      if (q.location) p.set('q', q.location)
      else { p.set('lat', String(q.lat)); p.set('lon', String(q.lon)) }
      if (q.date) p.set('date', q.date)
      history.replaceState(null, '', '?' + p.toString())
    } catch (err) {
      setReport(null)
      setError(err instanceof ApiRequestError ? err.message : 'Could not reach the server. Check your connection and try again.')
    } finally {
      setLoading(false)
    }
  }

  // Restore query from URL params on first load and auto-submit
  useEffect(() => {
    const p = new URLSearchParams(window.location.search)
    const q = p.get('q')
    const la = p.get('lat')
    const lo = p.get('lon')
    const d = p.get('date')
    if (!q && !(la && lo)) return

    const targetDate = d || tonightIso()
    const { wxUnavail, satUnavail } = availabilityFor(targetDate)

    if (d) setDate(d)

    const query: NightQuery = {
      date: targetDate,
      weather: !wxUnavail,
      targets: true,
      satellites: !satUnavail,
    }
    if (q) {
      setPlace(q)
      setMode('place')
      query.location = q
    } else {
      setLat(la!)
      setLon(lo!)
      setMode('coords')
      query.lat = Number(la)
      query.lon = Number(lo)
    }
    runQuery(query)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  function useMyLocation() {
    if (!navigator.geolocation) {
      setError('Geolocation is not supported by your browser.')
      return
    }
    setLocating(true)
    setError(null)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const la = pos.coords.latitude.toFixed(5)
        const lo = pos.coords.longitude.toFixed(5)
        setLat(la)
        setLon(lo)
        setMode('coords')
        setLocating(false)
        const { wxUnavail, satUnavail } = availabilityFor(date)
        runQuery({ lat: Number(la), lon: Number(lo), date, weather: !wxUnavail, targets: true, satellites: !satUnavail })
      },
      () => {
        setLocating(false)
        setError('Could not get your location. Check browser permissions.')
      },
      { timeout: 10000 },
    )
  }

  function shiftDay(delta: number) {
    const d = new Date(date + 'T00:00:00')
    d.setDate(d.getDate() + delta)
    const newDate = toIsoDate(d)
    setDate(newDate)
    const { wxUnavail, satUnavail } = availabilityFor(newDate)
    const q: NightQuery = { date: newDate, weather: !wxUnavail, targets: true, satellites: !satUnavail }
    if (mode === 'place') {
      if (!place.trim()) return
      q.location = place.trim()
    } else {
      const la = Number(lat), lo = Number(lon)
      if (!lat || !lon || Number.isNaN(la) || Number.isNaN(lo)) return
      q.lat = la
      q.lon = lo
    }
    runQuery(q)
  }

  // Bubble-up target for ReportCard's "View Details" drill-in (a date-only
  // fetch for the same location). Never touches `loading`, so the
  // `report && !loading` gate below never flips and ReportCard stays mounted —
  // Night Timeline/Satellite Ephemeris update in place instead of unmounting.
  function handleDateDetail(next: NightReport, nextDate: string) {
    setReport(next)
    setDate(nextDate)
    const p = new URLSearchParams(window.location.search)
    p.set('date', nextDate)
    history.replaceState(null, '', '?' + p.toString())
  }

  function quickSearch(location: string) {
    setMode('place')
    setPlace(location)
    const { wxUnavail, satUnavail } = availabilityFor(date)
    runQuery({ location, date, weather: !wxUnavail, targets: true, satellites: !satUnavail })
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    const q: NightQuery = { date, weather: !wxForecastUnavailable, targets: true, satellites: !satUnavailable }
    if (mode === 'place') {
      if (!place.trim()) {
        setError('Enter a place to search for.')
        return
      }
      q.location = place.trim()
    } else {
      const la = Number(lat)
      const lo = Number(lon)
      if (!lat || !lon || Number.isNaN(la) || Number.isNaN(lo)) {
        setError('Enter both latitude and longitude.')
        return
      }
      if (la < -90 || la > 90 || lo < -180 || lo > 180) {
        setError('Latitude must be −90..90 and longitude −180..180.')
        return
      }
      q.lat = la
      q.lon = lo
    }

    await runQuery(q)
  }

  return (
    <div className="app">
      {/* Luminance→red color matrix for Red Light (night-vision) mode. Hidden;
          referenced by `filter: url(#red-night)` on .app when .red-mode is set.
          Maps every pixel to (luma, 0, 0) so no blue/green light is emitted. */}
      <svg className="redmode-svg" aria-hidden="true" focusable="false">
        <filter id="red-night" colorInterpolationFilters="sRGB">
          <feColorMatrix
            type="matrix"
            values="0.2126 0.7152 0.0722 0 0
                    0      0      0      0 0
                    0      0      0      0 0
                    0      0      0      1 0"
          />
        </filter>
      </svg>

      <header className="masthead">
        <button
          type="button"
          className={`night-vision-toggle${redMode ? ' active' : ''}`}
          onClick={() => setRedMode(v => !v)}
          aria-pressed={redMode}
          title="Red light mode — night vision"
        >
          <span className="nv-switch" aria-hidden="true"><span className="nv-thumb" /></span>
          <span>Night vision</span>
        </button>

        <h1>
          <span className="masthead-logo" aria-hidden="true">
            <span className="masthead-logo-cell" style={{background:'#C85656', width:'14px'}} />
            <span className="masthead-logo-cell" style={{background:'#D99B41', width:'20px'}} />
            <span className="masthead-logo-cell" style={{background:'#3A8772', width:'10px'}} />
            <span className="masthead-logo-cell" style={{background:'#5BC0DE', width:'24px'}} />
          </span>
          DarkHours
        </h1>
      </header>

      <form className="card query" onSubmit={onSubmit}>
        <div className="location-header">
          <div className="mode-toggle" role="tablist" aria-label="Location input mode">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'place'}
              className={mode === 'place' ? 'active' : ''}
              onClick={() => setMode('place')}
            >
              Search a place
            </button>
            <button
              type="button"
              className="geo-btn"
              onClick={useMyLocation}
              disabled={locating || loading}
            >
              <LocateFixed size={14} strokeWidth={2} style={{ flexShrink: 0 }} />
              {locating ? 'Locating…' : 'Use my location'}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'coords'}
              className={mode === 'coords' ? 'active' : ''}
              onClick={() => setMode('coords')}
            >
              Enter coordinates
            </button>
          </div>
        </div>

        {mode === 'place' ? (() => {
          const q = place.trim()
          // While actively typing (≥3 chars) show live geocoder suggestions;
          // otherwise fall back to filtered recent searches.
          const useSuggest = q.length >= 3 && suggestions.length > 0
          type Item =
            | { kind: 'suggestion'; label: string }
            | { kind: 'history'; label: string; entry: HistoryEntry }
          const items: Item[] = useSuggest
            ? suggestions.map(s => ({ kind: 'suggestion' as const, label: s }))
            : searchHistory
                .filter(h => !q || h.label.toLowerCase().includes(q.toLowerCase()))
                .map(h => ({ kind: 'history' as const, label: h.label, entry: h }))
          const showDropdown = placeDropdown && items.length > 0

          const selectItem = (item: Item) => {
            setPlaceDropdown(false)
            setActiveIndex(-1)
            const { wxUnavail, satUnavail } = availabilityFor(date)
            if (item.kind === 'suggestion') {
              lastPickedRef.current = item.label   // suppress the refetch from setPlace
              setSuggestions([])
              setMode('place')
              setPlace(item.label)
              runQuery({ location: item.label, date, weather: !wxUnavail, targets: true, satellites: !satUnavail })
              return
            }
            const h = item.entry
            setMode(h.mode)
            if (h.mode === 'place') {
              setPlace(h.location!)
              runQuery({ location: h.location!, date, weather: !wxUnavail, targets: true, satellites: !satUnavail })
            } else {
              setLat(h.lat!)
              setLon(h.lon!)
              runQuery({ lat: Number(h.lat), lon: Number(h.lon), date, weather: !wxUnavail, targets: true, satellites: !satUnavail })
            }
          }
          return (
          <div
            className="field place-field-wrap"
            onBlur={(e) => {
              if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
                setPlaceDropdown(false)
                setActiveIndex(-1)
              }
            }}
          >
            <label htmlFor="place-input" className="field-label">Place</label>
            <div className="place-input-row">
              <input
                id="place-input"
                ref={placeInputRef}
                type="text"
                placeholder="e.g. Cherry Springs State Park"
                value={place}
                onChange={(e) => { setPlace(e.target.value); setActiveIndex(-1); setPlaceDropdown(true) }}
                onFocus={() => setPlaceDropdown(true)}
                onKeyDown={(e) => {
                  if (e.key === 'ArrowDown' && showDropdown) {
                    e.preventDefault()
                    setActiveIndex(0)
                    dropdownItemRefs.current[0]?.focus()
                  } else if (e.key === 'Escape') {
                    setPlaceDropdown(false)
                  }
                }}
                maxLength={200}
                autoFocus
                autoComplete="off"
                role="combobox"
                aria-expanded={showDropdown}
                aria-haspopup="listbox"
                aria-controls="place-listbox"
                aria-autocomplete="list"
                aria-activedescendant={activeIndex >= 0 ? `place-item-${activeIndex}` : undefined}
                className={place ? 'has-clear' : ''}
              />
              {place && (
                <button
                  type="button"
                  className="place-clear-btn"
                  aria-label="Clear search"
                  tabIndex={-1}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    setPlace('')
                    setActiveIndex(-1)
                    placeInputRef.current?.focus()
                  }}
                >
                  <X size={13} strokeWidth={2.5} />
                </button>
              )}
            </div>
            {showDropdown && (
              <div
                id="place-listbox"
                className="place-dropdown"
                role="listbox"
                aria-label={useSuggest ? 'Place suggestions' : 'Recent searches'}
              >
                {items.map((item, i) => (
                  <button
                    key={`${item.kind}-${i}`}
                    id={`place-item-${i}`}
                    ref={(el) => { dropdownItemRefs.current[i] = el }}
                    type="button"
                    className="place-dropdown-item"
                    role="option"
                    aria-selected={activeIndex === i}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => selectItem(item)}
                    onKeyDown={(e) => {
                      if (e.key === 'ArrowDown') {
                        e.preventDefault()
                        const next = i + 1
                        if (next < items.length) {
                          setActiveIndex(next)
                          dropdownItemRefs.current[next]?.focus()
                        }
                      } else if (e.key === 'ArrowUp') {
                        e.preventDefault()
                        if (i === 0) {
                          setActiveIndex(-1)
                          placeInputRef.current?.focus()
                        } else {
                          setActiveIndex(i - 1)
                          dropdownItemRefs.current[i - 1]?.focus()
                        }
                      } else if (e.key === 'Escape') {
                        setPlaceDropdown(false)
                        setActiveIndex(-1)
                        placeInputRef.current?.focus()
                      } else if (e.key === 'Enter') {
                        e.preventDefault()
                        selectItem(item)
                      }
                    }}
                  >
                    {item.kind === 'suggestion'
                      ? <MapPin size={12} strokeWidth={2} className="place-dropdown-icon" />
                      : <Clock size={12} strokeWidth={2} className="place-dropdown-icon" />}
                    <span className="place-dropdown-label">{item.label}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          )
        })() : (
          <div className="coords">
            <label className="field">
              <span>Latitude</span>
              <input
                type="number"
                step="any"
                placeholder="40.7"
                value={lat}
                onChange={(e) => setLat(e.target.value)}
              />
            </label>
            <label className="field">
              <span>Longitude</span>
              <input
                type="number"
                step="any"
                placeholder="-74.0"
                value={lon}
                onChange={(e) => setLon(e.target.value)}
              />
            </label>
          </div>
        )}

        <div className="field">
          <label htmlFor="dp-trigger" className="field-label">Date</label>
          <DatePicker value={date} min="1900-01-01" max="2050-12-31" onChange={setDate} />
        </div>

        <div className="scope-row">
          {/* Scope indicators read as config noise before first use — collapsed
              by default, with any availability diagnostic kept visible in the
              summary line so date-range limits still surface without expanding. */}
          <details className="scope-details">
            <summary className="scope-summary">
              Report scope
              {(wxForecastUnavailable || satUnavailable) && (
                <span className="scope-diag">
                  {' — '}
                  {[
                    wxForecastUnavailable ? 'weather out of range' : null,
                    satUnavailable ? (isPastDate ? 'satellites past date' : 'satellites out of range') : null,
                  ].filter(Boolean).join(' · ')}
                </span>
              )}
            </summary>
            <div className="scope-grid">
              <span className="scope-item"><span className="scope-brk">[■]</span>Lunar</span>
              {wxForecastUnavailable
                ? <span className="scope-item scope-dim"><span className="scope-brk">[ ]</span>Weather<span className="scope-diag"> · Out of range</span></span>
                : <span className="scope-item"><span className="scope-brk">[■]</span>Weather</span>
              }
              <span className="scope-item"><span className="scope-brk">[■]</span>Sky Features</span>
              {wxForecastUnavailable
                ? <span className="scope-item scope-dim"><span className="scope-brk">[ ]</span>Clear Dark Hours<span className="scope-diag"> · Out of range</span></span>
                : <span className="scope-item"><span className="scope-brk">[■]</span>Clear Dark Hours</span>
              }
              <span className="scope-item"><span className="scope-brk">[■]</span>Sky &amp; Horizon Glow</span>
              {satUnavailable
                ? <span className="scope-item scope-dim"><span className="scope-brk">[ ]</span>Satellites<span className="scope-diag"> · {isPastDate ? 'Past date' : 'Out of range'}</span></span>
                : <span className="scope-item"><span className="scope-brk">[■]</span>Satellites</span>
              }
            </div>
          </details>
        </div>

        <div className="submit-row">
          <button type="button" className="day-nav" onClick={() => shiftDay(-1)} disabled={loading || locating} aria-label="Previous day">
            <ChevronLeft size={18} strokeWidth={2.5} />
          </button>
          <button type="submit" className="submit" disabled={loading}>
            {loading ? 'Scoring…' : 'Score the night'}
          </button>
          <button type="button" className="day-nav" onClick={() => shiftDay(1)} disabled={loading || locating} aria-label="Next day">
            <ChevronRight size={18} strokeWidth={2.5} />
          </button>
        </div>
      </form>

      {error && <div className="card error">{error}</div>}
      {!!report && !loading && report.date !== date && (
        <div className="stale-notice">
          Date changed to {date}. Press "Score the night" to refresh.
        </div>
      )}
      {!report && !loading && !error && (
        <div className="card empty-state">
          <div className="es-copy">
            <p className="es-headline">Built for landscape astrophotography. Not for subscriptions.</p>
            <p className="es-body">
              Most of us only get a handful of clear, dark hours each month to do what we love. I built DarkHours because I needed a highly precise predictive tool for my own landscape astrophotography. It's open-source, free, and designed to help make the most of every dark hour.
            </p>
          </div>

          <div className="es-score-scale">
            <div className="es-score-bar" />
            <div className="es-score-labels">
              <span>Poor</span>
              <span>Fair</span>
              <span>Good</span>
              <span>Excellent</span>
            </div>
          </div>

          <span className="es-caps-label">Features</span>

          {/* Ordered by differentiation, not data category: the four features no
              competitor has lead; commodity metrics are swept into the
              "fundamentals" line below rather than given tiles. */}
          <div className="es-caps">
            {([
              ['Target Windows',    'Every shootable target with its peak time and window, and why others are blocked due to clouds, moonwash, or light domes.'],
              ['Milky Way Planner', 'A 360° view of the galactic plane over your horizon—altitude, bearing, and the best minute to shoot.'],
              ['Sky & Horizon Glow', 'A horizon map of light domes around you. Know more than just the Bortle.'],
              ['Nearby Dark Sky',   'Search for low bortle locations you can actually reach, with routing to facilities like parking, campgrounds, and viewpoints. All sorted by drive times.'],
              ['30 Day Best Night', 'A 30 day outlook. Compare conditions across multiple nights to identify the best windows.'],
              ['Smoke & Haze Forecast',  'Wildfire smoke and upper-air aerosol data from ground sensors, and satellites.'],
              ['Meteor Shower Predictions', 'Know when the next meteor shower will peak, the estimated meteors per hour, and visibility from your location.'],
              ['Tailored Weather Forecasts', 'A two week, hour by hour forecast for: cloud cover, temperature, wind and humidity cloud layers, and wind - updated twice an hour. Plus three day atmospheric seeing, and transparency provided by 7Timer.'],
            ] as [string, string][]).map(([k, v]) => (
              <div key={k} className="es-cap">
                <span className="es-cap-k">{k}</span>
                <span className="es-cap-v">{v}</span>
              </div>
            ))}
          </div>

          <p className="es-caps-more">
            Plus the fundamentals: Bortle class & SQM analysis, clear weather dark hours calcuations, seeing & transparency forecasts, and cloud layers by altitude. Every metric sourced and time-stamped. Free, open-source, no account required.
          </p>

          <div className="es-divider" />

          <div className="es-quickstart">
            <span className="es-quickstart-label">View a sample report</span>
            <div className="es-quickstart-btns">
              {([
                ['Sedona, AZ',         'Sedona, Arizona',                          'Bortle 7 suburb — the dark-sky finder at work'],
                ['Death Valley, CA',   'Death Valley, California',                 'Bortle 1 — pristine desert benchmark'],
                ['Cherry Springs, PA', 'Cherry Springs State Park, Pennsylvania',  'Bortle 2 — the East Coast classic'],
              ] as [string, string, string][]).map(([label, query, hook]) => (
                <button
                  key={query}
                  type="button"
                  className="es-qs-btn"
                  onClick={() => quickSearch(query)}
                >
                  <span className="es-qs-name">{label}</span>
                  <span className="es-qs-hook">{hook}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
      {report && !loading && (
        <ReportCard
          report={report}
          showWeather={reportWeather}
          showTargets={reportTargets}
          showSatellites={reportSatellites}
          imperial={imperial}
          onToggleUnits={toggleUnits}
          onDateDetail={handleDateDetail}
        />
      )}

      <footer className="colophon">
        Light pollution:{' '}
        <a href="https://doi.org/10.5880/GFZ.1.4.2016.001" target="_blank" rel="noreferrer">Falchi et al. 2016</a>
        {' / '}
        <a href="https://www2.lightpollutionmap.info" target="_blank" rel="noreferrer">VIIRS Black Marble 2025</a>
        {' · '}Weather:{' '}
        <a href="https://open-meteo.com" target="_blank" rel="noreferrer">Open-Meteo</a>
        {' / '}
        <a href="https://github.com/Yeqzids/7timer-issues/wiki" target="_blank" rel="noreferrer">7Timer</a>
        {' · '}Live haze:{' '}
        <a href="https://waqi.info" target="_blank" rel="noreferrer">World Air Quality Index Project</a>
        {' · '}Aurora:{' '}
        <a href="https://www.swpc.noaa.gov/" target="_blank" rel="noreferrer">NOAA SWPC</a>
        <br />
        Satellites:{' '}
        <a href="https://celestrak.org" target="_blank" rel="noreferrer">CelesTrak</a>
        {' · '}Ephemeris:{' '}
        <a href="https://ssd.jpl.nasa.gov/" target="_blank" rel="noreferrer">NASA/JPL DE421</a>
        {' · '}Moon imagery:{' '}
        <a href="https://svs.gsfc.nasa.gov/4874" target="_blank" rel="noreferrer">NASA SVS</a>
        <br />
        Nearby places:{' '}
        <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OpenStreetMap contributors</a>
        {' (ODbL)'}
        <br />
        Source:{' '}
        <a href="https://github.com/mbeher2200/PyNightSkyPredictor" target="_blank" rel="noreferrer">GitHub</a>
                <br /><br />
        DarkHours is free, open source, and will never require your email address. Visit our <a href="/blog/" target="_blank" rel="noreferrer">blog</a>!
      </footer>
    </div>
  )
}
