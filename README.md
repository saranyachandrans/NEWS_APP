# Live Indian News

A small end-to-end project that **collects Indian news from RSS feeds, cleans it, and shows it as cards in a Flask web app** with a login button in the navbar.

The project is built in four stages:

1. **Data ingestion**: download articles from many news sites and save them as JSON.
2. **Data cleaning**: turn the raw JSON into a clean dataset (JSON + CSV) ready for analysis such as sentiment classification.
3. **Data preprocessing**: remove stop words (keeping named entities) and create `all-MiniLM-L6-v2` embeddings, appended to each article (for "similar news" and later models).
4. **Web app**: browse, search and filter the news as cards.

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
│       ├── cleaning_report.json
│       ├── indian_news_vectorized.json    # clean news + text_processed + "vector"
│       ├── indian_news_vectors.npz        # same vectors as a numpy matrix + ids
│       └── preprocessing_report.json
├── src/
│   ├── utils.py                         # config, logging, HTML stripping, article ids
│   ├── components/
│   │   ├── dataingestion.py             # DataIngestion
│   │   ├── datacleaning.py              # DataCleaning
│   │   ├── datapreprocessing.py         # DataPreprocessing (stop words, entities, embeddings)
│   │   └── similarity.py                # SimilarityIndex (similar news from the vectors)
│   └── pipelines/
│       ├── dataingestionpipeline.py     # DataIngestionPipeline
│       ├── datacleaningpipeline.py      # DataCleaningPipeline
│       └── datapreprocessingpipeline.py # DataPreprocessingPipeline
├── templates/
│   ├── base.html                        # layout + navbar (Login button)
│   ├── index.html                       # news cards, search, filters, pagination
│   ├── article.html                     # single news page + similar news
│   ├── login.html
│   └── 404.html
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

Run all stages with `python main.py` (ingest, then clean, then preprocess).

### Findings from the first dataset

- The first dataset (460 articles from The Hindu and Indian Express) had **no HTML tags, emojis, or URLs**, only curly quotes, a few non-breaking spaces and double spaces.
- **Only 49 of the 460 articles had a description**, all from The Hindu. The Indian Express feeds gave none, which is why ingestion now falls back to the article page's meta description. For articles without a description, the title is the only text.

---

## Step 3: Data preprocessing (stop words, named entities, embeddings)

**Component:** `src/components/datapreprocessing.py`
**Pipeline:** `src/pipelines/datapreprocessingpipeline.py`

Reads the cleaned news (`data/processed/indian_news_clean.json`) and, for every article:

1. **Removes stop words** from the `text` field (title + description) using the standard English stop-word list.
2. **Keeps named entities intact**: stop words that belong to a name are not removed (see below).
3. **Creates a 384-dimensional embedding** of the result with `sentence-transformers/all-MiniLM-L6-v2`.
4. **Appends** `text_processed` (the text that was embedded) and `vector` to the original article.

The result goes to `data/processed/indian_news_vectorized.json`; the cleaned file is left untouched, so the web app does not have to load the long vectors.

```bash
pip install sentence-transformers          # also installs PyTorch; first run downloads the model
python main.py --stage preprocess
# or
python -m src.pipelines.datapreprocessingpipeline
```

### Stop words

- The standard English list (the NLTK list) is built in, so no download is needed.
- **Words that change the meaning are kept** (`stopwords.keep_words` in `config.yaml`): `no`, `not`, `nor`, `never`, `none`, `against`, `after`, `before`, `over`, `under`, `above`, `below`, `between`, `off`, `up`, `down`, `out`, `more`, `most`, `only`, `all`. Removing "no" from "No toilets, ID cards or safety gear" would reverse its meaning.
- Add your own with `stopwords.extra_words` (for example `["says", "said"]`).
- Only the stop-word itself is deleted; punctuation, numbers and symbols stay, and hyphenated words (`state-of-the-art`) are left alone.
- **Quote every word in the YAML lists.** Unquoted `no` and `off` are read by YAML as `false`; the code stops with a clear error if it finds such a value.
- Set `stopwords.remove: false` to embed the original text instead. Transformer models read whole sentences, so stop-word removal can slightly lower similarity quality; it is worth comparing both on your own data.

### Named entities

`all-MiniLM-L6-v2` itself never removes words, so it does not damage named entities. **The stop-word step does**: "WHO" and "IT" are on the stop-word list, and "The Hindu" becomes "Hindu" and "Bank of India" becomes "Bank India". With `named_entities.keep: true` these are protected.

| Method | How | Notes |
|---|---|---|
| `heuristic` (default) | Uses capitalisation: acronyms (`WHO`, `IT`), connectors between capitalised words (`Bank of India`, `Jammu and Kashmir`), `The` before a name (`The Hindu`), and a capitalised word following a name | No download. Cautious: when unsure it keeps the word, so it sometimes keeps a stop word that was not part of a name. In Title Case or ALL-CAPS headlines it is less precise (ALL-CAPS texts are left untouched). |
| `spacy` | spaCy named-entity recognition (people, places, organisations, groups, events ...; labels in `spacy_labels`) | More accurate. `pip install spacy` and `python -m spacy download en_core_web_sm` |

Keep `data_cleaning.lowercase: false`: capital letters are how names are found.

On the first dataset (460 articles) it removed about 1,400 stop words (articles went from 15.4 to 12.4 words on average), kept 74 stop words inside names, and left every `no`, `not`, `never` and `against` in place.

### Embeddings

- Model: `sentence-transformers/all-MiniLM-L6-v2` (`embedding.model_name`), 384 numbers per article, unit length (cosine similarity = dot product).
- The model lowercases its input, so it cannot tell "WHO" from "who" by itself; keeping the word is still better than deleting it.
- Texts longer than the model's limit (256 word pieces) are truncated by the model; headlines plus a short description fit.

### Outputs

- `indian_news_vectorized.json`: every original field plus `text_processed` and `vector`.
- `indian_news_vectors.npz`: the same vectors as a numpy matrix with matching `ids`. Use this for similarity search (it is much smaller than the JSON).
- `preprocessing_report.json`: model, vector length, stop words removed and kept, average words before and after, time taken.

Re-running recomputes the vectors for **all** articles. Do not mix vectors from different runs or settings.

### Using the vectors (for the "similar news" step later)

```python
import numpy as np
data = np.load("data/processed/indian_news_vectors.npz")
ids, vectors = data["ids"], data["vectors"]

i = 0                                    # position of an article
scores = vectors @ vectors[i]            # cosine similarity to every article
top = np.argsort(scores)[::-1][1:4]      # 3 most similar (skip itself)
similar_ids = ids[top]
```

---

## Step 4: Flask web app

**Files:** `app.py`, `templates/`, `static/style.css`

```bash
python app.py
```

Open http://127.0.0.1:5000.

Features:

- **News cards:** source and category chips, headline, a three-line description, time posted ("2h ago", full IST time on hover) and a "Read more" link. Clicking a card opens the article **inside the app** at `/article/<id>`; it does not send the reader to the source website.
- **Article page:** same navbar, the story's source, category, date, author, summary and tags, a "Back to all news" link, and a smaller "Read original" link to the source site. Unknown article ids show a 404 page.
- **Similar news:** below the article, the 3 most similar stories are shown as cards with a "% match" tag. They are found by comparing article vectors (`indian_news_vectors.npz`, from Step 3): the article's vector is compared with every other article's vector by cosine similarity, and the closest 3 are returned (the article itself is never included). The logic is in `src/components/similarity.py` and is called by `get_similar_articles()` in `app.py`. Similar news comes from any source, so the same story from another outlet can appear. The number of cards is `flask_app.similar_count`; `flask_app.similar_min_score` hides weak matches (default `0` always shows the 3 closest; with MiniLM, around `0.4` is a sensible cut-off).
  - Without the vectors file, the section shows a hint to run `python main.py --stage preprocess` instead of cards.
  - Articles collected after the last preprocess run have no vector yet; they show a message until preprocessing is run again (their own page still works).
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
| `data_preprocessing` | Input/output files, stop-word lists, named-entity method (`heuristic` or `spacy`), embedding model and batch size |
| `flask_app` | Host, port, debug, cards per page, similar-news cards and minimum score, secret key, users |

---

## Notes and limitations

- The feed URLs were written from memory and **have not been tested against the live sites**. Run ingestion once, read the feed summary, and disable any feed that fails.
- Some sites block automated requests or change their feed addresses over time.
- The category dropdown only shows `general` until you run the new ingestion; the first dataset was collected before categories were recorded.
- Articles are headlines and short summaries, not full article text, so the article page shows the summary (and the title only, when the source gave none).

## Possible next steps

- Boost similar-news ranking with the same category or recent publication date, on top of the vector score.
- Sentiment analysis on the `text` field (the project name in `setup.py` is `live-news-sentiment`), with the sentiment shown on each card.
- Scheduled ingestion (cron or Task Scheduler) so the dataset keeps growing.
- Restrict selected pages or features to logged-in users, and add a sign-up page backed by a database.
