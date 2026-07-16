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
repo-root `.env` file. Set `TUSHARE_URL` to use a Tushare-compatible proxy.

```bash
python3 skills/tushare-recap-reports/scripts/run.py full-chain
```

Common options:

```bash
python3 skills/tushare-recap-reports/scripts/run.py first-double --end-date 20260529 --min-pct-change 100
python3 skills/tushare-recap-reports/scripts/run.py tenbagger-watch --source-report artifacts/reports/tushare-recap-reports/first_double/latest.json
```

Default output directory: `artifacts/reports/tushare-recap-reports`.
Default Tushare cache directory: `artifacts/cache/tushare`.
