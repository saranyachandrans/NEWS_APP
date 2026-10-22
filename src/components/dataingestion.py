"""Data ingestion component.

Collects articles from many RSS feeds in parallel, extracts a proper
description (summary -> description -> content -> article page meta tag),
strips HTML, normalises dates to UTC, merges with previously collected
articles and saves everything as JSON.
"""
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.utils import collapse_ws, get_logger, load_config, resolve_path, strip_html

logger = get_logger("DataIngestion")

_POST_FOOTER = re.compile(r"\s*The post .{0,200}? appeared first on .{0,100}$", re.I)


# ----------------------------------------------------------------- helpers
def normalize_url(url: str) -> str:
    """Canonical form of a URL (no fragment, no utm_* tracking params)."""
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path,
                       urlencode(query), ""))


def make_id(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class _MetaParser(HTMLParser):
    """Finds the description meta tags of an HTML page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        a = dict(attrs)
        key = (a.get("property") or a.get("name") or "").lower()
        if key in ("og:description", "description", "twitter:description") and a.get("content"):
            self.found.setdefault(key, a["content"])

    @property
    def description(self) -> str:
        for key in ("og:description", "description", "twitter:description"):
            if key in self.found:
                return self.found[key]
        return ""


# ----------------------------------------------------------------- component
class DataIngestion:

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.feeds = [f for f in self.config["rss_feeds"] if f.get("enabled", True)]
        self.s = self.config["data_ingestion"]
        self.destination = resolve_path(self.s["destination_folder"], self.s["file_name"])
        self.session = self._build_session()

    # ---------------------------------------------------------- networking
    def _build_session(self) -> requests.Session:
        retry = Retry(
            total=self.s.get("retries", 3),
            backoff_factor=self.s.get("backoff", 1.0),
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=self.s.get("max_workers", 8) * 2)
        session = requests.Session()
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({
            "User-Agent": self.s.get("user_agent", "Mozilla/5.0"),
            "Accept": "application/rss+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5",
        })
        return session

    # ---------------------------------------------------------- extraction
    def _clean_snippet(self, raw) -> str:
        text = collapse_ws(strip_html(raw))
        text = _POST_FOOTER.sub("", text).strip()
        limit = self.s.get("max_description_chars", 1000)
        if len(text) > limit:
            text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "..."
        return text

    def _extract_description(self, entry) -> str:
        candidates = [entry.get("summary"), entry.get("description")]
        for block in entry.get("content") or []:
            candidates.append(block.get("value"))
        for raw in candidates:
            text = self._clean_snippet(raw)
            if text:
                return text
        return ""

    @staticmethod
    def _parse_date(entry):
        """Return an ISO-8601 UTC timestamp, or None when the feed has no date."""
        for key in ("published_parsed", "updated_parsed"):
            parsed = entry.get(key)
            if parsed:
                return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()
        return None

    def _build_article(self, entry, feed):
        link = (entry.get("link") or "").strip()
        title = collapse_ws(strip_html(entry.get("title", "")))
        if not link or not title:
            return None

        description = self._extract_description(entry)
        tags = [t.get("term", "").strip() for t in entry.get("tags") or [] if t.get("term")]
        now = utc_now()
        return {
            "id": make_id(link),
            "source": feed["name"],
            "category": feed.get("category", "general"),
            "title": title,
            "description": description,
            "description_source": "feed" if description else "",
            "url": link,
            "author": collapse_ws(entry.get("author", "")),
            "tags": tags,
            "published_at": self._parse_date(entry),
            "collected_at": now,
            "last_seen_at": now,
        }

    # ---------------------------------------------------------- fetching
    def _fetch_feed(self, feed):
        """Download and parse one feed -> (feed, articles, error)."""
        try:
            response = self.session.get(feed["url"], timeout=self.s.get("timeout", 20))
            response.raise_for_status()
            parsed = feedparser.parse(response.content)
            if parsed.bozo and not parsed.entries:
                return feed, [], "invalid or empty RSS feed"
            articles = [a for a in (self._build_article(e, feed) for e in parsed.entries) if a]
            return feed, articles, None
        except requests.RequestException as error:
            return feed, [], str(error)[:120]

    def _fetch_page_description(self, url: str) -> str:
        """Fallback: read the article page and use its meta description."""
        try:
            response = self.session.get(url, timeout=self.s.get("timeout", 20))
            response.raise_for_status()
            page = response.content[:300_000].decode("utf-8", errors="replace")
            parser = _MetaParser()
            parser.feed(page)
            return self._clean_snippet(parser.description)
        except Exception:
            return ""

    def _enrich_missing_descriptions(self, articles):
        if not self.s.get("fetch_missing_description", False):
            return
        pending = [a for a in articles
                   if not a["description"] and a.get("description_source") != "none"]
        pending.sort(key=lambda a: a.get("published_at") or "", reverse=True)
        pending = pending[: self.s.get("max_description_fetches", 200)]
        if not pending:
            return

        logger.info("Fetching meta descriptions for %d articles without a summary ...", len(pending))
        found = 0
        with ThreadPoolExecutor(max_workers=self.s.get("max_workers", 8)) as pool:
            futures = {pool.submit(self._fetch_page_description, a["url"]): a for a in pending}
            for future in as_completed(futures):
                article = futures[future]
                text = future.result()
                if text:
                    article["description"] = text
                    article["description_source"] = "page"
                    found += 1
                else:
                    article["description_source"] = "none"  # don't retry forever
        logger.info("Recovered %d/%d descriptions from article pages", found, len(pending))

    # ---------------------------------------------------------- storage
    def _load_existing(self):
        if not self.s.get("merge_with_existing", True) or not os.path.exists(self.destination):
            return []
        try:
            with open(self.destination, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError) as error:
            logger.warning("Could not read existing file (%s); starting fresh", error)
            return []

    def _merge(self, existing, new):
        merged = {}
        for article in existing:
            if not article.get("url"):
                continue
            article.setdefault("id", make_id(article["url"]))
            article.setdefault("category", "general")
            article.setdefault("description_source", "feed" if article.get("description") else "")
            article.setdefault("author", "")
            article.setdefault("tags", [])
            article.setdefault("last_seen_at", article.get("collected_at"))
            merged[article["id"]] = article

        added = 0
        for article in new:
            old = merged.get(article["id"])
            if old is None:
                merged[article["id"]] = article
                added += 1
            else:
                old["last_seen_at"] = article["last_seen_at"]
                if not old.get("description") and article["description"]:
                    old["description"] = article["description"]
                    old["description_source"] = "feed"
                if not old.get("published_at") and article["published_at"]:
                    old["published_at"] = article["published_at"]
        return list(merged.values()), added

    def save_news(self, news_data):
        os.makedirs(os.path.dirname(self.destination), exist_ok=True)
        tmp_path = self.destination + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(news_data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.destination)  # never leaves a half-written file
        logger.info("Saved %d articles to %s", len(news_data), self.destination)

    # ---------------------------------------------------------- main flow
    def fetch_news(self):
        collected, report = [], []
        with ThreadPoolExecutor(max_workers=self.s.get("max_workers", 8)) as pool:
            futures = [pool.submit(self._fetch_feed, feed) for feed in self.feeds]
            for future in as_completed(futures):
                feed, articles, error = future.result()
                report.append((feed, len(articles), error))
                collected.extend(articles)

        logger.info("---- feed summary ----")
        for feed, count, error in sorted(report, key=lambda r: (r[0]["name"], r[0].get("category", ""))):
            label = f'{feed["name"]} / {feed.get("category", "-")}'
            logger.info("%-34s %s", label, f"{count} articles" if not error else f"FAILED: {error}")
        failed = sum(1 for _, _, e in report if e)
        logger.info("%d/%d feeds OK", len(report) - failed, len(report))

        # De-duplicate within this run (same article listed in several feeds).
        # Feeds finish in random order, so rank the richest copy first and
        # fill any gaps in it from the other copies.
        collected.sort(key=lambda a: (bool(a["published_at"]), len(a["description"])), reverse=True)
        unique = {}
        for article in collected:
            kept = unique.setdefault(article["id"], article)
            if kept is article:
                continue
            for field in ("description", "published_at", "author", "tags"):
                if not kept[field] and article[field]:
                    kept[field] = article[field]
            if kept["description"] and not kept["description_source"]:
                kept["description_source"] = "feed"
        return list(unique.values())

    def initiate_data_ingestion(self):
        new_articles = self.fetch_news()
        logger.info("Collected %d unique articles this run", len(new_articles))

        merged, added = self._merge(self._load_existing(), new_articles)
        self._enrich_missing_descriptions(merged)

        merged.sort(key=lambda a: a.get("published_at") or "", reverse=True)
        merged = merged[: self.s.get("max_articles_kept", 20000)]

        with_desc = sum(1 for a in merged if a.get("description"))
        logger.info("%d new articles added | total %d | %d with description",
                    added, len(merged), with_desc)
        self.save_news(merged)
        return merged
