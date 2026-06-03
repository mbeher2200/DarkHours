import { useState, type FormEvent } from 'react'
import './App.css'
import { ApiRequestError, fetchNight, type NightQuery } from './api'
import { todayIso } from './format'
import ReportCard from './ReportCard'
import type { NightReport } from './types'

type Mode = 'place' | 'coords'

export default function App() {
  const [mode, setMode] = useState<Mode>('place')
  const [place, setPlace] = useState('')
  const [lat, setLat] = useState('')
  const [lon, setLon] = useState('')
  const [date, setDate] = useState(todayIso())
  const [weather, setWeather] = useState(true)
  const [targets, setTargets] = useState(false)
  const [satellites, setSatellites] = useState(false)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<NightReport | null>(null)
  // Track which optional sections were requested for the current report
  const [reportWeather, setReportWeather] = useState(false)
  const [reportTargets, setReportTargets] = useState(false)
  const [reportSatellites, setReportSatellites] = useState(false)

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    const q: NightQuery = { date, weather, targets, satellites }
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
      setReportWeather(weather)
      setReportTargets(targets)
      setReportSatellites(satellites)
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
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>

        <fieldset className="toggles">
          <label>
            <input type="checkbox" checked={weather} onChange={(e) => setWeather(e.target.checked)} />
            Weather
          </label>
          <label>
            <input type="checkbox" checked={targets} onChange={(e) => setTargets(e.target.checked)} />
            Visible targets
          </label>
          <label>
            <input
              type="checkbox"
              checked={satellites}
              onChange={(e) => setSatellites(e.target.checked)}
            />
            Satellites
          </label>
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
        />
      )}

      <footer className="colophon">
        Light pollution: Falchi 2016 / VIIRS · Weather: NOAA/NWS, Open-Meteo · Geocoding:
        OpenStreetMap Nominatim
      </footer>
    </div>
  )
}
