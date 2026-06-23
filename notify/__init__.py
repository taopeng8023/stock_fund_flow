"""
通知模块 — 企业微信 Webhook 消息推送

用法:
  from notify.wecom_sender import WeComSender
  sender = WeComSender()
  sender.send_markdown("## 测试消息")

环境变量:
  QUANT_WECOM_WEBHOOK  企业微信机器人 Webhook 完整 URL
                       示例: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
"""

from notify.wecom_sender import WeComSender
from notify.config import get_webhook_url, validate_webhook_url

__all__ = ["WeComSender", "get_webhook_url", "validate_webhook_url"]
