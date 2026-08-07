#!/usr/bin/env python3
"""Night sky scoring — converts raw sky/weather metrics into 0–10 scores."""

# Below this weather score, clouds stop being one factor among four and become a
# hard gate: the night is unshootable regardless of how dark or moon-free it is.
# See WEATHER_VETO_THRESHOLD's use in rate_night for why a ceiling is needed at
# all, and why it only applies below the threshold rather than always.
WEATHER_VETO_THRESHOLD = 4.0


def rate_night(
    moon_score: float,
    dark_score: float,
    weather_score: float | None,
    bortle_score: float | None,
) -> dict:
    """
    Compute an overall night rating (0–10) from component scores (each 0–10).

    Uses a weighted geometric mean so that every factor proportionally
    influences the result — a single bad factor pulls the score down
    naturally without requiring an extra penalty term.

    Weights (redistribute automatically when a factor is unavailable):
      Weather   40%  — clouds / precip make the night unusable
      Moon      25%  — illumination washes out faint targets
      Dark time 25%  — moon-free hours within astronomical night
      Bortle    10%  — site light pollution (fixed for a location)

    Formula: score = 10 × weighted_geometric_mean
    The geometric mean naturally punishes low factors — a single zero
    (complete cloud cover, full moon) zeros the whole score, and a factor
    of 1/10 with 40% weight contributes (0.1)^0.4 ≈ 0.25× to the product.

    Weather veto
    ------------
    The geometric mean is proportional, and that under-punishes bad weather: a
    true zero zeros the night, but a 3/10 at 40% weight only removes ~40% of the
    total, so three near-perfect factors carry the rest.  A new-moon night under
    100% cirrus scored 6.0 ("Good") with *zero* clear dark hours — clouds are a
    hard gate, not a proportional penalty.

    So when weather < WEATHER_VETO_THRESHOLD, the overall score is capped at the
    weather score. Above the threshold the geometric mean stands unmodified, so
    nights with genuinely usable time keep their full range (a night that's 4.8
    on weather but otherwise excellent still scores 7.2, not 4.8). The cap is
    deliberately one-sided rather than an always-on ceiling: weather is usually
    the weakest of the four factors, so an unconditional cap would collapse the
    model into "the weather score" on nearly every night.

    When weather is unavailable (beyond the forecast horizon, provider outage)
    there is nothing to veto with, and the geometric mean over the remaining
    factors stands — the same graceful degradation as the weight redistribution.
    """
    named = {
        "weather": (weather_score, 0.40),
        "moon":    (moon_score,    0.25),
        "dark":    (dark_score,    0.25),
        "bortle":  (bortle_score,  0.10),
    }

    available = {k: (s, w) for k, (s, w) in named.items() if s is not None}
    if not available:
        return {"score": None, "components": {}}

    total_w = sum(w for _, w in available.values())
    norm    = {k: w / total_w for k, (_, w) in available.items()}

    wgm = 1.0
    for k, (s, _) in available.items():
        wgm *= (s / 10) ** norm[k]

    score = round(10 * wgm, 1)

    if weather_score is not None and weather_score < WEATHER_VETO_THRESHOLD:
        score = round(min(score, weather_score), 1)

    components = {k: round(s, 1) for k, (s, _) in available.items()}
    return {"score": score, "components": components}


def weighted_weather_score(
    night_points: list,
    night_start,
    night_end,
    rate_fn,
) -> float | None:
    """
    Weighted average weather score across night_points.

    Points that fall inside the astronomical darkness window
    (night_start → night_end) receive 3× weight; twilight / buffer
    points receive 1×.  When there is no darkness window (polar summer,
    etc.) all points are equal-weighted.

    Returns None if night_points is empty.
    """
    if not night_points:
        return None

    pairs = [
        (rate_fn(p),
         3.0 if (night_start and night_end
                 and night_start <= p.time <= night_end)
         else 1.0)
        for p in night_points
    ]
    total_w = sum(w for _, w in pairs)
    return round(sum(r * w for r, w in pairs) / total_w, 1)
