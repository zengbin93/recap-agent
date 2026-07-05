---
name: feishu-card-push
description: Push generated recap cards to Feishu bot webhooks with single-group, multi-group, signing, and dry-run support.
---

# Feishu Card Push

Use this skill after a recap task renders an interactive card JSON. It resolves the
target webhook in this order:

1. Task-specific env, such as `FEISHU_DAILY_WEBHOOK_URL` and `FEISHU_DAILY_WEBHOOK_SECRET`.
2. `FEISHU_WEBHOOKS_JSON`, keyed by task name.
3. Default `FEISHU_WEBHOOK_URL` and `FEISHU_WEBHOOK_SECRET`.

Run dry mode before enabling real pushes:

```bash
python3 skills/feishu-card-push/scripts/push.py --task daily --card artifacts/reports/daily-feishu-card.json --dry-run
```

