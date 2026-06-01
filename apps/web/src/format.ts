// Presentation helpers. Times are formatted in the *report's* timezone (tz_name),
// not the viewer's, so "Sunset 20:47" reads correctly for the queried location.

export function formatTime(iso: string | null, tz: string): string {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: tz,
    }).format(new Date(iso))
  } catch {
    return '—'
  }
}

export function formatDayTime(iso: string | null, tz: string): string {
  if (!iso) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, {
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: tz,
    }).format(new Date(iso))
  } catch {
    return '—'
  }
}

/** 1–10 → a band used for color + label. */
export function scoreBand(score: number): 'excellent' | 'good' | 'fair' | 'poor' {
  if (score >= 8) return 'excellent'
  if (score >= 6) return 'good'
  if (score >= 4) return 'fair'
  return 'poor'
}

export function scoreLabel(score: number): string {
  return { excellent: 'Excellent', good: 'Good', fair: 'Fair', poor: 'Poor' }[scoreBand(score)]
}

export function todayIso(): string {
  return new Date().toISOString().slice(0, 10)
}
