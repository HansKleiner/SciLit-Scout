"""Core data, search, screening and export functions for SciLit Scout v1.1.1."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

APP_VERSION = "1.2.0"
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
EXPORTS = ROOT / "exports"
DATA.mkdir(exist_ok=True)
EXPORTS.mkdir(exist_ok=True)
DB = DATA / "research.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 fingerprint TEXT UNIQUE,
 title TEXT, authors TEXT, year INTEGER, doi TEXT, abstract TEXT, url TEXT,
 source TEXT, sources TEXT DEFAULT '[]', document_type TEXT DEFAULT 'publication',
 full_text_available TEXT DEFAULT 'unknown', language TEXT, keywords TEXT,
 patent_number TEXT, thesis_institution TEXT, category TEXT,
 screening_status TEXT DEFAULT 'pending', exclusion_reason TEXT,
 reviewer_notes TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS search_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, query TEXT, filters TEXT,
 searched_at TEXT, retrieved_count INTEGER, file_name TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, record_id INTEGER, decision TEXT,
 reason TEXT, notes TEXT, reviewer TEXT, decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_doi ON records(doi);
CREATE INDEX IF NOT EXISTS idx_records_status ON records(screening_status);
"""

NEW_COLUMNS = {
    "records": {
        "publication_date": "TEXT",
        "openalex_id": "TEXT",
        "openaire_id": "TEXT",
        "full_text_url": "TEXT",
        "full_text_checked_at": "TEXT",
        "suggested_category": "TEXT",
        "screening_stage": "TEXT DEFAULT 'title_abstract'",
        "updated_at": "TEXT",
    },
    "search_log": {
        "inserted_count": "INTEGER DEFAULT 0",
        "merged_count": "INTEGER DEFAULT 0",
        "file_sha256": "TEXT",
        "tool_version": "TEXT",
    },
    "decisions": {
        "stage": "TEXT DEFAULT 'title_abstract'",
        "reason_code": "TEXT",
    },
}

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con

def init_db():
    """Create or safely migrate a v1.0 SQLite database in place."""
    with db() as con:
        con.executescript(SCHEMA)
        for table, cols in NEW_COLUMNS.items():
            present = {row["name"] for row in con.execute(f"PRAGMA table_info({table})")}
            for name, declaration in cols.items():
                if name not in present:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER,
            event_type TEXT NOT NULL,
            event_data TEXT,
            event_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_record ON decisions(record_id);
        CREATE INDEX IF NOT EXISTS idx_audit_record ON audit_events(record_id);
        """)
        # v1.0 auto-filled category from keywords. Preserve it as a suggestion,
        # not as a researcher-confirmed classification.
        con.execute("""
            UPDATE records
               SET suggested_category=category, category=''
             WHERE COALESCE(category,'')<>'' AND COALESCE(suggested_category,'')=''
        """)
        con.execute("UPDATE records SET updated_at=COALESCE(updated_at,created_at)")
        con.execute("PRAGMA user_version=110")

def norm_doi(value) -> str:
    s = str(value or "").strip().lower()
    s = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi\s*:\s*", "", s)
    return s.rstrip(" .;,").strip()

def norm_text(value) -> str:
    s = str(value or "").lower()
    s = re.sub(r"[^a-zа-яё0-9]+", " ", s, flags=re.I)
    return " ".join(s.split())

def parse_year(value, publication_date=""):
    for candidate in (value, publication_date):
        match = re.search(r"(?:19|20)\d{2}", str(candidate or ""))
        if match:
            year = int(match.group(0))
            if 1800 <= year <= date.today().year + 2:
                return year
    return None

def suggest_category(title="", abstract="", keywords=""):
    text = f"{title} {abstract} {keywords}".lower()
    rules = {
        "T1": [r"piezoelectric", r"пьезоэлект"],
        "T2": [r"speed\s*bump", r"speed\s*breaker", r"лежач\w*\s+полиц", r"искусствен\w*\s+неровност"],
        "T3": [r"vehicle[- ]induced airflow", r"traffic[- ]induced wind", r"аэродинамич", r"воздушн\w*\s+поток"],
        "T5": [r"hybrid", r"combined system", r"комбинирован", r"гибридн"],
        "T4": [r"mechanical energy harvesting", r"road mechanical", r"pressure energy", r"механическ\w*\s+генератор"],
    }
    scores = {key: sum(bool(re.search(pattern, text, flags=re.I)) for pattern in patterns)
              for key, patterns in rules.items()}
    if not scores or max(scores.values()) == 0:
        return ""
    return max(scores, key=scores.get)

def fingerprint(row: dict) -> str:
    doi = norm_doi(row.get("doi"))
    if doi:
        base = "doi:" + doi
    elif row.get("document_type") == "patent" and row.get("patent_number"):
        base = "patent:" + norm_text(row["patent_number"])
    else:
        base = "|".join([
            str(row.get("document_type") or "publication"),
            norm_text(row.get("title")),
            str(row.get("year") or ""),
            norm_text(str(row.get("authors") or "").split(";")[0]),
        ])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()

ALIASES = {
    "title": ["title", "article title", "document title", "название", "заглавие", "ti"],
    "authors": ["authors", "author", "авторы", "автор", "au"],
    "year": ["year", "publication year", "год", "py"],
    "publication_date": ["publication_date", "published", "publication date", "date", "дата публикации"],
    "doi": ["doi", "digital object identifier", "do"],
    "abstract": ["abstract", "аннотация", "ab"],
    "url": ["url", "link", "ссылка", "ur"],
    "language": ["language", "язык", "la"],
    "keywords": ["keywords", "author keywords", "ключевые слова", "kw"],
    "document_type": ["document_type", "тип документа", "type"],
    "full_text_available": ["full_text_available", "full text", "полный текст"],
    "full_text_url": ["full_text_url", "full text url", "ссылка на полный текст"],
    "patent_number": ["patent_number", "publication number", "номер патента"],
    "thesis_institution": ["thesis_institution", "institution", "университет", "организация"],
    "openalex_id": ["openalex_id", "openalex id"],
    "openaire_id": ["openaire_id", "openaire id"],
}

def normalize(row: dict, source: str) -> dict:
    raw = {str(k).strip().lower(): v for k, v in row.items()}
    result = {}
    for key, names in ALIASES.items():
        value = next((raw[n] for n in names if n in raw and pd.notna(raw[n])
                      and str(raw[n]).strip()), "")
        result[key] = str(value).strip() if value != "" else ""
    result["title"] = result["title"] or "Без названия"
    result["doi"] = norm_doi(result["doi"])
    result["source"] = source
    result["year"] = parse_year(result["year"], result["publication_date"])
    result["publication_date"] = result["publication_date"] or ""
    typ = result["document_type"].lower()
    result["document_type"] = (
        "patent" if ("patent" in typ or "патент" in typ) else
        "dissertation" if any(x in typ for x in ("thesis", "dissertation", "диссер")) else
        "publication"
    )
    ft = result["full_text_available"].lower()
    result["full_text_available"] = (
        "yes" if ft in ("yes", "да", "true", "1", "available") else
        "no" if ft in ("no", "нет", "false", "0", "unavailable") else "unknown"
    )
    result["suggested_category"] = suggest_category(
        result["title"], result["abstract"], result["keywords"]
    )
    return result

def _audit(con, record_id, event_type, data):
    con.execute(
        "INSERT INTO audit_events(record_id,event_type,event_data,event_at) VALUES(?,?,?,?)",
        (record_id, event_type, json.dumps(data, ensure_ascii=False, default=str), now_utc())
    )

def import_rows(rows: Iterable[dict], source: str):
    inserted = merged = enriched = 0
    for row in rows:
        r = normalize(row, source)
        key = fingerprint(r)
        with db() as con:
            old = con.execute(
                "SELECT * FROM records WHERE doi=? AND doi<>'' LIMIT 1", (r["doi"],)
            ).fetchone() if r["doi"] else None
            if old is None:
                old = con.execute("SELECT * FROM records WHERE fingerprint=?", (key,)).fetchone()
            if old:
                try:
                    src = json.loads(old["sources"] or "[]")
                except (TypeError, json.JSONDecodeError):
                    src = []
                if source and source not in src:
                    src.append(source)
                updates = {"sources": json.dumps(src, ensure_ascii=False)}
                # Enrich absent bibliographic metadata; never overwrite researcher decisions.
                for field in ("title", "authors", "year", "doi", "abstract", "url",
                              "publication_date", "openalex_id", "openaire_id", "language", "keywords",
                              "full_text_url", "patent_number", "thesis_institution"):
                    value = r.get(field)
                    if value not in (None, "") and old[field] in (None, ""):
                        updates[field] = value
                        enriched += 1
                if old["full_text_available"] == "unknown" and r["full_text_available"] != "unknown":
                    updates["full_text_available"] = r["full_text_available"]
                if not old["suggested_category"] and r["suggested_category"]:
                    updates["suggested_category"] = r["suggested_category"]
                updates["updated_at"] = now_utc()
                assignments = ", ".join(f"{k}=?" for k in updates)
                con.execute(f"UPDATE records SET {assignments} WHERE id=?",
                            (*updates.values(), old["id"]))
                _audit(con, old["id"], "metadata_merged", {"source": source, "fields": list(updates)})
                merged += 1
                continue

            cur = con.execute("""
                INSERT INTO records (
                    fingerprint,title,authors,year,doi,abstract,url,source,sources,
                    document_type,full_text_available,language,keywords,patent_number,
                    thesis_institution,suggested_category,publication_date,openalex_id,
                    full_text_url,openaire_id,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                key, r["title"], r["authors"], r["year"], r["doi"], r["abstract"], r["url"],
                source, json.dumps([source] if source else [], ensure_ascii=False),
                r["document_type"], r["full_text_available"], r["language"], r["keywords"],
                r["patent_number"], r["thesis_institution"], r["suggested_category"],
                r["publication_date"], r["openalex_id"], r["full_text_url"], r["openaire_id"], now_utc(), now_utc()
            ))
            _audit(con, cur.lastrowid, "record_imported", {"source": source})
            inserted += 1
    return inserted, merged

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def log_search(source, query, filters="", count=0, file_name="", notes="",
               inserted=0, merged=0, file_sha256=""):
    with db() as con:
        con.execute("""
            INSERT INTO search_log(source,query,filters,searched_at,retrieved_count,
                file_name,notes,inserted_count,merged_count,file_sha256,tool_version)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (source, query, filters, now_utc(), int(count), file_name, notes,
              int(inserted), int(merged), file_sha256, APP_VERSION))

def _request_json(url, params, api_key="", retries=5):
    """GET JSON with bounded backoff for temporary API/rate-limit failures."""
    headers = {"User-Agent": f"SciLitScout/{APP_VERSION} (systematic review)"}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=(10, 60))
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(min(2 ** attempt, 30))
            continue
        if response.status_code == 429 or response.status_code in (500, 502, 503, 504):
            if attempt >= retries:
                try:
                    detail = response.json().get("message") or response.text[:500]
                except ValueError:
                    detail = response.text[:500]
                raise RuntimeError(
                    f"OpenAlex временно ограничил запрос (HTTP {response.status_code}). "
                    f"Попытки повтора исчерпаны. Ответ сервиса: {detail}"
                )
            retry_after = response.headers.get("Retry-After", "")
            try:
                wait = float(retry_after)
            except (TypeError, ValueError):
                wait = min(2 ** attempt, 30)
            time.sleep(max(1, min(wait, 60)))
            continue
        if response.status_code >= 400:
            try:
                detail = response.json().get("message") or response.text[:500]
            except ValueError:
                detail = response.text[:500]
            if response.status_code == 403 and "key_not_activated" in str(response.text):
                raise RuntimeError("API-ключ OpenAlex ещё не активирован. Проверьте письмо OpenAlex и подтвердите адрес электронной почты.")
            raise RuntimeError(f"OpenAlex вернул HTTP {response.status_code}: {detail}")
        return response.json()
    raise RuntimeError("Не удалось получить ответ OpenAlex.")

def search_openalex(query, start="2010-01-01", end=None, limit=100, email="", api_key=""):
    if not query.strip():
        raise ValueError("Введите поисковый запрос OpenAlex.")
    end = end or date.today().isoformat()
    url, cursor, found = "https://api.openalex.org/works", "*", []
    while cursor and len(found) < int(limit):
        params = {
            "search": query,
            "filter": f"from_publication_date:{start},to_publication_date:{end}",
            "per-page": min(100, int(limit) - len(found)),
            "cursor": cursor,
            "select": "id,doi,title,publication_year,publication_date,authorships,abstract_inverted_index,primary_location,language",
        }
        if email.strip():
            params["mailto"] = email.strip()
        payload = _request_json(url, params, api_key=api_key)
        for work in payload.get("results", []):
            authors = [
                a.get("author", {}).get("display_name", "")
                for a in work.get("authorships", [])
            ]
            inv = work.get("abstract_inverted_index") or {}
            words = []
            for word, positions in inv.items():
                words.extend((position, word) for position in positions)
            abstract = " ".join(word for _, word in sorted(words))
            location = work.get("primary_location") or {}
            found.append({
                "title": work.get("title", ""),
                "authors": "; ".join(x for x in authors if x),
                "year": work.get("publication_year"),
                "publication_date": work.get("publication_date") or "",
                "doi": work.get("doi", ""),
                "abstract": abstract,
                "url": location.get("landing_page_url") or work.get("id", ""),
                "language": work.get("language", ""),
                "openalex_id": work.get("id", ""),
            })
            if len(found) >= int(limit):
                break
        cursor = payload.get("meta", {}).get("next_cursor")
        if not payload.get("results"):
            break
        time.sleep(0.12)
    inserted, merged = import_rows(found, "OpenAlex")
    log_search("OpenAlex", query, f"from={start};to={end};limit={limit}",
               len(found), notes="API metadata search", inserted=inserted, merged=merged)
    return len(found), inserted, merged


def _request_openaire_json(url, params, retries=4):
    """GET OpenAIRE Graph JSON with bounded retries and polite rate-limit handling."""
    headers = {"User-Agent": f"SciLitScout/{APP_VERSION} (literature discovery)"}
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=(10, 60))
        except requests.RequestException:
            if attempt >= retries:
                raise
            time.sleep(min(2 ** attempt, 20))
            continue
        if response.status_code == 429 or response.status_code in (500, 502, 503, 504):
            if attempt >= retries:
                raise RuntimeError(
                    f"OpenAIRE временно недоступен (HTTP {response.status_code}); "
                    "число повторных попыток исчерпано."
                )
            retry_after = response.headers.get("Retry-After", "")
            try:
                wait = float(retry_after)
            except (TypeError, ValueError):
                wait = min(2 ** attempt, 20)
            time.sleep(max(1, min(wait, 60)))
            continue
        if response.status_code >= 400:
            detail = response.text[:500]
            raise RuntimeError(f"OpenAIRE Graph вернул HTTP {response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("OpenAIRE вернул ответ, который не является JSON.") from exc
    raise RuntimeError("Не удалось получить ответ OpenAIRE Graph.")


def _openaire_record(item):
    """Map an OpenAIRE Graph research product to SciLit Scout's import schema."""
    authors = [
        author.get("fullName", "")
        for author in (item.get("authors") or [])
        if isinstance(author, dict) and author.get("fullName")
    ]
    pids = item.get("pids") or []
    doi = next(
        (pid.get("value", "") for pid in pids
         if isinstance(pid, dict) and str(pid.get("scheme", "")).lower() == "doi"),
        ""
    )
    descriptions = item.get("descriptions") or []
    abstract = next((str(value).strip() for value in descriptions if value), "")
    instances = item.get("instances") or []
    urls = []
    for instance in instances:
        if isinstance(instance, dict):
            urls.extend(instance.get("urls") or [])
    language = item.get("language") or {}
    if isinstance(language, dict):
        language = language.get("label") or language.get("code") or ""
    subjects = item.get("subjects") or []
    keywords = []
    for subject in subjects:
        if isinstance(subject, dict):
            value = (subject.get("subject") or {}).get("value")
            if value:
                keywords.append(value)
    publication_date = item.get("publicationDate") or ""
    return {
        "title": item.get("mainTitle") or "",
        "authors": "; ".join(authors),
        "year": parse_year(publication_date),
        "publication_date": publication_date,
        "doi": doi,
        "abstract": abstract,
        "url": (urls[0] if urls else (f"https://doi.org/{doi}" if doi else "")),
        "language": language,
        "keywords": "; ".join(keywords),
        "openaire_id": item.get("id") or "",
    }


def search_openaire(query, start="2010-01-01", end=None, limit=100, mailto=""):
    """Search OpenAIRE Graph for publications with at least one Russian affiliation."""
    if not query or not query.strip():
        raise ValueError("Введите поисковый запрос OpenAIRE.")
    start_year = parse_year(start)
    end_year = parse_year(end or date.today().isoformat())
    if not start_year or not end_year or start_year > end_year:
        raise ValueError("Проверьте начальный и конечный год поиска.")
    limit = max(1, min(int(limit), 2000))
    url = "https://api.openaire.eu/graph/v4/research-products"
    found, page = [], 1
    while len(found) < limit:
        page_size = min(100, limit - len(found))
        params = {
            "search": query.strip(),
            "filter": (
                f"type:publication,authorships.countries:RU,"
                f"from_publication_year:{start_year},to_publication_year:{end_year}"
            ),
            "page": page,
            "page_size": page_size,
        }
        if mailto and mailto.strip():
            params["mailto"] = mailto.strip()
        payload = _request_openaire_json(url, params)
        items = payload.get("results") or []
        if not items:
            break
        found.extend(_openaire_record(item) for item in items)
        if len(items) < page_size:
            break
        page += 1
        time.sleep(0.2)
    inserted, merged = import_rows(found, "OpenAIRE")
    log_search(
        "OpenAIRE", query,
        f"country=RU;from={start_year};to={end_year};limit={limit}",
        len(found), notes="OpenAIRE Graph API v4; publications with Russian author affiliations",
        inserted=inserted, merged=merged
    )
    return len(found), inserted, merged

def _crossref_year(item):
    for key in ("published-online", "published-print", "published", "issued", "created"):
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and parts[0]:
            year = parts[0][0]
            if year:
                return int(year), "-".join(str(x).zfill(2) for x in parts[0])
    return None, ""

def search_crossref(query, start="2010-01-01", end=None, limit=100, mailto=""):
    if not query.strip():
        raise ValueError("Введите библиографический запрос Crossref.")
    end = end or date.today().isoformat()
    url, offset, found = "https://api.crossref.org/works", 0, []
    while len(found) < int(limit):
        rows = min(100, int(limit) - len(found))
        params = {
            "query.bibliographic": query,
            "filter": f"from-pub-date:{start},until-pub-date:{end}",
            "rows": rows, "offset": offset,
        }
        if mailto.strip():
            params["mailto"] = mailto.strip()
        payload = _request_json(url, params).get("message", {})
        items = payload.get("items", [])
        if not items:
            break
        for item in items:
            authors = [
                " ".join(part for part in (author.get("given"), author.get("family")) if part)
                for author in item.get("author", [])
            ]
            year, pubdate = _crossref_year(item)
            abstract = re.sub(r"<[^>]+>", " ", item.get("abstract", "") or "")
            abstract = re.sub(r"\s+", " ", abstract).strip()
            found.append({
                "title": (item.get("title") or [""])[0],
                "authors": "; ".join(authors),
                "year": year,
                "publication_date": pubdate,
                "doi": item.get("DOI", ""),
                "abstract": abstract,
                "url": item.get("URL", ""),
            })
            if len(found) >= int(limit):
                break
        offset += len(items)
        if len(items) < rows:
            break
        time.sleep(0.15)
    inserted, merged = import_rows(found, "Crossref")
    log_search("Crossref", query, f"from={start};to={end};limit={limit}",
               len(found), notes="API metadata search", inserted=inserted, merged=merged)
    return len(found), inserted, merged

def parse_ris(path):
    out, current = [], {}
    for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if line.startswith("TY  -"):
            current = {}
        elif line.startswith("ER  -"):
            if current:
                out.append(current)
            current = {}
        elif len(line) >= 6 and line[2:6] == "  -":
            tag, value = line[:2], line[6:].strip()
            key = {"TI": "title", "T1": "title", "AU": "authors", "PY": "year",
                   "Y1": "publication_date", "DO": "doi", "AB": "abstract",
                   "UR": "url", "KW": "keywords", "LA": "language"}.get(tag)
            if key:
                if key == "authors":
                    current[key] = (current.get(key, "") + "; " + value).strip("; ")
                else:
                    current[key] = (current.get(key, "") + " " + value).strip()
    if current:
        out.append(current)
    return out

def parse_bib(path):
    import bibtexparser
    with open(path, encoding="utf-8-sig") as handle:
        bib_db = bibtexparser.load(handle)
    return [{
        "title": entry.get("title", ""), "authors": entry.get("author", ""),
        "year": entry.get("year", ""), "doi": entry.get("doi", ""),
        "abstract": entry.get("abstract", ""), "url": entry.get("url", ""),
        "keywords": entry.get("keywords", "")
    } for entry in bib_db.entries]

def parse_file(path):
    file_path = Path(path)
    ext = file_path.suffix.lower()
    if ext == ".ris":
        return parse_ris(file_path)
    if ext in (".bib", ".bibtex"):
        return parse_bib(file_path)
    if ext == ".csv":
        return pd.read_csv(file_path, dtype=str, encoding="utf-8-sig").fillna("").to_dict("records")
    if ext in (".xlsx", ".xls"):
        return pd.read_excel(file_path, dtype=str).fillna("").to_dict("records")
    raise ValueError("Поддерживаются CSV, XLSX, RIS и BibTeX.")

def import_file_bytes(file_name: str, data: bytes, source: str):
    temp = DATA / f"_import_{Path(file_name).name}"
    temp.write_bytes(data)
    try:
        rows = parse_file(temp)
        inserted, merged = import_rows(rows, source)
        log_search(source, f"Импорт файла {file_name}", "", len(rows), file_name,
                   notes="Библиографический импорт", inserted=inserted, merged=merged,
                   file_sha256=sha256_bytes(data))
        return len(rows), inserted, merged
    finally:
        temp.unlink(missing_ok=True)

def records(kind=None, status=None):
    query, params, clauses = "SELECT * FROM records", [], []
    if kind:
        clauses.append("document_type=?"); params.append(kind)
    if status:
        clauses.append("screening_status=?"); params.append(status)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY CASE WHEN year IS NULL THEN 1 ELSE 0 END, year DESC, title COLLATE NOCASE"
    with db() as con:
        return [dict(row) for row in con.execute(query, params).fetchall()]

def latest_decision(record_id):
    with db() as con:
        row = con.execute(
            "SELECT * FROM decisions WHERE record_id=? ORDER BY id DESC LIMIT 1",
            (record_id,)
        ).fetchone()
        return dict(row) if row else None

def decide(record_id, decision, reason="", notes="", reviewer="", stage="title_abstract"):
    allowed = {"include", "exclude", "maybe", "pending"}
    if decision not in allowed:
        raise ValueError("Недопустимое решение.")
    if decision == "exclude" and not reason:
        raise ValueError("Для исключения обязательно выберите причину.")
    if stage not in ("title_abstract", "full_text"):
        raise ValueError("Неизвестный этап скрининга.")
    now = now_utc()
    with db() as con:
        row = con.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
        if not row:
            raise ValueError("Публикация не найдена.")
        if decision == "include" and (stage != "full_text" or row["full_text_available"] != "yes"):
            raise ValueError(
                "Финальное включение возможно только на этапе полного текста, "
                "когда доступность полного текста подтверждена как «Да»."
            )
        con.execute("""
            UPDATE records SET screening_status=?, exclusion_reason=?,
                reviewer_notes=?, screening_stage=?, updated_at=? WHERE id=?
        """, (decision, reason if decision == "exclude" else "", notes, stage, now, record_id))
        con.execute("""
            INSERT INTO decisions(record_id,decision,reason,notes,reviewer,decided_at,stage,reason_code)
            VALUES(?,?,?,?,?,?,?,?)
        """, (record_id, decision, reason if decision == "exclude" else "",
              notes, reviewer, now, stage, reason if decision == "exclude" else ""))
        _audit(con, record_id, "screening_decision", {
            "decision": decision, "reason": reason, "notes": notes,
            "reviewer": reviewer, "stage": stage
        })

def set_category(record_id, category):
    if category not in ("", "T1", "T2", "T3", "T4", "T5"):
        raise ValueError("Категория должна быть T1–T5.")
    with db() as con:
        con.execute("UPDATE records SET category=?,updated_at=? WHERE id=?",
                    (category, now_utc(), record_id))
        _audit(con, record_id, "category_set", {"category": category})

def set_full_text(record_id, availability, url=""):
    if availability not in ("yes", "no", "unknown"):
        raise ValueError("Статус полного текста должен быть yes/no/unknown.")
    with db() as con:
        con.execute("""
            UPDATE records SET full_text_available=?,full_text_url=?,
                full_text_checked_at=?,updated_at=? WHERE id=?
        """, (availability, url.strip(), now_utc(), now_utc(), record_id))
        _audit(con, record_id, "full_text_check", {"availability": availability, "url": url})

def search_logs():
    with db() as con:
        return [dict(row) for row in con.execute("SELECT * FROM search_log ORDER BY id DESC")]

def prisma_summary():
    all_records = records()
    logs = search_logs()
    counts = {status: sum(r["screening_status"] == status for r in all_records)
              for status in ("pending", "maybe", "include", "exclude")}
    doc_counts = {kind: sum(r["document_type"] == kind for r in all_records)
                  for kind in ("publication", "patent", "dissertation")}
    return {
        "unique_records": len(all_records),
        "raw_retrieved_sum": sum(int(x.get("retrieved_count") or 0) for x in logs),
        "new_records_sum": sum(int(x.get("inserted_count") or 0) for x in logs),
        "merge_events_sum": sum(int(x.get("merged_count") or 0) for x in logs),
        "statuses": counts, "document_types": doc_counts, "search_sessions": len(logs),
    }

def export_excel(path=None):
    path = Path(path or EXPORTS / "scilit_scout_v1.1.1.xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)
    with db() as con:
        all_records = pd.read_sql_query("SELECT * FROM records ORDER BY id", con)
        logs = pd.read_sql_query("SELECT * FROM search_log ORDER BY id", con)
        decisions = pd.read_sql_query("SELECT * FROM decisions ORDER BY id", con)
        audit = pd.read_sql_query("SELECT * FROM audit_events ORDER BY id", con)
    summary = prisma_summary()
    summary_rows = [
        ("Unique records in local registry", summary["unique_records"]),
        ("Sum of retrieved records across sessions (may include repeat searches)", summary["raw_retrieved_sum"]),
        ("New unique records added across sessions", summary["new_records_sum"]),
        ("Merge/deduplication events recorded", summary["merge_events_sum"]),
        ("Search sessions", summary["search_sessions"]),
    ]
    summary_rows += [(f"Screening: {k}", v) for k, v in summary["statuses"].items()]
    summary_rows += [(f"Document type: {k}", v) for k, v in summary["document_types"].items()]
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        all_records.to_excel(writer, sheet_name="All records", index=False)
        logs.to_excel(writer, sheet_name="Search log", index=False)
        decisions.to_excel(writer, sheet_name="Decisions", index=False)
        audit.to_excel(writer, sheet_name="Audit trail", index=False)
        pd.DataFrame(summary_rows, columns=["Metric", "Value"]).to_excel(
            writer, sheet_name="PRISMA overview", index=False
        )
        for kind, sheet in (("publication", "Publications"), ("patent", "Patents"),
                            ("dissertation", "Dissertations")):
            all_records[all_records["document_type"] == kind].to_excel(
                writer, sheet_name=sheet, index=False
            )
        # Improve usability and guarantee a visible worksheet.
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            for column_cells in worksheet.columns:
                letter = column_cells[0].column_letter
                max_len = min(max((len(str(cell.value or "")) for cell in column_cells[:200]), default=10), 55)
                worksheet.column_dimensions[letter].width = max(12, max_len + 2)
        writer.book.active = 0
    return path

def export_word(path=None):
    from docx import Document
    path = Path(path or EXPORTS / "scilit_scout_report_v1.1.1.docx")
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    doc.add_heading("SciLit Scout v1.1.1 — технический отчёт", 0)
    doc.add_paragraph(f"Сформировано (UTC): {now_utc()}")
    doc.add_paragraph(
        "Отчёт отражает только записи, фактически зарегистрированные в локальной базе. "
        "Сумма результатов поисковых сессий может включать повторные запросы и не является "
        "самостоятельно подтверждённым числом PRISMA."
    )
    summary = prisma_summary()
    doc.add_heading("Состав локального реестра", 1)
    for kind, count in summary["document_types"].items():
        doc.add_paragraph(f"{kind}: {count}")
    doc.add_heading("Текущий статус скрининга", 1)
    for status, count in summary["statuses"].items():
        doc.add_paragraph(f"{status}: {count}")
    doc.add_heading("Поисковые сессии", 1)
    for item in search_logs():
        doc.add_paragraph(
            f"{item.get('source')} | {item.get('searched_at')} | {item.get('query')} "
            f"| найдено: {item.get('retrieved_count')} | новых: {item.get('inserted_count')}"
        )
    doc.add_heading("Методологическое примечание", 1)
    doc.add_paragraph(
        "OpenAlex и Crossref предоставляют библиографические метаданные. Доступность полного "
        "текста должна проверяться исследователем. Автоматическая категория T1–T5 является "
        "подсказкой; подтверждённую категорию назначает эксперт. Поиск в Scopus, Web of Science, "
        "eLibrary и CyberLeninka фиксируется только после фактического получения и импорта "
        "соответствующих выгрузок."
    )
    doc.save(path)
    return path
