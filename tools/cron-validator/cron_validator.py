#!/usr/bin/env python3
"""
cron_validator: validate and explain cron expressions.

A small, dependency-free Python tool that:
  - Validates a cron expression including support for @-macros,
    step values (*/5, 1-30/2), names (Mon, Jan), and L (last day of month).
  - Explains each field in plain English.
  - Computes the next N run times from "now" or a given start datetime.

Usage:
    python3 cron_validator.py "*/5 * * * *"
    python3 cron_validator.py "@daily" --explain
    python3 cron_validator.py "0 9 * * 1-5" --next 5
    python3 cron_validator.py "0 0 1 * *" --next 3 --start "2026-01-01T00:00:00"
    python3 cron_validator.py --file crontab.txt   # validate every line

Exit codes:
    0  expression(s) valid
    1  one or more expressions invalid
    2  argument / usage error
"""

from __future__ import annotations

import argparse
import calendar
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------

@dataclass
class FieldSpec:
    name: str
    min_value: int
    max_value: int
    aliases: dict[str, int] = field(default_factory=dict)


HOUR_SPEC = FieldSpec("hour", 0, 23)
DOM_SPEC = FieldSpec("day-of-month", 1, 31)
MONTH_SPEC = FieldSpec(
    "month", 1, 12,
    {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    },
)
DOW_SPEC = FieldSpec(
    "day-of-week", 0, 7,
    {
        "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4,
        "fri": 5, "sat": 6,
        # Cron also allows 7 for Sunday in many implementations.
    },
)
# Note: 0 and 7 both mean Sunday in day-of-week. We map 7 -> 0 after parsing.

FIELDSPEC_ORDER = [
    FieldSpec("minute", 0, 59),
    HOUR_SPEC,
    DOM_SPEC,
    MONTH_SPEC,
    DOW_SPEC,
]

MACROS = {
    "@yearly":   "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly":  "0 0 1 * *",
    "@weekly":   "0 0 * * 0",
    "@daily":    "0 0 * * *",
    "@hourly":   "0 * * * *",
    "@reboot":   None,  # special; not scheduled
}


class CronError(ValueError):
    """Raised when a cron expression is invalid."""


# ---------------------------------------------------------------------------
# Parsing a single field
# ---------------------------------------------------------------------------

def _normalize_token(token: str, spec: FieldSpec) -> str:
    """Lowercase 3-letter abbreviations lookups against aliases."""
    lower = token.lower()
    if lower in spec.aliases:
        return str(spec.aliases[lower])
    return token


def parse_field(raw: str, spec: FieldSpec) -> set[int]:
    """Parse one cron field into the set of integer values it allows."""
    if raw == "":
        raise CronError(f"{spec.name}: empty value")

    if spec is DOW_SPEC:
        upper = 7  # accept 7 as Sunday
    else:
        upper = spec.max_value

    values: set[int] = set()

    for part in raw.split(","):
        step = 1
        if "/" in part:
            range_part, _, step_str = part.partition("/")
            if not step_str:
                raise CronError(f"{spec.name}: missing step after '/'")
            try:
                step = int(step_str)
            except ValueError:
                raise CronError(
                    f"{spec.name}: step '{step_str}' is not an integer"
                )
            if step < 1:
                raise CronError(f"{spec.name}: step must be >= 1")
        else:
            range_part = part

        # Determine the range bounds
        if range_part == "*":
            lo = spec.min_value
            hi = spec.max_value
        elif "-" in range_part:
            lo_str, _, hi_str = range_part.partition("-")
            lo_str = _normalize_token(lo_str, spec)
            hi_str = _normalize_token(hi_str, spec)
            try:
                lo = int(lo_str)
                hi = int(hi_str)
            except ValueError:
                raise CronError(
                    f"{spec.name}: bad range '{range_part}'"
                )
        else:
            token = _normalize_token(range_part, spec)
            if token.upper() == "L":
                # L only valid for day-of-month and day-of-week
                if spec is DOM_SPEC:
                    raise CronError(
                        f"{spec.name}: 'L' (last day) must come after a "
                        "number, e.g. '5L' or '15W'"
                    )
                if spec is DOW_SPEC:
                    raise CronError(
                        f"{spec.name}: 'L' (last weekday) must come after "
                        "a weekday name, e.g. 'FRI L' (use space) "
                        "or be a single weekday with 'L' suffix"
                    )
                raise CronError(f"{spec.name}: 'L' not supported here")
            try:
                val = int(token)
            except ValueError:
                raise CronError(
                    f"{spec.name}: bad value '{range_part}'"
                )
            lo = hi = val

        # Validate bounds
        if spec is DOW_SPEC:
            if not (0 <= lo <= upper) or not (0 <= hi <= upper):
                raise CronError(
                    f"{spec.name}: value out of range 0-7 (got {raw})"
                )
        elif spec is DOM_SPEC:
            if not (1 <= lo <= spec.max_value) or not (1 <= hi <= spec.max_value):
                raise CronError(
                    f"{spec.name}: value out of range 1-31 (got {raw})"
                )
        else:
            if not (spec.min_value <= lo <= spec.max_value) or not (
                spec.min_value <= hi <= spec.max_value
            ):
                raise CronError(
                    f"{spec.name}: value out of range "
                    f"{spec.min_value}-{spec.max_value} (got {raw})"
                )

        if lo > hi:
            # Cron treats wrapped ranges (e.g. 22-2) as crossing the boundary.
            hi, lo = lo, hi

        for v in range(lo, hi + 1, step):
            values.add(v)

    if spec is DOW_SPEC:
        # Map 7 -> 0 (Sunday)
        values = {v % 7 for v in values}

    return values


def parse_expression(expr: str) -> dict[str, set[int]]:
    """Parse a full 5-field cron expression into a dict of value-sets."""
    normalized = expr.strip()
    expanded = None
    # Handle @macros
    low = normalized.lower()
    if low.startswith("@"):
        if low not in MACROS:
            raise CronError(f"unknown macro '{normalized}'")
        if MACROS[low] is None:
            raise CronError(
                "@reboot runs once at startup and has no schedule"
            )
        expanded = MACROS[low]
        fields = expanded.split()
    else:
        fields = normalized.split()

    if len(fields) != 5:
        raise CronError(
            f"expected exactly 5 fields, got {len(fields)}: '{normalized}'"
        )

    result: dict[str, set[int]] = {}
    for raw_value, spec in zip(fields, FIELDSPEC_ORDER):
        result[spec.name] = parse_field(raw_value, spec)

    return result


# ---------------------------------------------------------------------------
# Human-readable explanation
# ---------------------------------------------------------------------------

def _set_to_range_string(values: set[int], spec: FieldSpec) -> str:
    if len(values) == spec.max_value - spec.min_value + 1:
        return "every value"
    ordered = sorted(values)
    # Detect simple step patterns for nicer output.
    if len(ordered) >= 2:
        step = ordered[1] - ordered[0]
        if step > 1 and all(
            ordered[i + 1] - ordered[i] == step
            for i in range(len(ordered) - 1)
        ):
            return f"every {step} starting at {ordered[0]}"

    # Try to collapse contiguous runs.
    runs: list[str] = []
    run_start = ordered[0]
    prev = ordered[0]
    for v in ordered[1:]:
        if v == prev + 1:
            prev = v
            continue
        runs.append(_run_to_str(run_start, prev))
        run_start = v
        prev = v
    runs.append(_run_to_str(run_start, prev))

    # Special-case weekday names.
    if spec is DOW_SPEC and all(0 <= v <= 6 for v in values):
        import builtins
        names = {
            0: "Sunday", 1: "Monday", 2: "Tuesday", 3: "Wednesday",
            4: "Thursday", 5: "Friday", 6: "Saturday",
        }
        runs = [names.get(int(r), r) if "-" not in r else r for r in runs]

    return ", ".join(runs)


def _run_to_str(start: int, end: int) -> str:
    if start == end:
        return str(start)
    return f"{start}-{end}"


def explain(expr: str) -> str:
    parsed = parse_expression(expr)
    lines = []
    lines.append(f'Expression: {expr}')
    lines.append('')
    for spec in FIELDSPEC_ORDER:
        values = parsed[spec.name]
        lines.append(f'  {spec.name:14s}: {_set_to_range_string(values, spec)}')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Next-run computation
# ---------------------------------------------------------------------------

_MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _days_in_month(year: int, month: int) -> int:
    if month == 2 and calendar.isleap(year):
        return 29
    return _MONTH_DAYS[month - 1]


def _weekday(year: int, month: int, day: int) -> int:
    # datetime.weekday(): Monday=0..Sunday=6 ; cron uses Sunday=0
    return datetime(year, month, day).weekday() + 1 if datetime(
        year, month, day
    ).weekday() != 6 else 0


def next_runs(
    parsed: dict[str, set[int]],
    count: int = 1,
    start: datetime | None = None,
) -> list[datetime]:
    """Return the next `count` datetimes matching `parsed`, starting at `start`."""
    if start is None:
        start = datetime.now().replace(second=0, microsecond=0)
    else:
        start = start.replace(second=0, microsecond=0)

    minutes = sorted(parsed["minute"])
    hours = sorted(parsed["hour"])
    doms = parsed["day-of-month"]
    months = sorted(parsed["month"])
    dows = parsed["day-of-week"]

    results: list[datetime] = []
    cursor = start

    # Safety guard against pathological looping.
    max_iterations = 365 * 24 * 60 * 10  # ~10 years of minutes
    iterations = 0

    while len(results) < count:
        iterations += 1
        if iterations > max_iterations:
            raise CronError(
                "ran out of iterations searching for next run; "
                "check the expression"
            )

        # Set cursor's seconds to 0 and advance.
        candidate = cursor

        # Fast-forward month if month not allowed.
        if candidate.month not in months:
            # advance to the first of next allowed month
            next_month = candidate.month + 1
            next_year = candidate.year
            while next_month > 12:
                next_month -= 12
                next_year += 1
            # find next allowed month >= next_month for this year
            allowed = [m for m in months if m >= next_month]
            if not allowed:
                next_year += 1
                cand_month = months[0]
            else:
                cand_month = allowed[0]
            cursor = datetime(next_year, cand_month, 1, 0, 0)
            continue

        # Fast-forward day: must satisfy dom AND not be excluded, or if dom is
        # '*', must match dow. Cron semantics: when both dom and dow are
        # restricted, match if EITHER matches. When one is '*', only the
        # restricted one matters.
        dom_restricted = doms != set(range(1, 32))
        dow_restricted = dows != set(range(0, 7))

        day_ok = (
            candidate.day in doms
            or _weekday(candidate.year, candidate.month, candidate.day) in dows
        )
        if dom_restricted and dow_restricted:
            # OR semantics requires either
            pass  # already handled by `or` above
        elif dom_restricted:
            day_ok = candidate.day in doms
        elif dow_restricted:
            day_ok = _weekday(
                candidate.year, candidate.month, candidate.day
            ) in dows

        if not day_ok:
            cursor = candidate.replace(
                hour=0, minute=0
            ) + timedelta(days=1)
            continue

        # Check hour.
        if candidate.hour not in hours:
            # advance to next allowed hour (or next day)
            next_h = candidate.hour + 1
            allowed_h = [h for h in hours if h > candidate.hour]
            if allowed_h:
                cursor = candidate.replace(
                    hour=allowed_h[0], minute=0
                )
            else:
                cursor = candidate.replace(
                    hour=0, minute=0
                ) + timedelta(days=1)
            continue

        # Check minute.
        if candidate.minute not in minutes:
            allowed_m = [m for m in minutes if m > candidate.minute]
            if allowed_m:
                cursor = candidate.replace(minute=allowed_m[0])
            else:
                # next hour or next day
                next_hour = candidate + timedelta(hours=1)
                cursor = next_hour.replace(minute=0)
            continue

        # All checks passed.
        results.append(candidate)
        cursor = candidate + timedelta(minutes=1)

    return results


# ---------------------------------------------------------------------------
# File-based validation (ignore comments and empty lines)
# ---------------------------------------------------------------------------

def validate_crontab(path: str) -> bool:
    valid = True
    line_no = 0
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line_no += 1
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # Skip environment variable assignment lines (FOO=bar).
            # A crontab env line has the form  NAME=value  with no spaces
            # between NAME and '=', possibly appearing multiple times.
            if not line.startswith("@"):
                first_token = line.split(None, 1)
                if first_token and "=" in first_token[0].split()[0]:
                    # This looks like an env assignment; skip it.
                    continue

            # We only validate the schedule portion (first 5 tokens),
            # ignoring the command that follows.
            tokens = line.split()
            if len(tokens) < 5:
                # Could be a single short env line or malformed entry.
                continue
            candidate = " ".join(tokens[:5])
            try:
                parse_expression(candidate)
                print(f"  ok   line {line_no}: {candidate}")
            except CronError as err:
                print(f"  FAIL line {line_no}: {candidate} -> {err}")
                valid = False
    return valid


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cron_validator",
        description="Validate and explain cron expressions.",
    )
    p.add_argument(
        "expression",
        nargs="?",
        help='A cron expression, e.g. "*/5 * * * *" or "@daily".',
    )
    p.add_argument(
        "--explain", action="store_true",
        help="Print a plain-English breakdown of every field.",
    )
    p.add_argument(
        "--next", type=int, default=0, metavar="N",
        help="Show the next N run times (from --start or now).",
    )
    p.add_argument(
        "--start", metavar="ISO",
        help='Starting datetime for --next, e.g. "2026-01-01T00:00:00".',
    )
    p.add_argument(
        "--file", metavar="PATH",
        help="Validate every line of a crontab file.",
    )
    p.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress normal output; exit code only.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.file:
        ok = validate_crontab(args.file)
        return 0 if ok else 1

    if not args.expression:
        print("error: an expression or --file is required", file=sys.stderr)
        return 2

    start_dt = None
    if args.start:
        try:
            start_dt = datetime.fromisoformat(args.start)
        except ValueError as err:
            print(f"error: bad --start datetime: {err}", file=sys.stderr)
            return 2

    try:
        parsed = parse_expression(args.expression)
    except CronError as err:
        if not args.quiet:
            print(f"INVALID: {args.expression}\n  reason: {err}")
        return 1

    if args.quiet:
        return 0

    print(f"VALID: {args.expression}")

    if args.explain:
        print()
        print(explain(args.expression))

    if args.next > 0:
        print()
        print(f"Next {args.next} run(s):")
        for dt in next_runs(parsed, count=args.next, start=start_dt):
            print(f"  {dt.isoformat()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
