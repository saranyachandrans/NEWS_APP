# Live Indian News

A small end-to-end project that **collects Indian news from RSS feeds, cleans it, and shows it as cards in a Flask web app** with a login button in the navbar.

The project is built in three stages:

1. **Data ingestion**: download articles from many news sites and save them as JSON.
2. **Data cleaning**: turn the raw JSON into a clean dataset (JSON + CSV) ready for analysis such as sentiment classification.
3. **Web app**: browse, search and filter the news as cards.

---

## Project structure

```
trash/
├── config.yaml                          # all settings: feeds, ingestion, cleaning, Flask
├── main.py                              # runs the pipelines (ingest / clean / all)
├── app.py                               # Flask web app
├── requirements.txt
├── setup.py
├── data/
│   ├── raw/indian_news.json             # output of ingestion
│   └── processed/                       # output of cleaning
│       ├── indian_news_clean.json
│       ├── indian_news_clean.csv
│       └── cleaning_report.json
├── src/
│   ├── utils.py                         # config loading, logging, HTML stripping
│   ├── components/
│   │   ├── dataingestion.py             # DataIngestion
│   │   └── datacleaning.py              # DataCleaning
│   └── pipelines/
│       ├── dataingestionpipeline.py     # DataIngestionPipeline
│       └── datacleaningpipeline.py      # DataCleaningPipeline
├── templates/
│   ├── base.html                        # layout + navbar (Login button)
│   ├── index.html                       # news cards, search, filters, pagination
│   └── login.html
└── static/style.css
```

---

## Setup

Requires Python 3.10+.

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

---

## Step 1: Data ingestion

**Component:** `src/components/dataingestion.py`
**Pipeline:** `src/pipelines/dataingestionpipeline.py`

Reads the RSS feeds listed in `config.yaml` and saves the articles to `data/raw/indian_news.json`.

What it does:

- **Many sources:** 27 feeds across 9 outlets (The Hindu, Indian Express, Times of India, NDTV, Hindustan Times, Mint, Economic Times, News18, Firstpost). Each feed has a `name` (source), a `category`, and an `enabled` flag.
- **Parallel and resilient:** feeds are fetched in parallel with retries and a browser-like User-Agent. A feed that fails is logged and skipped; it never stops the run.
- **Proper descriptions:** tries the feed's `summary`, then `description`, then `content`, and strips any HTML. If an article still has no description (as with Indian Express), it opens the article page and uses its meta description (limited by `max_description_fetches`).
- **Normalised data:** dates are stored in UTC, tracking parameters (`utm_*`) are removed from URLs, and every article gets a stable `id`.
- **Duplicates:** the same article listed in several feeds is kept once, using the most complete copy.
- **Growing dataset:** new articles are merged into the existing file by URL instead of overwriting it (`merge_with_existing`), capped by `max_articles_kept`.
- **Safe saving:** the file is written to a temporary file first, so a crash never leaves a half-written JSON.

Run it:

```bash
python main.py --stage ingest
# or
python -m src.pipelines.dataingestionpipeline
```

After each run, a **feed summary** is printed showing how many articles each feed returned or why it failed. Set `enabled: false` in `config.yaml` for any feed that fails.

### Raw data fields

| Field | Description |
|---|---|
| `id` | Stable hash of the normalised URL |
| `source`, `category` | News outlet and topic label from `config.yaml` |
| `title`, `description` | Headline and summary text |
| `description_source` | `feed`, `page` (recovered from the article page), or `none` |
| `url`, `author`, `tags` | Link and metadata from the feed |
| `published_at` | UTC ISO timestamp |
| `collected_at`, `last_seen_at` | When the article was first and last collected |

---

## Step 2: Data cleaning

**Component:** `src/components/datacleaning.py`
**Pipeline:** `src/pipelines/datacleaningpipeline.py`

Reads `data/raw/indian_news.json` and writes a cleaned dataset to `data/processed/`.

What it does:

- Removes HTML tags and decodes entities, emojis, URLs, zero-width and control characters.
- Normalises unicode (non-breaking spaces become normal spaces), straightens curly quotes and dashes, and collapses extra whitespace.
- Removes noisy title suffixes such as `| Explained` or `- The Hindu` (configurable regex list).
- Converts all dates to UTC.
- Builds a combined **`text`** field (title + description) for use in text models.
- Drops articles that are missing a title or URL, too short (`min_text_words`), non-English (`english_only`), or duplicates by title.
- Writes `indian_news_clean.json`, `indian_news_clean.csv` (opens correctly in Excel) and a `cleaning_report.json` with counts of what was dropped.

Run it:

```bash
python main.py --stage clean
# or
python -m src.pipelines.datacleaningpipeline
```

Run both stages together with `python main.py`.

### Findings from the first dataset

- The first dataset (460 articles from The Hindu and Indian Express) had **no HTML tags, emojis, or URLs**, only curly quotes, a few non-breaking spaces and double spaces.
- **Only 49 of the 460 articles had a description**, all from The Hindu. The Indian Express feeds gave none, which is why ingestion now falls back to the article page's meta description. For articles without a description, the title is the only text.

---

## Step 3: Flask web app

**Files:** `app.py`, `templates/`, `static/style.css`

```bash
python app.py
```

Open http://127.0.0.1:5000.

Features:

- **News cards:** source and category chips, headline, a three-line description, time posted ("2h ago", full IST time on hover) and a "Read more" link that opens the original article in a new tab.
- **Search and filters:** search box plus source and category dropdowns, with pagination (12 cards per page).
- **Navbar with login:** a **Login** button; after login it shows the username and a **Logout** button.
- **Responsive and theme-aware:** works on phones and follows the device's light or dark mode.
- **Data loading:** reads `data/processed/indian_news_clean.json` (falls back to the raw file) and reloads automatically when the file changes.

### Login

- Session-based login with hashed passwords, a CSRF token on the forms, and a safe redirect after login.
- Demo account: username **`admin`**, password **`news123`**. **Change this before sharing the app.**
- Create a new password hash and put it in `flask_app.users` in `config.yaml`:

  ```bash
  python -c "from werkzeug.security import generate_password_hash as g; print(g('your-password'))"
  ```

- Set a real secret key with the `SECRET_KEY` environment variable instead of the placeholder in `config.yaml`.
- All news pages are public; logging in currently only changes the navbar. Users are stored in `config.yaml`; there is no sign-up page or user database yet.

---

## Configuration (`config.yaml`)

| Section | Controls |
|---|---|
| `rss_feeds` | Feed list: `name`, `category`, `url`, `enabled` |
| `data_ingestion` | Output file, timeout, retries, parallel workers, User-Agent, description length, merge behaviour, page-description fallback |
| `data_cleaning` | Input/output paths, URL/emoji/quote handling, lowercase, minimum words, duplicate and English-only filters, title suffix patterns |
| `flask_app` | Host, port, debug, cards per page, secret key, users |

---

## Notes and limitations

- The feed URLs were written from memory and **have not been tested against the live sites**. Run ingestion once, read the feed summary, and disable any feed that fails.
- Some sites block automated requests or change their feed addresses over time.
- The category dropdown only shows `general` until you run the new ingestion; the first dataset was collected before categories were recorded.
- Articles are headlines and short summaries, not full article text.

## Possible next steps

- Sentiment analysis on the `text` field (the project name in `setup.py` is `live-news-sentiment`), with the sentiment shown on each card.
- Scheduled ingestion (cron or Task Scheduler) so the dataset keeps growing.
- Restrict selected pages or features to logged-in users, and add a sign-up page backed by a database.
