"""
企业微信机器人 Webhook 发送器

API 文档: https://developer.work.weixin.qq.com/document/path/91770

支持的消息类型:
  - text      文本消息（支持 @提醒）
  - markdown  Markdown 消息（支持 <font color>、引用、加粗）
  - news      图文卡片消息（最多 8 条）
  - file      文件消息（需先上传获取 media_id）
  - image     图片消息（base64 + md5）
"""

import hashlib
import json
import logging
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from notify.config import (
    get_webhook_url,
    validate_webhook_url,
    is_notify_enabled,
    is_duplicate,
    mark_sent,
)

logger = logging.getLogger(__name__)


class WeComSenderError(Exception):
    """企业微信发送异常。"""
    pass


class WeComSender:
    """企业微信 Webhook 消息发送器。

    用法:
        sender = WeComSender()
        sender.send_text("大家好，今日买入推荐已出")
        sender.send_markdown("## 买入推荐\\n> 平安银行 得分 0.783")
    """

    def __init__(self, webhook_url: Optional[str] = None):
        url = webhook_url or get_webhook_url()
        if not url:
            raise WeComSenderError(
                "未配置企业微信 Webhook URL，请设置环境变量 QUANT_WECOM_WEBHOOK\n"
                "格式: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY"
            )
        if not validate_webhook_url(url):
            raise WeComSenderError(f"Webhook URL 格式不合法: {url[:60]}...")
        self._webhook_url = url
        self._key = url.split("key=")[-1]

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def send_text(
        self,
        content: str,
        mentioned_list: Optional[list[str]] = None,
        mentioned_mobile_list: Optional[list[str]] = None,
        dedup_key: Optional[str] = None,
    ) -> bool:
        """发送文本消息。

        Args:
            content: 消息正文（最长 2048 字节）
            mentioned_list: @提醒的用户 ID 列表，"@all" 提醒所有人
            mentioned_mobile_list: @提醒的手机号列表
            dedup_key: 去重键，相同 key 的消息不会重复发送

        Returns:
            True if sent, False if suppressed by dedup
        """
        if dedup_key and is_duplicate(dedup_key):
            logger.info(f"消息去重跳过: {dedup_key}")
            return False

        body = {
            "msgtype": "text",
            "text": {"content": content},
        }
        if mentioned_list:
            body["text"]["mentioned_list"] = mentioned_list
        if mentioned_mobile_list:
            body["text"]["mentioned_mobile_list"] = mentioned_mobile_list

        self._post(body)
        if dedup_key:
            mark_sent(dedup_key)
        return True

    def send_markdown(
        self,
        content: str,
        dedup_key: Optional[str] = None,
    ) -> bool:
        """发送 Markdown 消息。

        支持的语法:
          # 标题1  ## 标题2  ### 标题3
          **加粗**  [链接](url)
          > 引用
          <font color="info">绿色</font>
          <font color="warning">橙色</font>
          <font color="comment">灰色</font>

        Args:
            content: Markdown 格式正文（最长 4096 字节）
            dedup_key: 去重键

        Returns:
            True if sent, False if suppressed by dedup
        """
        if dedup_key and is_duplicate(dedup_key):
            logger.info(f"消息去重跳过: {dedup_key}")
            return False

        body = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        self._post(body)
        if dedup_key:
            mark_sent(dedup_key)
        return True

    def send_news(
        self,
        articles: list[dict],
        dedup_key: Optional[str] = None,
    ) -> bool:
        """发送图文卡片消息。

        Args:
            articles: 图文列表，每条 {"title": str, "description": str, "url": str, "picurl": str}
                     最多 8 条
            dedup_key: 去重键

        Returns:
            True if sent, False if suppressed by dedup
        """
        if dedup_key and is_duplicate(dedup_key):
            logger.info(f"消息去重跳过: {dedup_key}")
            return False

        if len(articles) > 8:
            logger.warning(f"图文消息最多 8 条，实际传入 {len(articles)}，截断")
            articles = articles[:8]

        body = {
            "msgtype": "news",
            "news": {"articles": articles},
        }
        self._post(body)
        if dedup_key:
            mark_sent(dedup_key)
        return True

    def send_file(self, file_path: str, dedup_key: Optional[str] = None) -> bool:
        """发送文件（先上传获取 media_id，再推送）。

        Args:
            file_path: 本地文件路径
            dedup_key: 去重键

        Returns:
            True if sent, False if suppressed by dedup
        """
        if dedup_key and is_duplicate(dedup_key):
            logger.info(f"消息去重跳过: {dedup_key}")
            return False

        media_id = self._upload_file(file_path)
        body = {"msgtype": "file", "file": {"media_id": media_id}}
        self._post(body)
        if dedup_key:
            mark_sent(dedup_key)
        return True

    def send_image(self, image_path: str, dedup_key: Optional[str] = None) -> bool:
        """发送图片消息。

        Args:
            image_path: 本地图片路径
            dedup_key: 去重键

        Returns:
            True if sent, False if suppressed by dedup
        """
        if dedup_key and is_duplicate(dedup_key):
            logger.info(f"消息去重跳过: {dedup_key}")
            return False

        import base64

        with open(image_path, "rb") as f:
            data = f.read()

        body = {
            "msgtype": "image",
            "image": {
                "base64": base64.b64encode(data).decode("ascii"),
                "md5": hashlib.md5(data).hexdigest(),
            },
        }
        self._post(body)
        if dedup_key:
            mark_sent(dedup_key)
        return True

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _post(self, body: dict):
        """发送 POST 请求到 Webhook。"""
        if not is_notify_enabled():
            logger.info(f"通知已关闭，跳过发送: {body.get('msgtype', '?')}")
            return

        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(
            self._webhook_url,
            data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("errcode") != 0:
                    raise WeComSenderError(
                        f"企业微信 API 错误: errcode={result.get('errcode')}, "
                        f"errmsg={result.get('errmsg')}"
                    )
                logger.debug(f"消息发送成功: {body.get('msgtype')}")
        except URLError as e:
            raise WeComSenderError(f"网络错误: {e}") from e

    def _upload_file(self, file_path: str) -> str:
        """上传文件到企业微信，返回 media_id。"""
        from pathlib import Path
        import mimetypes

        path = Path(file_path)
        if not path.exists():
            raise WeComSenderError(f"文件不存在: {file_path}")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise WeComSenderError("文件不能超过 20MB")

        # 构造 multipart/form-data
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        filename = path.name

        body = b""
        body += f"--{boundary}\r\n".encode("utf-8")
        body += f'Content-Disposition: form-data; name="media"; filename="{filename}"\r\n'.encode("utf-8")
        body += f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8")
        body += path.read_bytes()
        body += f"\r\n--{boundary}--\r\n".encode("utf-8")

        upload_url = f"https://qyapi.weixin.qq.com/cgi-bin/webhook/upload_media?type=file&key={self._key}"
        req = Request(
            upload_url,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("errcode") != 0:
                    raise WeComSenderError(
                        f"文件上传失败: errcode={result.get('errcode')}, "
                        f"errmsg={result.get('errmsg')}"
                    )
                return result["media_id"]
        except URLError as e:
            raise WeComSenderError(f"文件上传网络错误: {e}") from e


# ------------------------------------------------------------------
# 模块级便捷函数
# ------------------------------------------------------------------

_default_sender: Optional[WeComSender] = None


def _get_sender() -> WeComSender:
    global _default_sender
    if _default_sender is None:
        _default_sender = WeComSender()
    return _default_sender


def send_text(content: str, **kwargs) -> bool:
    """发送文本消息（便捷函数）。"""
    return _get_sender().send_text(content, **kwargs)


def send_markdown(content: str, **kwargs) -> bool:
    """发送 Markdown 消息（便捷函数）。"""
    return _get_sender().send_markdown(content, **kwargs)


def send_news(articles: list[dict], **kwargs) -> bool:
    """发送图文消息（便捷函数）。"""
    return _get_sender().send_news(articles, **kwargs)
