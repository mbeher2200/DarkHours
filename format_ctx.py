"""format_ctx.py — per-request formatting context and pure display helpers.

FormatCtx replaces the module-level _TZ / _units globals in pynightsky.py.
Instantiate once per CLI invocation or per web request; pass it into any
renderer that needs timezone or unit conversions.

Pure helpers (_lp_str, _cardinal) live here because they're needed by
multiple renderers and carry no state dependencies.
"""

import os
import platform
import subprocess
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


# ---------------------------------------------------------------------------
# Unit detection
# ---------------------------------------------------------------------------

def detect_units() -> str:
    """Return 'imperial' for US locale, 'si' otherwise."""
    for var in ("LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES"):
        if os.environ.get(var, "").startswith("en_US"):
            return "imperial"
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "NSGlobalDomain", "AppleLocale"],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip().startswith("en_US"):
                return "imperial"
        except Exception:
            pass
    return "si"


# ---------------------------------------------------------------------------
# Formatting context
# ---------------------------------------------------------------------------

@dataclass
class FormatCtx:
    """Timezone + unit system bundled into a single object.

    Replaces the pynightsky.py globals _TZ and _units.  Safe to instantiate
    once per request in a web context (no shared mutable state).
    """
    tz:    ZoneInfo
    units: str          # 'imperial' | 'si'

    def local(self, dt: datetime) -> datetime:
        """Convert a UTC-aware datetime to the local timezone."""
        return dt.astimezone(self.tz)

    def fmt(self, dt: datetime) -> str:
        """Format as 'May 14,  6:47 PM' (local time, space-padded hour)."""
        local = self.local(dt)
        hour  = int(local.strftime("%I"))
        return local.strftime(f"%b %-d, {hour:>2}:%M %p")

    def fmt_time(self, dt: datetime) -> str:
        """Format as ' 6:47 PM' (local time, space-padded hour, no date)."""
        local = self.local(dt)
        hour  = int(local.strftime("%I"))
        return local.strftime(f"{hour:>2}:%M %p")

    def temp(self, c) -> str:
        """Format a Celsius temperature in the configured unit system."""
        if c is None:
            return "—"
        if self.units == "imperial":
            return f"{round(c * 9 / 5 + 32)}°F"
        return f"{c:.1f}°C"

    def wind(self, ms) -> str:
        """Format a wind speed (m/s) in the configured unit system."""
        if ms is None:
            return "—"
        if self.units == "imperial":
            return f"{ms * 2.237:.1f}mph"
        return f"{ms:.1f}m/s"


# ---------------------------------------------------------------------------
# Pure display helpers (no state dependencies)
# ---------------------------------------------------------------------------

def lp_str(info: dict | None) -> str | None:
    """Format a darksky.lookup() result dict into a single display string."""
    if info is None:
        return None
    if info.get("below_detection"):
        return "Light pollution data unavailable"
    if info.get("sqm") is None:
        return None
    return (f"SQM {info['sqm']}  ·  Zone {info['lp_zone']}"
            f"  ·  Bortle {info['bortle_class']}"
            f"  ({info['bortle_desc']})  [{info['source']}]")


def cardinal(az_deg: float) -> str:
    """Convert an azimuth in degrees to an 8-point cardinal direction string."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(az_deg / 45) % 8]
