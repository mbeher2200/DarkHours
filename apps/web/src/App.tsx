import React, { useState, useRef, useEffect, type FormEvent } from 'react'
import './App.css'
import { LocateFixed, Cloud, Star, Satellite, ChevronLeft, ChevronRight, Clock, MapPin, Moon, X, Eye, Sparkles, Compass } from 'lucide-react'
import { ApiRequestError, fetchNight, fetchSuggestions, type NightQuery } from './api'
import { todayIso, toIsoDate, defaultImperial } from './format'
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
const HISTORY_MAX = 8

function loadHistory(): HistoryEntry[] {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY) ?? '[]') } catch { return [] }
}
function saveHistory(entries: HistoryEntry[]) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(entries))
}

function Tip({ text, children }: { text: string; children: React.ReactNode }) {
  const [visible, setVisible] = useState(false)
  const [nudge, setNudge] = useState(0)
  const bubbleRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    if (!visible || !bubbleRef.current) return
    const rect = bubbleRef.current.getBoundingClientRect()
    if (rect.left < 8) setNudge(8 - rect.left)
    else if (rect.right > window.innerWidth - 8) setNudge(window.innerWidth - 8 - rect.right)
    else setNudge(0)
  }, [visible])

  return (
    <span
      className="toggle-tip-wrap"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => { setVisible(false); setNudge(0) }}
    >
      {children}
      {visible && (
        <span ref={bubbleRef} className="tip-bubble" style={{ transform: `translateX(calc(-50% + ${nudge}px))` }}>
          {text}
        </span>
      )}
    </span>
  )
}

export default function App() {
  const [mode, setMode] = useState<Mode>('place')
  const [place, setPlace] = useState('')
  const [lat, setLat] = useState('')
  const [lon, setLon] = useState('')
  const [date, setDate] = useState(todayIso())
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
    const today = new Date(); today.setHours(0, 0, 0, 0)
    const d = new Date(date + 'T00:00:00')
    const days = Math.round((d.getTime() - today.getTime()) / 86_400_000)
    return { wxForecastUnavailable: days > 7, satUnavailable: days < 0 || days > 10, isPastDate: days < 0 }
  })()

  function availabilityFor(d: string) {
    const today = new Date(); today.setHours(0, 0, 0, 0)
    const dd = new Date(d + 'T00:00:00')
    const days = Math.round((dd.getTime() - today.getTime()) / 86_400_000)
    return { wxUnavail: days > 7, satUnavail: days < 0 || days > 10 }
  }

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

    const targetDate = d || todayIso()
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
    setDate(toIsoDate(d))
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
          <Moon size={13} strokeWidth={2} fill={redMode ? 'currentColor' : 'none'} />
          <span>Night vision</span>
        </button>

        <h1>DarkHours</h1>
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
          <div className="scope-strip">
            <span className="scope-pill">
              <Moon size={13} strokeWidth={2} />
              Lunar
            </span>
            <span className="scope-pill">
              <Eye size={13} strokeWidth={2} />
              Light Pollution
            </span>
            <span className="scope-pill">
              <Clock size={13} strokeWidth={2} />
              Clear Dark Hours
            </span>
            <span className="scope-pill">
              <Sparkles size={13} strokeWidth={2} />
              Milky Way
            </span>
            <span className="scope-pill">
              <Compass size={13} strokeWidth={2} />
              Horizon Glow
            </span>
            {wxForecastUnavailable ? (
              <Tip text="Weather forecast unavailable beyond 7 days">
                <span className="scope-pill unavail">
                  <Cloud size={13} strokeWidth={2} />
                  Weather
                </span>
              </Tip>
            ) : (
              <span className="scope-pill">
                <Cloud size={13} strokeWidth={2} />
                Weather
              </span>
            )}
            <span className="scope-pill">
              <Star size={13} strokeWidth={2} />
              Objects
            </span>
            {satUnavailable ? (
              <Tip text={isPastDate ? 'Satellite passes unavailable for past dates' : 'TLE accuracy degrades beyond 10 days: passes unreliable'}>
                <span className="scope-pill unavail">
                  <Satellite size={13} strokeWidth={2} />
                  Satellites
                </span>
              </Tip>
            ) : (
              <span className="scope-pill">
                <Satellite size={13} strokeWidth={2} />
                Satellites
              </span>
            )}
          </div>
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
            <p className="es-headline">Stop checking multiple websites.</p>
            <p className="es-body">
              DarkHours unifies Bortle, lunar, dark sky hours, and weather factors into a single night quality score. If your location doesn't hold up, find better skies within driving distance in seconds.
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

          <div className="es-divider" />

          <div className="es-quickstart">
            <span className="es-quickstart-label">Try an example location</span>
            <div className="es-quickstart-btns">
              {([
                ['Sedona, AZ',         'Sedona, Arizona'],
                ['Death Valley, CA',   'Death Valley, California'],
                ['Cherry Springs, PA', 'Cherry Springs State Park, Pennsylvania'],
              ] as [string, string][]).map(([label, query]) => (
                <button
                  key={query}
                  type="button"
                  className="es-qs-btn"
                  onClick={() => quickSearch(query)}
                >
                  {label}
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
        DarkHours is free, open source, and will never require your email address.
      </footer>
    </div>
  )
}
