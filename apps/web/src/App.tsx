import React, { useState, useRef, useEffect, type FormEvent } from 'react'
import './App.css'
import { LocateFixed, Cloud, Star, Satellite } from 'lucide-react'
import { ApiRequestError, fetchNight, type NightQuery } from './api'
import { todayIso, defaultImperial } from './format'
import ReportCard from './ReportCard'
import type { NightReport } from './types'

type Mode = 'place' | 'coords'

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
      const p = new URLSearchParams()
      if (q.location) p.set('q', q.location)
      else { p.set('lat', String(q.lat)); p.set('lon', String(q.lon)) }
      if (q.date) p.set('date', q.date)
      history.replaceState(null, '', '?' + p.toString())
    } catch (err) {
      setReport(null)
      setError(err instanceof ApiRequestError ? err.message : 'Something went wrong.')
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
      <header className="masthead">
        <h1>DarkHours</h1>
        <p>Know every factor. Know the right night to shoot the stars.</p>
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

        {mode === 'place' ? (
          <label className="field">
            <span>Place</span>
            <input
              type="text"
              placeholder="e.g. Cherry Springs State Park"
              value={place}
              onChange={(e) => setPlace(e.target.value)}
              maxLength={200}
              autoFocus
            />
          </label>
        ) : (
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

        <label className="field">
          <span>Date</span>
          <input type="date" value={date} min="1900-01-01" max="2050-12-31" onChange={(e) => setDate(e.target.value)} />
        </label>

        <div className="scope-row">
          <div className="scope-strip">
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
              Targets
            </span>
            {satUnavailable ? (
              <Tip text={isPastDate ? 'Satellite passes unavailable for past dates' : 'TLE accuracy degrades beyond ~10 days — passes unreliable'}>
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
          <div className="units-toggle" role="group" aria-label="Unit system">
            <button
              type="button"
              className={!imperial ? 'active' : ''}
              onClick={() => toggleUnits(false)}
            >°C / m/s</button>
            <button
              type="button"
              className={imperial ? 'active' : ''}
              onClick={() => toggleUnits(true)}
            >°F / mph</button>
          </div>
        </div>

        <button type="submit" className="submit" disabled={loading}>
          {loading ? 'Scoring…' : 'Score the night'}
        </button>
      </form>

      {error && <div className="card error">{error}</div>}
      {report && !loading && (
        <ReportCard
          report={report}
          showWeather={reportWeather}
          showTargets={reportTargets}
          showSatellites={reportSatellites}
          imperial={imperial}
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
        Source:{' '}
        <a href="https://github.com/mbeher2200/PyNightSkyPredictor" target="_blank" rel="noreferrer">GitHub</a>
      </footer>
    </div>
  )
}
