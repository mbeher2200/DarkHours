import { useEffect, useRef, useState } from 'react'
import { CalendarDays, ChevronDown } from 'lucide-react'
import type { CalendarResult } from './types'
import { tonightIso, addDaysIso, daySpan } from './format'
import DatePicker from './DatePicker'

export type CalendarPickerState =
  | { phase: 'idle' }
  | { phase: 'loading'; start: string; days: number }
  | { phase: 'done'; data: CalendarResult; days: number }
  | { phase: 'error'; message: string }

const PRESET_DAYS = [7, 14, 30]

function fmtShort(iso: string): string {
  return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

/**
 * Grafana-style time-range picker, adapted for a date-only, forward-looking,
 * <=30-day outlook: a trigger button showing the current selection, opening a
 * two-column popover — quick presets (click-to-apply) beside a custom From/To
 * range (calendar-only inputs + an explicit Apply).
 */
export default function CalendarRangePicker({
  state,
  anchor,
  onApply,
}: {
  state: CalendarPickerState
  anchor: string
  onApply: (start: string, days: number) => void
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [draftStart, setDraftStart] = useState(anchor)
  const [draftEnd, setDraftEnd] = useState(() => addDaysIso(anchor, 6))

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

  function handleDraftStartChange(v: string) {
    setDraftStart(v)
    setDraftEnd(cur => {
      if (cur < v) return v
      if (cur > addDaysIso(v, 29)) return addDaysIso(v, 29)
      return cur
    })
  }

  function applyPreset(days: number) {
    onApply(anchor, days)
    setOpen(false)
  }

  function applyCustom() {
    onApply(draftStart, daySpan(draftStart, draftEnd))
    setOpen(false)
  }

  const span = daySpan(draftStart, draftEnd)
  const customInvalid = !draftStart || !draftEnd || span < 1 || span > 30

  const label =
    state.phase === 'loading' ? 'Checking…'
    : state.phase === 'done'  ? `${fmtShort(state.data.date_start)} – ${fmtShort(state.data.date_end)}`
    : 'Select range'

  const isActivePreset = (days: number) =>
    state.phase === 'done' && state.days === days && state.data.date_start === anchor

  return (
    <div className="crp-wrap" ref={wrapRef}>
      <button
        type="button"
        className="crp-trigger submit"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen(o => !o)}
      >
        <CalendarDays size={13} strokeWidth={2} />
        <span>Time Range: {label}</span>
        <ChevronDown size={13} strokeWidth={2} />
      </button>

      {open && (
        <div className="crp-popover" role="dialog" aria-label="Select outlook range">
          <div className="crp-col">
            <div className="crp-col-title">Custom range</div>
            <div className="crp-field">
              <label>From</label>
              <DatePicker value={draftStart} min={tonightIso()} onChange={handleDraftStartChange} />
            </div>
            <div className="crp-field">
              <label>To</label>
              <DatePicker value={draftEnd} min={draftStart} max={addDaysIso(draftStart, 29)} onChange={setDraftEnd} />
            </div>
            <button type="button" className="nearby-trigger crp-apply" disabled={customInvalid} onClick={applyCustom}>
              Apply range
            </button>
          </div>
          <div className="crp-col crp-col-presets">
            <div className="crp-col-title">Quick ranges</div>
            <ul className="crp-preset-list">
              {PRESET_DAYS.map(d => (
                <li key={d}>
                  <button
                    type="button"
                    className={isActivePreset(d) ? 'active' : ''}
                    onClick={() => applyPreset(d)}
                  >
                    Next {d} days
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}
