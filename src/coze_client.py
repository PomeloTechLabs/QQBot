"""Coze /v3/chat SSE 流式客户端。

参考 scripts/test_coze.py 的解析逻辑，封装为可复用的异步函数。
"""
from __future__ import annotations

import json
import logging

import httpx

from .config import CozeConfig

logger = logging.getLogger("coze_client")

_COZE_API_URL = "https://api.coze.cn/v3/chat"
_COZE_FILE_UPLOAD_URL = "https://api.coze.cn/v1/files/upload"


class CozeClient:
    """调用 Coze /v3/chat 接口，返回最终 answer 文本。"""

    def __init__(self, config: CozeConfig) -> None:
        self._config = config

    async def _upload_image(self, image_src: str) -> str | None:
        """
        获取图片原始数据并上传到 Coze Files API，返回 file_id。

        image_src 可以是：
          - HTTP/HTTPS URL：先从 QQ CDN 下载（Bot 本地运行可以访问），再上传
          - "base64://" 前缀字符串：直接 base64 解码，无需下载

        Coze 云端无法访问 QQ CDN URL，因此必须先拿到原始字节再上传。
        失败返回 None（调用方可降级为直接传 URL）。
        """
        import base64
        try:
            image_data: bytes
            content_type = "image/jpeg"

            if image_src.startswith("base64://"):
                # NapCat 直接给了 base64 数据，无需网络请求
                raw_b64 = image_src[len("base64://"):]
                image_data = base64.b64decode(raw_b64)
                logger.debug(f"图片来源: base64, 大小={len(image_data)} bytes")
            else:
                # 从 QQ CDN URL 下载原始图片字节
                logger.debug(f"正在下载 QQ 图片: {image_src[:80]}")
                async with httpx.AsyncClient(timeout=30.0) as client:
                    dl_resp = await client.get(image_src, follow_redirects=True)
                if dl_resp.status_code != 200:
                    logger.warning(f"下载 QQ 图片失败 HTTP {dl_resp.status_code}: {image_src[:80]}")
                    return None
                image_data = dl_resp.content
                content_type = dl_resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
                logger.debug(f"图片下载成功: {len(image_data)} bytes, content-type={content_type}")

            ext_map = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp"}
            ext = ext_map.get(content_type, "jpg")
            filename = f"image.{ext}"

            # 上传到 Coze Files API（multipart/form-data）
            headers = {"Authorization": f"Bearer {self._config.api_key}"}
            async with httpx.AsyncClient(timeout=60.0) as client:
                upload_resp = await client.post(
                    _COZE_FILE_UPLOAD_URL,
                    headers=headers,
                    files={"file": (filename, image_data, content_type)},
                )
            if upload_resp.status_code != 200:
                logger.warning(f"上传图片至 Coze 失败 HTTP {upload_resp.status_code}: {upload_resp.text}")
                return None

            result = upload_resp.json()
            # Coze 响应格式：{"code": 0, "data": {"id": "xxx", ...}}
            file_id = (result.get("data") or {}).get("id")
            if file_id:
                logger.info(f"图片上传 Coze 成功: file_id={file_id}, size={len(image_data)} bytes")
            else:
                logger.warning(f"Coze 上传响应中未找到 file_id: {result}")
            return file_id
        except Exception:
            logger.exception(f"图片上传异常: {image_src[:80]}")
            return None

    async def chat(self, messages: list[dict], system_prompt: str | None = None) -> str:
        """
        发送消息列表（含历史）给 Coze，流式读取 SSE，返回最终 answer 字符串。

        Args:
            messages: [{
                "role": "user",
                "content": "...",           # 文本内容
                "content_type": "text",     # 或 "object_string" 若包含图片
                "images": ["url1", "url2"]  # 辅助字段：QQ 图片 URL，会先上传至 Coze
            }, ...]
                      最后一条为当前提问，前面的为历史上下文。
            system_prompt: 可选的额外系统提示词，通过 additional_system_prompt 注入。

        Returns:
            Coze 返回的完整回复文本；若出错或无回复则返回空字符串。
        """
        processed_messages = []
        for msg in messages:
            content = msg.get("content", "")
            images = msg.get("images", [])

            if not images:
                processed_messages.append({
                    "role": msg["role"],
                    "content": content,
                    "content_type": "text"
                })
            else:
                # 构建 Coze 的多模态 content 数组
                contents = []
                if content:
                    contents.append({"type": "text", "text": content})

                for img_url in images:
                    # 优先上传图片取得 file_id（Coze 无法直接访问 QQ CDN URL）
                    file_id = await self._upload_image(img_url)
                    if file_id:
                        contents.append({"type": "image", "file_id": file_id})
                    else:
                        # 降级：直接传 URL（image_url 为字符串，非嵌套对象）
                        contents.append({"type": "image", "image_url": img_url})

                processed_messages.append({
                    "role": msg["role"],
                    "content": json.dumps(contents, ensure_ascii=False),
                    "content_type": "object_string"
                })

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "bot_id": self._config.bot_id,
            "user_id": self._config.user_id,
            "stream": True,
            "additional_messages": processed_messages,
        }
        if system_prompt:
            body["additional_system_prompt"] = system_prompt

        answer = ""

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout) as client:
                async with client.stream("POST", _COZE_API_URL, headers=headers, json=body) as resp:
                    if resp.status_code >= 400:
                        body_bytes = await resp.aread()
                        logger.error(f"Coze API 错误 {resp.status_code}: {body_bytes.decode()}")
                        return ""

                    current_event: str | None = None
                    data_lines: list[str] = []

                    def flush() -> None:
                        nonlocal current_event, data_lines, answer
                        if not data_lines:
                            current_event = None
                            return
                        raw = "\n".join(data_lines).strip()
                        evt = (current_event or "").strip()
                        current_event = None
                        data_lines.clear()

                        if raw == "[DONE]":
                            return

                        try:
                            obj = json.loads(raw)
                        except Exception:
                            return

                        if isinstance(obj, dict) and obj.get("type") == "answer":
                            content = obj.get("content", "")
                            if content:
                                # conversation.message.completed 包含完整文本；
                                # delta 包含增量片段——持续更新，最终以 completed 覆盖
                                answer = content
                                logger.debug(f"[{evt}] answer 长度={len(content)}")

                    async for line in resp.aiter_lines():
                        if line is None:
                            continue
                        s = line.strip()
                        if not s:
                            flush()
                        elif s.startswith(":"):
                            continue  # SSE 注释行
                        elif s.startswith("event:"):
                            current_event = s[6:].strip()
                        elif s.startswith("data:"):
                            data_lines.append(s[5:].strip())
                        else:
                            data_lines.append(s)

                    flush()  # 处理最后一个未以空行结尾的事件

        except httpx.TimeoutException:
            logger.error("Coze API 请求超时")
        except Exception:
            logger.exception("Coze API 调用异常")

        logger.info(f"Coze 最终回复 (长度={len(answer)}): {answer[:200]!r}")
        return answer
