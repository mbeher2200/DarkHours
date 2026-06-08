import { useState, useRef, useEffect } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'

interface DatePickerProps {
  value: string   // YYYY-MM-DD
  min?: string
  max?: string
  onChange: (value: string) => void
}

const MONTHS = [
  'January','February','March','April','May','June',
  'July','August','September','October','November','December',
]
const DOW = ['Su','Mo','Tu','We','Th','Fr','Sa']

function todayIso() {
  const d = new Date()
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')

  return `${year}-${month}-${day}`
}

function formatDisplay(iso: string) {
  const d = new Date(iso + 'T00:00:00')
  return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })
}

export default function DatePicker({ value, min, max, onChange }: DatePickerProps) {
  const today = todayIso()
  const wrapRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)

  const seed = value ? new Date(value + 'T00:00:00') : new Date()
  const [viewYear, setViewYear] = useState(seed.getFullYear())
  const [viewMonth, setViewMonth] = useState(seed.getMonth())

  useEffect(() => {
    if (!value) return
    const d = new Date(value + 'T00:00:00')
    setViewYear(d.getFullYear())
    setViewMonth(d.getMonth())
  }, [value])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  function prevMonth() {
    if (viewMonth === 0) { setViewYear(y => y - 1); setViewMonth(11) }
    else setViewMonth(m => m - 1)
  }
  function nextMonth() {
    if (viewMonth === 11) { setViewYear(y => y + 1); setViewMonth(0) }
    else setViewMonth(m => m + 1)
  }

  function select(iso: string) {
    onChange(iso)
    setOpen(false)
  }

  const firstDow = new Date(viewYear, viewMonth, 1).getDay()
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate()
  const cells: (number | null)[] = [
    ...Array<null>(firstDow).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ]
  while (cells.length % 7 !== 0) cells.push(null)

  function cellIso(day: number) {
    return `${viewYear}-${String(viewMonth + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
  }

  function isDisabled(iso: string) {
    return (!!min && iso < min) || (!!max && iso > max)
  }

  return (
    <div ref={wrapRef} className="dp-wrap">
      <button
        type="button"
        id="dp-trigger"
        className="dp-trigger"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen(o => !o)}
      >
        <span>{value ? formatDisplay(value) : 'Pick a date'}</span>
        <svg className="dp-cal-icon" width="15" height="15" viewBox="0 0 15 15" fill="none"
          stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="1" y="2" width="13" height="12" rx="2"/>
          <line x1="4.5" y1="0.5" x2="4.5" y2="3.5"/>
          <line x1="10.5" y1="0.5" x2="10.5" y2="3.5"/>
          <line x1="1" y1="6" x2="14" y2="6"/>
        </svg>
      </button>

      {open && (
        <div className="dp-popover" role="dialog" aria-label="Choose a date" aria-modal="true">
          <div className="dp-header">
            <button type="button" className="dp-nav-btn" onClick={prevMonth} aria-label="Previous month">
              <ChevronLeft size={15} strokeWidth={2.5} />
            </button>
            <span className="dp-month-label">{MONTHS[viewMonth]} {viewYear}</span>
            <button type="button" className="dp-nav-btn" onClick={nextMonth} aria-label="Next month">
              <ChevronRight size={15} strokeWidth={2.5} />
            </button>
          </div>

          <div className="dp-grid" role="grid" aria-label={`${MONTHS[viewMonth]} ${viewYear}`}>
            {DOW.map(d => (
              <div key={d} role="columnheader" className="dp-dow">{d}</div>
            ))}
            {cells.map((day, idx) => {
              if (day === null) return <div key={`_${idx}`} className="dp-cell-empty" aria-hidden="true" />
              const iso = cellIso(day)
              const selected = iso === value
              const isToday = iso === today
              const disabled = isDisabled(iso)
              return (
                <button
                  key={iso}
                  type="button"
                  role="gridcell"
                  className={[
                    'dp-day',
                    selected             ? 'dp-selected'    : '',
                    isToday && !selected ? 'dp-today-mark'  : '',
                    disabled             ? 'dp-disabled'    : '',
                  ].filter(Boolean).join(' ')}
                  disabled={disabled}
                  aria-selected={selected}
                  aria-current={isToday ? 'date' : undefined}
                  onClick={() => { if (!disabled) select(iso) }}
                >
                  {day}
                </button>
              )
            })}
          </div>

          <div className="dp-footer">
            <button type="button" className="dp-today-btn" onClick={() => select(today)}>
              Today
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
