---
name: recap-daily
description: Generate the daily global market recap HTML report and Feishu card.
---

# Daily Recap

Use this skill for one concrete problem: produce the daily global market recap.
It calls the shared data collection layer, renders an HTML report, and creates a
Feishu interactive card payload.

```bash
python3 skills/recap-daily/scripts/run.py --dry-run
```

Default output directory: `artifacts/reports`.

