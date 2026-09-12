"""Bounded, local-only preservation of media attached to a feedback report."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import mimetypes
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx


class FeedbackMediaArchiver:
    """Save QQ CDN/base64 attachments inside the bot workspace with size limits.

    Binary files stay local evidence. Valid public QQ CDN URLs are retained as
    optional issue links so maintainers can inspect image/video evidence before
    the CDN expires.
    """

    _MAX_REDIRECTS = 3

    def __init__(
        self,
        directory: str | Path,
        workspace_root: str | Path,
        max_image_bytes: int,
        max_video_bytes: int,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        raw_directory = Path(directory)
        self._directory = (
            raw_directory
            if raw_directory.is_absolute()
            else self._workspace_root / raw_directory
        ).resolve()
        try:
            self._directory.relative_to(self._workspace_root)
        except ValueError as exc:
            raise ValueError("反馈媒体目录必须位于机器人工作目录内") from exc
        self._max_image_bytes = max(100_000, int(max_image_bytes))
        self._max_video_bytes = max(1_000_000, int(max_video_bytes))

    async def archive(
        self,
        feedback_id: int,
        attachments: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        archived: list[dict[str, Any]] = []
        for index, attachment in enumerate(attachments, start=1):
            kind = str(attachment.get("kind", "")).lower()
            source = str(attachment.get("source", "")).strip()
            if kind not in {"image", "video"}:
                continue
            limit = self._max_image_bytes if kind == "image" else self._max_video_bytes
            try:
                content, content_type = await self._read_source(source, limit)
                target_dir = self._directory / f"feedback_{feedback_id:06d}"
                target_dir.mkdir(parents=True, exist_ok=True)
                suffix = self._suffix_for(kind, content_type, source)
                target = target_dir / f"{kind}_{index:02d}{suffix}"
                target.write_bytes(content)
                archived.append(
                    {
                        "kind": kind,
                        "status": "saved",
                        "path": str(target.relative_to(self._workspace_root)).replace("\\", "/"),
                        "bytes": len(content),
                        "content_type": content_type or "application/octet-stream",
                        "source_url": source if source.startswith(("http://", "https://")) else "",
                    }
                )
            except Exception as exc:
                archived.append(
                    {
                        "kind": kind,
                        "status": "unavailable",
                        "reason": str(exc)[:180],
                    }
                )
        return archived

    async def _read_source(self, source: str, limit: int) -> tuple[bytes, str]:
        if source.startswith("base64://"):
            try:
                content = base64.b64decode(source[len("base64://") :], validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("附件 base64 数据无效") from exc
            if len(content) > limit:
                raise ValueError(f"附件超过 {limit} 字节保存上限")
            return content, ""
        return await self._download_public_url(source, limit)

    async def _download_public_url(self, source: str, limit: int) -> tuple[bytes, str]:
        current_url = self._validate_public_url(source)
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            for _ in range(self._MAX_REDIRECTS + 1):
                async with client.stream("GET", current_url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("附件重定向缺少目标地址")
                        current_url = self._validate_public_url(urljoin(current_url, location))
                        continue
                    response.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > limit:
                            raise ValueError(f"附件超过 {limit} 字节保存上限")
                        chunks.append(chunk)
                    return b"".join(chunks), response.headers.get("content-type", "").split(";", 1)[0]
        raise ValueError("附件重定向次数超过上限")

    @staticmethod
    def _validate_public_url(value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("附件没有可安全下载的公开 HTTP(S) 地址")
        hostname = parsed.hostname
        if hostname.lower() in {"localhost", "localhost.localdomain"}:
            raise ValueError("拒绝保存来自本机或内网的附件")
        try:
            addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ValueError("附件地址无法解析") from exc
        if not addresses:
            raise ValueError("附件地址无法解析")
        for item in addresses:
            try:
                address = ipaddress.ip_address(item[4][0])
            except ValueError as exc:
                raise ValueError("附件地址无效") from exc
            if not address.is_global:
                raise ValueError("拒绝保存来自本机或内网的附件")
        return value

    @staticmethod
    def _suffix_for(kind: str, content_type: str, source: str) -> str:
        extension = mimetypes.guess_extension(content_type or "")
        if extension:
            return ".jpg" if extension == ".jpe" else extension
        path_suffix = Path(urlparse(source).path).suffix.lower()
        if path_suffix and len(path_suffix) <= 8:
            return path_suffix
        return ".jpg" if kind == "image" else ".mp4"
