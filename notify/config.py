"""
企业微信通知配置

Webhook URL 优先级:
  1. 环境变量 QUANT_WECOM_WEBHOOK
  2. 项目根目录 .env 文件（待实现）
"""

import os
import re
from pathlib import Path
from typing import Optional

# 环境变量名
ENV_WEBHOOK = "QUANT_WECOM_WEBHOOK"

# 企业微信 Webhook URL 格式
_WECOM_PATTERN = re.compile(
    r"^https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[a-f0-9\-]+$"
)

# 消息去重（简单内存 set，后续迁到 NotificationLog 表）
_sent_hashes: set[str] = set()

# 通知开关
_notify_enabled: bool = True


def get_webhook_url() -> Optional[str]:
    """获取企业微信 Webhook URL。"""
    # 1. 环境变量
    url = os.environ.get(ENV_WEBHOOK)
    if url:
        return url.strip()

    # 2. .env 文件（简单解析，避免依赖 python-dotenv）
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == ENV_WEBHOOK:
                return v.strip().strip('"').strip("'")

    return None


def validate_webhook_url(url: str) -> bool:
    """校验 Webhook URL 格式。"""
    return bool(_WECOM_PATTERN.match(url))


def enable_notify():
    """开启通知。"""
    global _notify_enabled
    _notify_enabled = True


def disable_notify():
    """关闭通知（静默模式，用于回测/调试）。"""
    global _notify_enabled
    _notify_enabled = False


def is_notify_enabled() -> bool:
    """检查通知是否开启。"""
    return _notify_enabled


def is_duplicate(msg_hash: str) -> bool:
    """检查消息是否已发送过。"""
    return msg_hash in _sent_hashes


def mark_sent(msg_hash: str):
    """标记消息已发送。"""
    _sent_hashes.add(msg_hash)
    # 限制内存增长：保留最近 10000 条
    if len(_sent_hashes) > 10000:
        # 清一半
        to_remove = list(_sent_hashes)[:5000]
        for h in to_remove:
            _sent_hashes.discard(h)
