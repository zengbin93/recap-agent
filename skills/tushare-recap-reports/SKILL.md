---
name: tushare-recap-reports
description: Generate Tushare-based first-double and tenbagger watch recap reports.
---

# Tushare Recap Reports

Use this skill for the Tushare stock recap chain:

- `first-double`: build the eligible A-share research universe and separately mark stocks whose recent half-year price move has doubled.
- `tenbagger-watch`: score the full eligible A-share universe for potential-stock follow-up.
- `full-chain`: run both reports in sequence.

The skill is self-contained under `skills/tushare-recap-reports` and writes HTML,
CSV, JSON, and (for `full-chain`) a Feishu interactive card payload. It expects
`TUSHARE_TOKEN` in the environment or in a repo-root `.env` file. Set
`TUSHARE_URL` to use a Tushare-compatible proxy.

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

The potential-stock report is a scored research queue, not a tenbagger
prediction. The current scoring version is `v4.0-full-universe`. It starts from
the full eligible A-share universe (active A shares, non-ST, sufficient trading
history) and combines trend stage, liquidity, industry breadth, point-in-time
financial quality, valuation safety margin, benchmark market regime, and
multi-timeframe pullback structure. The half-year-double list remains a
separate retrospective validation sample; it is not an entry condition for the
research pool. Each candidate also carries an archetype, `why_now`, first
rejection point, and thesis-kill condition.

The A tier is gated: a candidate needs both verified quality and a usable setup.
Names outside the fundamental fetch window remain explicitly unverified rather
than being presented as core candidates. The scoring version and any
source/backfill data warnings are included in the JSON and HTML output.

The watch report now separates observed rise drivers from unverified hypotheses.
Observed drivers include industry breadth/median return, recent turnover amount,
recent acceleration, trend drawdown, positive-day ratio, benchmark regime, and
multi-timeframe price structure. The top 80 provisional names attempt to load
Tushare `fina_indicator` evidence as of the report cutoff date; future-announced
rows are excluded to avoid look-ahead leakage. Missing permissions or missing
filings are shown as warnings instead of being treated as a reason for the rise.

The technical ranking runs across the full universe using the daily-market cache.
To keep the job within the GitHub Actions time budget, point-in-time
`fina_indicator` evidence is refreshed only for the top 80 pre-financial names;
the rest are explicitly not eligible for A/B priority until that evidence is
available.

When run through the `potential` GitHub Actions task, the card is sent after the
report chain succeeds. The card explicitly distinguishes full-A-share coverage,
the final research queue, and the half-year-strong replay sample.
`FEISHU_POTENTIAL_WEBHOOK_URL` is preferred; when it is not configured, the
existing daily webhook is used as the fallback target.

Default output directory: `artifacts/reports/tushare-recap-reports`.
Default Tushare cache directory: `artifacts/cache/tushare`.
