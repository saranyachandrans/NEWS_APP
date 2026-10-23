"""Data preprocessing component: stop words out, named entities kept, embeddings in.

For every cleaned article this component
  1. removes stop words from the article text (negations etc. can be kept),
  2. keeps stop words that belong to a named entity ("Bank of India", "The Hindu",
     "IT", "WHO"), so names are not damaged,
  3. embeds the result with all-MiniLM-L6-v2 (sentence-transformers),
  4. appends `text_processed` and `vector` to the original article.

Named entities are found either with a built-in capitalisation heuristic (no
download needed) or with spaCy (more accurate); see `named_entities` in
config.yaml.  all-MiniLM-L6-v2 itself never removes words, so entities are only
at risk from the stop-word step.
"""
import json
import os
import re
import time

import numpy as np

from src.utils import get_logger, load_config, resolve_path

logger = get_logger("DataPreprocessing")

# English stop-word list (the standard NLTK list, embedded so no download is needed).
STOPWORDS = frozenset("""
i me my myself we our ours ourselves you your yours yourself yourselves he him his
himself she her hers herself it its itself they them their theirs themselves what
which who whom this that these those am is are was were be been being have has had
having do does did doing a an the and but if or because as until while of at by for
with about against between into through during before after above below to from up
down in out on off over under again further then once here there when where why how
all any both each few more most other some such no nor not only own same so than too
very can will just should now
you're you've you'll you'd she's it's that'll don't should've aren't couldn't didn't
doesn't hadn't hasn't haven't isn't mightn't mustn't needn't shan't shouldn't wasn't
weren't won't wouldn't
""".split())

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")      # words, numbers, contractions
CONNECTORS = frozenset({"of", "the", "de", "and", "for", "in"})   # inside names: "Bank of India"
SENTENCE_BREAKS = (".", "!", "?", ":", '"', "(")


class StopwordRemover:
    """Removes stop words in place (punctuation, numbers and symbols are left alone)."""

    def __init__(self, keep_words=(), extra_words=(), keep_entities=True,
                 ner_method="heuristic", spacy_model="en_core_web_sm", spacy_labels=()):
        for name, words in (("keep_words", keep_words), ("extra_words", extra_words)):
            bad = [w for w in words if not isinstance(w, str)]
            if bad:
                raise ValueError(
                    f"stopwords.{name} contains non-text values {bad}. YAML reads unquoted "
                    "no/off/on/yes as true/false: put every word in quotes in config.yaml.")
        self.stopwords = (set(STOPWORDS) - {w.lower() for w in keep_words}) \
            | {w.lower() for w in extra_words}
        self.keep_entities = keep_entities
        self.ner_method = ner_method
        self.spacy_labels = set(spacy_labels)
        self._nlp = None
        if keep_entities and ner_method == "spacy":
            self._nlp = self._load_spacy(spacy_model)

    # ------------------------------------------------------------ spaCy
    @staticmethod
    def _load_spacy(model_name):
        try:
            import spacy
        except ImportError as error:
            raise ImportError("named_entities.method 'spacy' needs spaCy: "
                              "pip install spacy") from error
        try:
            return spacy.load(model_name, disable=["lemmatizer"])
        except OSError as error:
            raise OSError(f"spaCy model '{model_name}' is not installed: "
                          f"python -m spacy download {model_name}") from error

    def spacy_entity_spans(self, texts):
        """Character spans of named entities for each text (spaCy NER)."""
        spans = []
        for doc in self._nlp.pipe(texts, batch_size=64):
            spans.append([(e.start_char, e.end_char) for e in doc.ents
                          if not self.spacy_labels or e.label_ in self.spacy_labels])
        return spans

    # ------------------------------------------------------------ tokens
    @staticmethod
    def tokenize(text):
        return [{"start": m.start(), "end": m.end(), "text": m.group(), "lower": m.group().lower()}
                for m in WORD_RE.finditer(text)]

    # ------------------------------------------------------------ heuristic NER
    def heuristic_protected(self, text, toks):
        """Indexes of stop-word tokens that look like part of a name
        (None for ALL-CAPS text, where names cannot be told apart).

        Rules (only stop-word tokens ever need protecting):
          1. ACRONYMS: US, IT, WHO, UN ... (all caps, 2+ letters)
          2. CONNECTORS between capitalised words: Bank of India, Press Trust of India
          3. "The" before a capitalised word: The Hindu
          4. A capitalised stop word that follows a capitalised word: Theresa May
        It is deliberately cautious: when unsure it keeps the word.
        """
        n = len(toks)
        sw = self.stopwords
        alpha = [t for t in toks if t["text"].isalpha()]
        if not alpha:
            return set()

        upper = sum(1 for t in alpha if t["text"].isupper() and len(t["text"]) > 1)
        mostly_upper = len(alpha) >= 4 and upper / len(alpha) >= 0.6      # ALL-CAPS headline
        if mostly_upper:
            return None          # capitals carry no information here: caller keeps the text as is
        big = [t for t in alpha if len(t["text"]) > 3 and t["lower"] not in sw]
        title_case = len(big) >= 4 and \
            sum(1 for t in big if t["text"][0].isupper()) / len(big) >= 0.7   # Every Word Capitalised

        def cap(i):
            return toks[i]["text"][0].isupper()

        def acronym(i):
            t = toks[i]["text"]
            return t.isupper() and len(t) > 1

        def capname(i):
            return (cap(i) and toks[i]["lower"] not in sw) or acronym(i)

        def adjacent(i, j):
            return not text[toks[i]["end"]:toks[j]["start"]].strip()

        def sentence_initial(i):
            before = text[:toks[i]["start"]].rstrip()
            return not before or before.endswith(SENTENCE_BREAKS)

        protected = set()
        for i in range(n):
            if toks[i]["lower"] not in sw:
                continue
            # 1. acronyms
            if acronym(i):
                protected.add(i)
                continue
            # 2. connectors: Capitalised  of [the]  Capitalised
            if toks[i]["lower"] in CONNECTORS and i > 0 and adjacent(i - 1, i) \
                    and (capname(i - 1) or (i - 1) in protected):
                j = i
                while j < n and toks[j]["lower"] in CONNECTORS and \
                        (j == i or adjacent(j - 1, j)):
                    j += 1
                if j < n and adjacent(j - 1, j) and capname(j):
                    protected.update(range(i, j))
                    continue
            # 3. "The Hindu"
            if toks[i]["lower"] == "the" and cap(i) and i + 1 < n \
                    and adjacent(i, i + 1) and capname(i + 1):
                protected.add(i)
                continue
            # 4. "Theresa May" (skipped in Title Case headlines, where everything is capitalised)
            if not title_case and cap(i) and i > 0 and adjacent(i - 1, i) \
                    and not sentence_initial(i) and (capname(i - 1) or (i - 1) in protected):
                protected.add(i)
        return protected

    # ------------------------------------------------------------ main
    def remove(self, text, entity_spans=None):
        """Return (text_without_stopwords, removed_count, protected_stopword_count)."""
        toks = self.tokenize(text)
        protected = set()
        if self.keep_entities:
            if entity_spans is not None:                    # spaCy
                for i, t in enumerate(toks):
                    if any(t["start"] < e and t["end"] > s for s, e in entity_spans):
                        protected.add(i)
            else:                                           # heuristic
                protected = self.heuristic_protected(text, toks)
                if protected is None:                       # ALL CAPS: cannot find names, so
                    return text, 0, 0                       # leave the text untouched

        removed, kept_for_entity = [], 0
        for i, t in enumerate(toks):
            if t["lower"] not in self.stopwords:
                continue
            # leave hyphenated compounds alone: state-of-the-art, co-operative
            before = text[t["start"] - 1] if t["start"] > 0 else ""
            after = text[t["end"]] if t["end"] < len(text) else ""
            if before == "-" or after == "-":
                continue
            if i in protected:
                kept_for_entity += 1
                continue
            removed.append(t)

        if not removed:
            return text, 0, kept_for_entity

        pieces, pos = [], 0
        for t in removed:
            pieces.append(text[pos:t["start"]])
            pos = t["end"]
        pieces.append(text[pos:])
        out = re.sub(r"\s+", " ", "".join(pieces))
        out = re.sub(r"\s+([,.;:!?])", r"\1", out)
        out = re.sub(r"^[\s,;:.-]+", "", out).strip()
        if not out:                                         # never return an empty text
            return text, 0, kept_for_entity
        return out, len(removed), kept_for_entity


class DataPreprocessing:

    def __init__(self, config_path: str = "config.yaml"):
        self.config = load_config(config_path)
        self.s = self.config["data_preprocessing"]
        self.input_path = resolve_path(self.s["input_file"])
        self.output_dir = resolve_path(self.s["output_folder"])

        sw, ne = self.s["stopwords"], self.s["named_entities"]
        self.remove_stopwords = sw.get("remove", True)
        self.ner_method = ne.get("method", "heuristic").lower()
        if self.ner_method not in ("heuristic", "spacy"):
            raise ValueError(f"Unknown named_entities.method '{self.ner_method}'. "
                             "Use 'heuristic' or 'spacy'.")
        self.remover = StopwordRemover(
            keep_words=sw.get("keep_words", []),
            extra_words=sw.get("extra_words", []),
            keep_entities=ne.get("keep", True),
            ner_method=self.ner_method,
            spacy_model=ne.get("spacy_model", "en_core_web_sm"),
            spacy_labels=ne.get("spacy_labels", []),
        )

    # ------------------------------------------------------------ input
    def load_articles(self):
        if not os.path.exists(self.input_path):
            raise FileNotFoundError(
                f"{self.input_path} not found. Run the cleaning stage first: "
                "python main.py --stage clean")
        with open(self.input_path, "r", encoding="utf-8") as f:
            articles = json.load(f)
        if not articles:
            raise ValueError("The cleaned news file is empty.")
        return articles

    def get_text(self, article: dict) -> str:
        text = article.get(self.s.get("text_field", "text"))
        if not text:
            text = f'{article.get("title", "")}. {article.get("description", "")}'
        return text.strip()

    # ------------------------------------------------------------ steps
    def process_texts(self, texts):
        """Stop-word removal for all texts -> (processed_texts, stats)."""
        spans = None
        if self.remover.keep_entities and self.ner_method == "spacy":
            logger.info("Running spaCy named-entity recognition ...")
            spans = self.remover.spacy_entity_spans(texts)

        processed, removed_total, protected_total = [], 0, 0
        words_before = words_after = 0
        for i, text in enumerate(texts):
            if self.remove_stopwords:
                new, removed, protected = self.remover.remove(text, spans[i] if spans else None)
            else:
                new, removed, protected = text, 0, 0
            processed.append(new)
            removed_total += removed
            protected_total += protected
            words_before += len(text.split())
            words_after += len(new.split())

        stats = {
            "stopwords_removed": removed_total,
            "stopwords_kept_inside_names": protected_total,
            "avg_words_before": round(words_before / len(texts), 1),
            "avg_words_after": round(words_after / len(texts), 1),
        }
        return processed, stats

    def _load_model(self):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise ImportError("Embeddings need sentence-transformers: "
                              "pip install sentence-transformers") from error
        name = self.s["embedding"]["model_name"]
        logger.info("Loading embedding model %s (first run downloads it) ...", name)
        return SentenceTransformer(name)

    def embed(self, texts):
        cfg = self.s["embedding"]
        model = self._load_model()
        matrix = model.encode(
            texts,
            batch_size=cfg.get("batch_size", 64),
            show_progress_bar=True,
            normalize_embeddings=cfg.get("normalize", True),
            convert_to_numpy=True,
        )
        return np.asarray(matrix, dtype=np.float32)

    def attach(self, articles, processed_texts, matrix):
        """Original articles + processed text + vector (originals are not modified)."""
        decimals = self.s.get("decimals", 4)
        result = []
        for article, text, row in zip(articles, processed_texts, matrix):
            item = dict(article)
            if self.s.get("store_processed_text", True):
                item[self.s.get("processed_text_field", "text_processed")] = text
            item[self.s.get("vector_field", "vector")] = np.round(row, decimals).tolist()
            result.append(item)
        return result

    # ------------------------------------------------------------ output
    def save(self, vectorized, matrix, report):
        os.makedirs(self.output_dir, exist_ok=True)

        json_path = os.path.join(self.output_dir, self.s["output_json"])
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(vectorized, f, ensure_ascii=False)
        logger.info("Saved vectorised articles to %s", json_path)

        npz_path = os.path.join(self.output_dir, self.s["vectors_file"])
        np.savez_compressed(npz_path, ids=np.array([a["id"] for a in vectorized]),
                            vectors=matrix.astype(np.float32))
        logger.info("Saved vector matrix to %s", npz_path)

        with open(os.path.join(self.output_dir, self.s["report_file"]), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    def initiate_data_preprocessing(self):
        started = time.time()
        articles = self.load_articles()
        texts = [self.get_text(a) for a in articles]

        processed, stats = self.process_texts(texts)
        logger.info("Stop words removed: %d | kept inside names: %d | avg words %.1f -> %.1f",
                    stats["stopwords_removed"], stats["stopwords_kept_inside_names"],
                    stats["avg_words_before"], stats["avg_words_after"])

        logger.info("Creating embeddings for %d articles ...", len(processed))
        matrix = self.embed(processed)
        vectorized = self.attach(articles, processed, matrix)

        report = {
            "articles": len(articles),
            "model": self.s["embedding"]["model_name"],
            "vector_dimensions": int(matrix.shape[1]),
            "stopword_removal": self.remove_stopwords,
            "keep_named_entities": self.remover.keep_entities,
            "entity_method": self.ner_method if self.remover.keep_entities else None,
            "seconds": round(time.time() - started, 2),
            **stats,
        }
        self.save(vectorized, matrix, report)
        logger.info("Preprocessing report: %s", json.dumps(report))
        return vectorized
