# cron-validator

A tiny, dependency-free Python tool to **validate, explain, and preview**
cron expressions. A single file. No external packages. Just `python3`.

Perfect for checking crontab edits before installing them, debugging a
schedule that "never fires", or wiring into a CI pipeline that lints your
`crontab` file.

## Features

- Validates 5-field cron expressions
- Supports all the usual syntaxes:
  - wildcards: `*`
  - comma lists: `1,5,10`
  - ranges: `1-30`
  - step values: `*/5`, `1-30/2`, `0-23/4`
  - day and month names: `MON`, `FRI`, `JAN`, `DEC`
  - macros: `@yearly`, `@monthly`, `@weekly`, `@daily`, `@hourly`
- Plain-English explanation of every field with `--explain`
- Predictive next-run listing with `--next N`
- Whole-file linting of a real crontab with `--file`
- Correct Vixie cron "OR" semantics for day-of-month and day-of-week
- Works with Python 3.8 or newer (tested on 3.11)

## Installation

No install needed. Just clone and run:

```
git clone https://github.com/cappy-dev/cron-validator.git
cd cron-validator
python3 cron_validator.py "*/5 * * * *"
```

Want it callable as `cron-validator`? Drop it somewhere on your `PATH`:

```
chmod +x cron_validator.py
sudo cp cron_validator.py /usr/local/bin/cron-validator
```

## Usage

### Basic validation

```
python3 cron_validator.py "*/5 * * * *"
```

Output:

```
VALID: */5 * * * *
```

### Explain a schedule

```
python3 cron_validator.py "0 9 * * 1-5" --explain
```

Output:

```
VALID: 0 9 * * 1-5

Expression: 0 9 * * 1-5

  minute        : 0
  hour          : 9
  day-of-month  : every value
  month         : every value
  day-of-week   : 1-5
```

### Preview upcoming runs

```
python3 cron_validator.py "0 9 * * 1-5" --next 5 --start "2026-07-16T00:00:00"
```

Output:

```
VALID: 0 9 * * 1-5

Next 5 run(s):
  2026-07-16T09:00:00
  2026-07-17T09:00:00
  2026-07-20T09:00:00
  2026-07-21T09:00:00
  2026-07-22T09:00:00
```

The next-run engine correctly skips weekends (day-of-week `1-5`) and honors
the cron rule that when both day-of-month and day-of-week are restricted,
either match fires the job.

### Lint a crontab file

Run `--file` against any crontab to check every non-comment line:

```
python3 cron_validator.py --file my-crontab
```

Output (bad lines show the reason, good ones pass through):

```
  ok   line 5: 0 0 * * 1-5
  ok   line 6: */15 * * * *
  FAIL line 11: 60 * * * * -> minute: value out of range 0-59 (got 60)
  FAIL line 12: 0 * 32 * * -> day-of-month: value out of range 1-31 (got 32)
```

Comments (`#`) and environment assignments (`MAILTO=me@host`) are skipped
automatically, and the command portion after the 5 schedule fields is
ignored.

### Macros

```
python3 cron_validator.py "@daily" --next 3
python3 cron_validator.py "@yearly" --explain
```

The `@reboot` macro is rejected with a clear message since it runs once
at startup and has no recurring schedule to validate.

## Exit codes

```
0  all expressions valid
1  one or more expressions invalid
2  usage / argument error
```

This makes the tool easy to chain in scripts or CI:

```
python3 cron_validator.py --file crontab && echo "crontab OK"
```

## Field reference

Standard 5-field format:

```
 minute  hour  day-of-month  month  day-of-week
```

| field         | range | allowed names
|---------------|-------|---------------
| minute        | 0-59  | (none)
| hour          | 0-23  | (none)
| day-of-month  | 1-31  | (none)
| month         | 1-12  | JAN through DEC
| day-of-week   | 0-7   | SUN(0) through SAT(6); both 0 and 7 mean Sunday

## Examples

Validate a quarterly job (first day of Jan/Apr/Jul/Oct at 2am):

```
python3 cron_validator.py "0 2 1 1,4,7,10 *" --explain --next 4
```

Check a "every other Wednesday" step pattern:

```
python3 cron_validator.py "0 0 * * WED/2" --explain
```

## License

MIT. See `LICENSE`.
