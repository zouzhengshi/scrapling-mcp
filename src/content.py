"""Consistent HTML extraction and success classification for both engines."""
from __future__ import annotations

import re

from src.models import ScrapeResult, failure

MAX_HTML_CHARS = 4_000_000


def _truncate_markdown(markdown: str, max_chars: int) -> tuple[str, bool, int]:
    # Truncation is machine-readable; preserve useful content even with tiny limits.
    return markdown[:max_chars], len(markdown) > max_chars, len(markdown)


def make_result(url, engine, raw_html, status, final_url, options, headers=None):
    from lxml import html
    import html2text

    if len(raw_html) > MAX_HTML_CHARS:
        return failure(url, engine, "CONTENT_TOO_LARGE", "页面 HTML 超过 400 万字符限制")
    if not raw_html.strip():
        return failure(url, engine, "EMPTY_CONTENT", "页面内容为空")
    document = html.fromstring(raw_html, base_url=final_url)
    title = " ".join("".join(document.xpath("//title/text()")).split())[:500] or None
    sample = raw_html[:12000].lower()
    challenge_title = bool(title and re.search(
        r"^(just a moment[.!…]*|attention required!?|verify you are human|access denied)$",
        title.lower(),
    ))
    challenge = challenge_title or bool(re.search(
        r'<(?:script|form)[^>]+(?:/cdn-cgi/challenge-platform/|id=["\x27]challenge-form)', sample
    ))
    code, message = None, None
    if status == 429:
        code, message = "RATE_LIMITED", "目标站点限制请求频率"
    elif challenge:
        code, message = "BLOCKED", "目标返回了反爬挑战页"
    elif status is None or not 200 <= status < 300:
        code, message = "HTTP_ERROR", f"目标返回 HTTP {status}"
    result = ScrapeResult(url, engine, "", False, final_url=final_url, status_code=status, title=title)
    if code:
        result.error_code, result.error = code, message
        result.retryable = status == 429 or (status is not None and status >= 500)
        retry_after = next((v for k, v in (headers or {}).items() if k.lower() == "retry-after"), None)
        if retry_after:
            result.metadata["retry_after"] = str(retry_after)[:100]
        return result
    for element in document.xpath("//script|//style|//noscript|//template"):
        element.drop_tree()
    selector = options.get("css_selector")
    if selector:
        selected = document.cssselect(selector)
        if not selected:
            result.error_code, result.error = "SELECTOR_NOT_FOUND", "CSS 选择器未匹配到内容"
            return result
    elif options.get("main_content", True):
        selected = document.xpath("//main|//article") or [document]
        selected = [node for node in selected if not any(parent in selected for parent in node.iterancestors())]
        for node in selected:
            for noise in node.xpath(".//nav|.//footer|.//aside"):
                noise.drop_tree()
    else:
        selected = [document]
    converter = html2text.HTML2Text()  # Never share mutable parser state across calls.
    converter.ignore_links = not options.get("include_links", True)
    converter.ignore_images = True
    converter.body_width = 0
    converter.baseurl = final_url
    fragment = "\n".join(html.tostring(node, encoding="unicode") for node in selected)
    markdown = converter.handle(fragment).strip()
    if not markdown:
        result.error_code, result.error = "EMPTY_CONTENT", "提取后正文为空"
        return result
    result.markdown, result.truncated, original = _truncate_markdown(markdown, options["max_chars"])
    result.success = True
    result.metadata = {"original_markdown_length": original, "truncated": result.truncated}
    result.summary = {
        "title": title,
        "status_code": status,
        "content_chars": original,
        "link_count": len(document.xpath("//a[@href]")),
        "image_count": len(document.xpath("//img")),
        "paragraph_count": len(document.xpath("//p")),
    }
    return result
