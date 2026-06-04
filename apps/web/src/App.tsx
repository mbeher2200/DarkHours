import React, { useState, useRef, useEffect, type FormEvent } from 'react'
import './App.css'
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
    if (rect.left < 8) {
      setNudge(8 - rect.left)
    } else if (rect.right > window.innerWidth - 8) {
      setNudge(window.innerWidth - 8 - rect.right)
    } else {
      setNudge(0)
    }
  }, [visible])

  return (
    <span
      className="toggle-tip-wrap"
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => { setVisible(false); setNudge(0) }}
    >
      {children}
      {visible && (
        <span
          ref={bubbleRef}
          className="tip-bubble"
          style={{ transform: `translateX(calc(-50% + ${nudge}px))` }}
        >
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
  const [weather, setWeather] = useState(true)
  const [targets, setTargets] = useState(false)
  const [satellites, setSatellites] = useState(false)
  const [imperial, setImperial] = useState<boolean>(defaultImperial)

  function toggleUnits(imp: boolean) {
    setImperial(imp)
    localStorage.setItem('units', imp ? 'imperial' : 'si')
  }

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<NightReport | null>(null)
  // Track which optional sections were requested for the current report
  const [reportWeather, setReportWeather] = useState(false)
  const [reportTargets, setReportTargets] = useState(false)
  const [reportSatellites, setReportSatellites] = useState(false)

  const [wxForecastUnavailable, satUnavailable] = (() => {
    if (!date) return [false, false]
    const today = new Date(); today.setHours(0, 0, 0, 0)
    const d = new Date(date + 'T00:00:00')
    const days = Math.round((d.getTime() - today.getTime()) / 86_400_000)
    return [days > 7, days < 0 || days > 10]
  })()

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    const q: NightQuery = { date, weather: weather && !wxForecastUnavailable, targets, satellites: satellites && !satUnavailable }
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

    setLoading(true)
    try {
      setReport(await fetchNight(q))
      setReportWeather(weather && !wxForecastUnavailable)
      setReportTargets(targets)
      setReportSatellites(satellites && !satUnavailable)
    } catch (err) {
      setReport(null)
      setError(err instanceof ApiRequestError ? err.message : 'Something went wrong.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app">
      <header className="masthead">
        <h1>PyNightSky</h1>
        <p>Night-sky quality scoring for astrophotography planning.</p>
      </header>

      <form className="card query" onSubmit={onSubmit}>
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
            role="tab"
            aria-selected={mode === 'coords'}
            className={mode === 'coords' ? 'active' : ''}
            onClick={() => setMode('coords')}
          >
            Enter coordinates
          </button>
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

        <fieldset className="toggles">
          {wxForecastUnavailable ? (
            <Tip text="Weather forecast unavailable beyond 7 days">
              <label className="wx-unavailable">
                <input type="checkbox" checked={false} disabled onChange={() => {}} />
                Weather
              </label>
            </Tip>
          ) : (
            <label>
              <input type="checkbox" checked={weather} onChange={(e) => setWeather(e.target.checked)} />
              Weather
            </label>
          )}
          <label>
            <input type="checkbox" checked={targets} onChange={(e) => setTargets(e.target.checked)} />
            Visible targets
          </label>
          {satUnavailable ? (
            <Tip text={
              date && new Date(date + 'T00:00:00') < new Date(new Date().setHours(0, 0, 0, 0))
                ? 'Satellite passes unavailable for past dates'
                : 'TLE accuracy degrades beyond ~10 days — passes unreliable'
            }>
              <label className="wx-unavailable">
                <input type="checkbox" checked={false} disabled onChange={() => {}} />
                Satellites
              </label>
            </Tip>
          ) : (
            <label>
              <input type="checkbox" checked={satellites} onChange={(e) => setSatellites(e.target.checked)} />
              Satellites
            </label>
          )}
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
        </fieldset>

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
