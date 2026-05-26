#!/usr/bin/env python3
"""
PyNightSkyPredictor test runner.

Usage
-----
  python pynighttest.py            Run all tests
  python pynighttest.py --fast     Skip ephemeris tests (no de421.bsp required)
  python pynighttest.py -v         Verbose output (passed through to pytest)
  python pynighttest.py --fast -v  Fast + verbose

The test suite lives in tests/.  This script is the project-standard way to
run it; pytest can also be invoked directly:

  python -m pytest               Same as python pynighttest.py
  python -m pytest -m "not eph"  Same as python pynighttest.py --fast

Exit code mirrors pytest: 0 = all passed, non-zero = failures.
"""

import sys
import pytest


def main() -> int:
    fast_mode = "--fast" in sys.argv
    passthrough = [a for a in sys.argv[1:] if a != "--fast"]

    args = ["tests", "--tb=short"] + passthrough
    if fast_mode:
        args += ["-m", "not eph"]
        print("Fast mode: skipping ephemeris-dependent tests (de421.bsp not required)\n")

    return pytest.main(args)


if __name__ == "__main__":
    sys.exit(main())
