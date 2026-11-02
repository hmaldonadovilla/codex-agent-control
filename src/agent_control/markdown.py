"""Render profile instructions for display without accepting executable HTML."""

from functools import lru_cache
from urllib.parse import urlsplit

from markdown_it import MarkdownIt


class _InstructionMarkdown(MarkdownIt):
    def validateLink(self, url: str) -> bool:
        try:
            scheme = urlsplit(url).scheme.lower()
        except ValueError:
            return False
        return scheme in {"", "http", "https", "mailto"} and super().validateLink(url)


_MARKDOWN = _InstructionMarkdown("js-default").disable("image")


@lru_cache(maxsize=64)
def instructions_html(source: str) -> str:
    """Keep stored/model-facing Markdown intact; hide only the legacy reporting footer."""
    visible = source.split("Agent Control reporting:", 1)[0].strip()
    return _MARKDOWN.render(visible or "No instructions configured.")
