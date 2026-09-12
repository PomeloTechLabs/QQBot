from __future__ import annotations

import re

_LINK_RE = re.compile(r"(!)?\[([^\]]*)\]\(([^)]+)\)")
_AUTO_LINK_RE = re.compile(r"<(https?://[^>]+)>")
_FENCE_RE = re.compile(r"```(?:[\w.+-]+)?\s*\n?")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BLOCKQUOTE_RE = re.compile(r"^\s*>\s?", re.MULTILINE)
_DONE_TASK_RE = re.compile(r"^\s*[-*+]\s+\[(?:x|X)\]\s*", re.MULTILINE)
_TODO_TASK_RE = re.compile(r"^\s*[-*+]\s+\[\s\]\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_HR_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$", re.MULTILINE)
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)|(?<!_)_(?!\s)(.+?)(?<!\s)_(?!_)", re.DOTALL)
_STRIKE_RE = re.compile(r"~~(.+?)~~", re.DOTALL)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _replace_link(match: re.Match[str]) -> str:
    is_image = bool(match.group(1))
    label = match.group(2).strip()
    url = match.group(3).strip()

    if is_image:
        return label or url
    if not label or label == url:
        return url
    return f"{label}: {url}"


def _replace_italic(match: re.Match[str]) -> str:
    return (match.group(1) or match.group(2) or "").strip()


def sanitize_for_qq(text: str) -> str:
    if not text:
        return ""

    sanitized = text.replace("\r\n", "\n").replace("\r", "\n")
    sanitized = _LINK_RE.sub(_replace_link, sanitized)
    sanitized = _AUTO_LINK_RE.sub(r"\1", sanitized)
    sanitized = _FENCE_RE.sub("", sanitized)
    sanitized = _INLINE_CODE_RE.sub(r"\1", sanitized)
    sanitized = _BOLD_RE.sub(r"\2", sanitized)
    sanitized = _ITALIC_RE.sub(_replace_italic, sanitized)
    sanitized = _STRIKE_RE.sub(r"\1", sanitized)
    sanitized = _HEADING_RE.sub("", sanitized)
    sanitized = _BLOCKQUOTE_RE.sub("", sanitized)
    sanitized = _DONE_TASK_RE.sub("[已处理] ", sanitized)
    sanitized = _TODO_TASK_RE.sub("[待处理] ", sanitized)
    sanitized = _BULLET_RE.sub("- ", sanitized)
    sanitized = _HR_RE.sub("", sanitized)
    sanitized = sanitized.replace("\\*", "*").replace("\\_", "_").replace("\\`", "`")
    sanitized = _MULTI_BLANK_RE.sub("\n\n", sanitized)
    return sanitized.strip()
