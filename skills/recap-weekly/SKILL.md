---
name: recap-weekly
description: Generate the weekly global market recap HTML report and Feishu card.
---

# Weekly Recap

Use this skill only for weekly recap production. Shared data access remains in
`recap-data-collect`; Feishu delivery remains in `feishu-card-push`.

```bash
python3 scripts/run_recap.py --task weekly --dry-run
```

