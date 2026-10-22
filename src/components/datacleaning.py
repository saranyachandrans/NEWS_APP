"""Data cleaning component.

Reads the raw JSON from ingestion and produces a clean dataset ready for
sentiment analysis: HTML/emoji/URL removal, unicode and whitespace
normalisation, title-suffix removal, UTC dates, duplicate/short/non-English
filtering and a combined `text` field (title + description).
"""
import csv
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from src.utils import collapse_ws, get_logger, load_config, resolve_path, strip_html

logger = get_logger("DataCleaning")

QUOTE_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-",
})
EMOJI = re.compile(
    "[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]+"
)
ZERO_WIDTH = re.compile("[\u200b-\u200f\u2060\ufeff\u00ad]")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
URL = re.compile(r"https?://\S+|www\.\S+", re.I)


class DataCleaning:

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.s = self.config["data_cleaning"]
        self.input_path = resolve_path(self.s["input_file"])
        self.output_dir = resolve_path(self.s["output_folder"])
        self.suffix_patterns = [re.compile(p, re.I) for p in self.s.get("title_suffix_patterns", [])]

    # ------------------------------------------------------------ text
    def clean_text(self, text) -> str:
        text = strip_html(text)
        text = unicodedata.normalize("NFKC", text)      # nbsp -> space, ligatures, full-width chars
        if self.s.get("remove_emojis", True):
            text = EMOJI.sub(" ", text)
        text = ZERO_WIDTH.sub("", text)
        text = CONTROL.sub(" ", text)
        if self.s.get("normalize_quotes", True):
            text = text.translate(QUOTE_MAP)
        if self.s.get("remove_urls", True):
            text = URL.sub(" ", text)
        text = collapse_ws(text)
        return text.lower() if self.s.get("lowercase", False) else text

    def clean_title(self, title) -> str:
        title = self.clean_text(title)
        for pattern in self.suffix_patterns:
            title = pattern.sub("", title).strip()
        return title

    @staticmethod
    def parse_date(value):
        """Any ISO / RFC-822 date -> ISO-8601 UTC string, or None."""
        if not value:
            return None
        value = str(value).strip()
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

    @staticmethod
    def build_text(title: str, description: str) -> str:
        if not description or description.lower().startswith(title.lower()):
            return title
        joiner = " " if title[-1] in ".!?:" else ". "
        return f"{title}{joiner}{description}"

    def _non_latin_ratio(self, text: str) -> float:
        letters = [c for c in text if c.isalpha()]
        if not letters:
            return 0.0
        return sum(1 for c in letters if ord(c) > 0x024F) / len(letters)

    # ------------------------------------------------------------ pipeline
    def load_raw(self):
        with open(self.input_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def clean(self, raw):
        report = {"input_articles": len(raw), "dropped_missing_title_or_url": 0,
                  "dropped_non_english": 0, "dropped_too_short": 0,
                  "dropped_duplicate_title": 0}
        cleaned = []

        for item in raw:
            title = self.clean_title(item.get("title", ""))
            url = (item.get("url") or "").strip()
            if not title or not url:
                report["dropped_missing_title_or_url"] += 1
                continue

            description = self.clean_text(item.get("description", ""))
            text = self.build_text(title, description)

            if self.s.get("english_only", True) and \
                    self._non_latin_ratio(text) > self.s.get("max_non_latin_ratio", 0.3):
                report["dropped_non_english"] += 1
                continue
            if len(text.split()) < self.s.get("min_text_words", 4):
                report["dropped_too_short"] += 1
                continue

            cleaned.append({
                "id": item.get("id") or url,
                "source": item.get("source", ""),
                "category": item.get("category", "general"),
                "title": title,
                "description": description,
                "text": text,
                "url": url,
                "published_at": self.parse_date(item.get("published_at")),
                "collected_at": self.parse_date(item.get("collected_at")),
            })

        # newest first, so de-duplication keeps the most recent copy
        cleaned.sort(key=lambda a: a["published_at"] or "", reverse=True)

        if self.s.get("drop_duplicate_titles", True):
            seen, unique = set(), []
            for article in cleaned:
                key = re.sub(r"[^a-z0-9 ]", "", article["title"].lower())
                if key in seen:
                    report["dropped_duplicate_title"] += 1
                    continue
                seen.add(key)
                unique.append(article)
            cleaned = unique

        report["output_articles"] = len(cleaned)
        report["with_description"] = sum(1 for a in cleaned if a["description"])
        report["missing_date"] = sum(1 for a in cleaned if not a["published_at"])
        report["by_source"] = {}
        for a in cleaned:
            report["by_source"][a["source"]] = report["by_source"].get(a["source"], 0) + 1
        return cleaned, report

    def save(self, cleaned, report):
        os.makedirs(self.output_dir, exist_ok=True)

        json_path = os.path.join(self.output_dir, self.s["output_json"])
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2, ensure_ascii=False)

        report_path = os.path.join(self.output_dir, self.s["report_file"])
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        if self.s.get("save_csv", True) and cleaned:
            csv_path = os.path.join(self.output_dir, self.s["output_csv"])
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: opens fine in Excel
                writer = csv.DictWriter(f, fieldnames=list(cleaned[0].keys()))
                writer.writeheader()
                writer.writerows(cleaned)
            logger.info("Saved CSV to %s", csv_path)

        logger.info("Saved %d clean articles to %s", len(cleaned), json_path)

    def initiate_data_cleaning(self):
        raw = self.load_raw()
        cleaned, report = self.clean(raw)
        self.save(cleaned, report)
        logger.info("Cleaning report: %s", json.dumps(
            {k: v for k, v in report.items() if k != "by_source"}))
        return cleaned
