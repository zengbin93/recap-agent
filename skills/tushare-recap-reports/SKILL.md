---
name: tushare-recap-reports
description: Generate Tushare-based first-double and tenbagger watch recap reports.
---

# Tushare Recap Reports

Use this skill for the Tushare stock recap chain from PR #1:

- `first-double`: screen A-share stocks whose recent half-year price move has doubled.
- `tenbagger-watch`: score the first-double pool for second-stage tenbagger follow-up.
- `full-chain`: run both reports in sequence.

The skill is self-contained under `skills/tushare-recap-reports` and writes HTML,
CSV, and JSON artifacts. It expects `TUSHARE_TOKEN` in the environment or in a
repo-root `.env` file.

The default first-double screen uses Tushare `daily` prices multiplied by
`adj_factor` and normalized by the interval-end factor (the `qfq` price mode), requires at least 80 available trading days,
keeps only SH/SZ/BJ A shares, and excludes ST/delisting-risk names. If an
adjustment factor or cached price series is missing, the report records a data
warning and the watch report tries to backfill the candidate's date range.

```bash
python3 skills/tushare-recap-reports/scripts/run.py full-chain
```

Common options:

```bash
python3 skills/tushare-recap-reports/scripts/run.py first-double --end-date 20260529 --min-pct-change 100
python3 skills/tushare-recap-reports/scripts/run.py tenbagger-watch --source-report artifacts/reports/tushare-recap-reports/first_double/latest.json
```

For an explicit raw-price comparison or a different coverage threshold:

```bash
python3 skills/tushare-recap-reports/scripts/run.py first-double --price-mode raw --min-trading-days 100
```

The second-stage report is a scored research queue, not a tenbagger prediction.
Its scoring version and any source/backfill data warnings are included in the
JSON and HTML output. Fundamental quality, catalysts, and forward returns are
planned follow-ups rather than hidden assumptions in the current score.

Default output directory: `artifacts/reports/tushare-recap-reports`.
Default Tushare cache directory: `artifacts/cache/tushare`.
