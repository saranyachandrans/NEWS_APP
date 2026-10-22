"""Flask app: news cards + navbar with login.

Run:   python app.py      ->  http://127.0.0.1:5000
Data:  data/processed/indian_news_clean.json (falls back to the raw file)
"""
import json
import math
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

from src.utils import load_config, resolve_path

config = load_config("config.yaml")
CFG = config["flask_app"]
IST = timezone(timedelta(hours=5, minutes=30))  # fixed offset: no tzdata needed on Windows

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or CFG["secret_key"]
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")


# ------------------------------------------------------------------ data
_cache = {"path": None, "mtime": None, "articles": []}


def data_path() -> str:
    clean = resolve_path(config["data_cleaning"]["output_folder"],
                         config["data_cleaning"]["output_json"])
    raw = resolve_path(config["data_ingestion"]["destination_folder"],
                       config["data_ingestion"]["file_name"])
    return clean if os.path.exists(clean) else raw


def parse_dt(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_articles():
    """Load articles, re-reading the file only when it changes on disk."""
    path = data_path()
    if not os.path.exists(path):
        return []
    mtime = os.path.getmtime(path)
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["articles"]

    with open(path, "r", encoding="utf-8") as f:
        articles = json.load(f)

    for a in articles:
        a["description"] = a.get("description") or ""
        a["category"] = a.get("category") or "general"
        a["source"] = a.get("source") or "Unknown"
        a["_dt"] = parse_dt(a.get("published_at"))
        # never put a non-http(s) link (e.g. javascript:) into an href
        if not str(a.get("url", "")).lower().startswith(("http://", "https://")):
            a["url"] = "#"

    floor = datetime.min.replace(tzinfo=timezone.utc)
    articles.sort(key=lambda a: a["_dt"] or floor, reverse=True)
    _cache.update(path=path, mtime=mtime, articles=articles)
    return articles


# ------------------------------------------------------------ templating
@app.template_filter("ist_date")
def ist_date(dt):
    return dt.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST") if dt else ""


@app.template_filter("time_ago")
def time_ago(dt):
    if not dt:
        return ""
    seconds = int((datetime.now(timezone.utc) - dt).total_seconds())
    if seconds < 60:
        return "just now"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit} ago"
    return ""


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@app.context_processor
def inject_globals():
    return {"csrf_token": csrf_token, "current_user": session.get("user")}


def is_safe_next(target: str) -> bool:
    """Only allow redirects to paths on this site."""
    if not target or "\\" in target:
        return False
    parts = urlsplit(target)
    return not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//")


def valid_csrf() -> bool:
    return secrets.compare_digest(session.get("csrf", ""), request.form.get("csrf", ""))


# ---------------------------------------------------------------- routes
@app.route("/")
def index():
    articles = load_articles()
    q = request.args.get("q", "").strip()
    source = request.args.get("source", "")
    category = request.args.get("category", "")

    needle = q.lower()
    filtered = [
        a for a in articles
        if (not source or a["source"] == source)
        and (not category or a["category"] == category)
        and (not needle or needle in f'{a["title"]} {a["description"]}'.lower())
    ]

    per_page = CFG.get("per_page", 12)
    pages = max(1, math.ceil(len(filtered) / per_page))
    page = min(max(1, request.args.get("page", 1, type=int)), pages)
    items = filtered[(page - 1) * per_page: page * per_page]

    return render_template(
        "index.html",
        articles=items,
        total=len(filtered),
        all_total=len(articles),
        page=page,
        pages=pages,
        q=q, source=source, category=category,
        sources=sorted({a["source"] for a in articles}),
        categories=sorted({a["category"] for a in articles}),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.values.get("next", "")
    if session.get("user"):
        return redirect(next_url if is_safe_next(next_url) else url_for("index"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not valid_csrf():
            flash("Session expired. Please try again.", "error")
        else:
            user = next((u for u in CFG["users"] if u["username"] == username), None)
            if user and check_password_hash(user["password_hash"], password):
                session.clear()                 # new session on login
                session["user"] = username
                flash(f"Welcome back, {username}!", "success")
                return redirect(next_url if is_safe_next(next_url) else url_for("index"))
            flash("Invalid username or password.", "error")

    return render_template("login.html", next_url=next_url if is_safe_next(next_url) else "")


@app.route("/logout", methods=["POST"])
def logout():
    if valid_csrf():
        session.clear()
        flash("You have been logged out.", "success")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host=CFG["host"], port=CFG["port"], debug=CFG["debug"])
