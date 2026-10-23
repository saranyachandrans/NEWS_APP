"""Shared helpers: paths, config, logging, HTML/whitespace cleanup."""
import hashlib
import html
import logging
import os
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

# Project root = parent of the src/ folder
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def resolve_path(*parts: str) -> str:
    """Join path parts onto the project root (absolute paths are kept)."""
    return os.path.join(BASE_DIR, *parts)


def load_config(config_path: str = "config.yaml") -> dict:
    path = config_path if os.path.isabs(config_path) else resolve_path(config_path)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_logger(name: str, level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
    )
    return logging.getLogger(name)


class _TextExtractor(HTMLParser):
    """Collects visible text from an HTML fragment."""

    _SKIP = {"script", "style"}
    _BREAKS = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BREAKS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BREAKS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def strip_html(text) -> str:
    """Remove HTML tags/scripts and decode entities (also double-encoded ones)."""
    if not text:
        return ""
    text = str(text)
    if "<" in text or "&" in text:
        parser = _TextExtractor()
        try:
            parser.feed(text)
            parser.close()
            text = "".join(parser.parts)
        except Exception:
            text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


def collapse_ws(text) -> str:
    """Replace any run of whitespace (incl. nbsp) with a single space."""
    return re.sub(r"[\s\u00a0]+", " ", text or "").strip()


def normalize_url(url: str) -> str:
    """Canonical form of a URL (no fragment, no utm_* tracking params)."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                       urlencode(query), ""))


def make_id(url: str) -> str:
    """Stable, URL-safe article id (16 hex chars) derived from the article URL."""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]
