"""Guardrail: no naive date.today() calls in backend source.

date.today() returns the local system date, which on a non-UTC machine
differs from the UTC date that the AWS Lambda runtime uses. All date
references in the API and engine must use datetime.now(timezone.utc).date()
so behaviour is identical locally and in prod.

If this test fails, replace the offending date.today() call with
datetime.now(timezone.utc).date() (or reuse an already-captured _now.date()
where one is in scope).
"""
import pathlib
import re

# Source trees that run in the Lambda / API process.
_SOURCE_DIRS = [
    pathlib.Path("apps/api"),
    pathlib.Path("darkhours"),
]

_PATTERN = re.compile(r'\bdate\.today\(\)')


def test_no_naive_date_today_in_backend_source():
    violations = []
    for src_dir in _SOURCE_DIRS:
        for py_file in src_dir.rglob("*.py"):
            if "__pycache__" in py_file.parts:
                continue
            text = py_file.read_text()
            for lineno, line in enumerate(text.splitlines(), 1):
                if _PATTERN.search(line):
                    violations.append(f"{py_file}:{lineno}: {line.strip()}")

    assert not violations, (
        "date.today() found in backend source — use datetime.now(timezone.utc).date() instead:\n"
        + "\n".join(violations)
    )
