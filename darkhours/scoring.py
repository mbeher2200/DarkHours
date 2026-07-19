#!/usr/bin/env python3
"""Night sky scoring — converts raw sky/weather metrics into 0–10 scores."""


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
