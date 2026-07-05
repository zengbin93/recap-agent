"""全球市场复盘工程公共工具层。

分层约定：
- ``recap_agent.feishu``  —— 飞书 webhook 解析、签名与发送。
- ``recap_agent.data``   —— tushare 采集封装、缓存与降级。
- ``recap_agent.reports`` —— HTML / 卡片渲染。

本包零运行时依赖；tushare / pandas 仅在数据采集时按需 import。
"""

__version__ = "0.1.0"
