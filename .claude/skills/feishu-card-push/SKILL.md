---
name: feishu-card-push
description: 把指定复盘任务（daily/weekly/monthly）的卡片 JSON 推送到飞书群。读取 FEISHU_WEBHOOKS JSON，daily 必填，weekly/monthly 缺省回退 daily。编排最后一步调用。
---

# feishu-card-push

读取 `FEISHU_WEBHOOKS` secret 与卡片 JSON，按任务推送。**daily 群必填**；weekly / monthly 未配置时回退到 daily 群。

## 何时使用
- 编排最后一步：把 daily / weekly / monthly 的卡片发到对应飞书群

## 必需 secret
- `FEISHU_WEBHOOKS`：JSON 字符串，结构如下
  ```json
  {
    "daily":   {"key": "<webhook-key-或-完整URL>", "sign_secret": "<可选>"},
    "weekly":  {"key": "<可选，缺省回退 daily>"},
    "monthly": {"key": "<可选，缺省回退 daily>"}
  }
  ```
  - `key`：飞书自定义机器人 webhook 的末段，或完整 URL；`sign_secret` 仅当机器人开启签名校验时填。

## 运行
```bash
python .claude/skills/feishu-card-push/scripts/push.py \
  --task daily --card artifacts/cards/daily.json
```

## 失败可见性
单条推送失败（网络错误 / 非 2xx）返回非零退出码并打印错误细节；workflow 层用 `continue-on-error` 隔离——**一条失败不拖垮同批其它推送**。
