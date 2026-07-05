---
name: recap-data-collect
description: Shared data collection skill for RecapAgent tasks. Reuses Tushare data capability through TUSHARE_TOKEN and local scripts, with retry, cache, and fallback handling.
---

# Recap Data Collect

Use this skill when a recap task needs market data. It owns only engineering concerns:
retry, cache, fallback data, manifest-friendly outputs, and error reporting. Financial
table selection should follow the installed `tushare` skill or upstream Tushare docs,
not copied into this skill as separate market knowledge.

## Run

```bash
python3 skills/recap-data-collect/scripts/collect.py --task daily --output-dir artifacts/data
```

Required secret: `TUSHARE_TOKEN`.

Outputs are JSON files under the chosen output directory. Cache files live under
`artifacts/cache` by default and fallback fixtures may be placed under `fallback-data`.

