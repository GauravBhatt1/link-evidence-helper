"""Local media library scanner and read API backing store.

This module deliberately has no web-framework dependency.  The hosting app
calls ``LibraryService`` from its HTTP handler, keeping filesystem and TMDB
credentials entirely server-side.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts"}
IGNORED_DIRS = {"@eadir", ".trash", "extras", "featurettes", "samples", "sample", "trailers", "trailer"}
IGNORED_WORDS = ("sample", "trailer", ".part", ".tmp", ".crdownload", ".!qB")
RESOLUTION_RE = re.compile(r"\b(2160p|1080p|720p|480p|4k|uhd)\b", re.I)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
SEASON_EPISODE_RE = re.compile(r"\bS(\d{1,3})\s*E(\d{1,3})(?:\s*[-E]\s*E?(\d{1,3}))?\b", re.I)
X_EPISODE_RE = re.compile(r"\b(\d{1,3})x(\d{1,3})\b", re.I)
SEASON_RE = re.compile(r"\bseason\s*(\d{1,3})\b|\bs(\d{1,3})\b", re.I)
EPISODE_RE = re.compile(r"\b(?:episode|ep|e)\s*(\d{1,3})\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def clean_title(value: str) -> tuple[str, int | None]:
    value = Path(value).stem
    year_match = YEAR_RE.search(value)
    year = int(year_match.group(1)) if year_match else None
    value = SEASON_EPISODE_RE.sub(" ", value)
    value = X_EPISODE_RE.sub(" ", value)
    value = re.sub(r"\b(?:season|episode|ep|s|e)\s*\d{1,3}\b", " ", value, flags=re.I)
    value = re.sub(r"\b(?:19\d{2}|20\d{2})\b", " ", value)
    value = re.sub(r"\b(?:2160p|1080p|720p|480p|4k|uhd|web[- .]?dl|webrip|bluray|brrip|dvdrip|remux|x26[45]|hevc|avc|h\.264|h\.265|aac|ddp?\d?\.?(?:1|0)|atmos|proper|repack|extended|unrated|multi|dual|original)\b", " ", value, flags=re.I)
    value = re.sub(r"[._\-\[\]{}()]+", " ", value)
    value = re.sub(r"^\s*\d{1,3}\s+", "", value)  # [01] / 01 collection ordering prefixes
    value = re.sub(r"\s+", " ", value).strip(" -")
    return value or "Unknown", year


def normalized_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def parse_media_file(path: Path, root: Path, media_type: str) -> dict[str, Any] | None:
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    low_path = str(path).lower()
    if any(word.lower() in low_path for word in IGNORED_WORDS) or any(part.lower() in IGNORED_DIRS or part.startswith(".") for part in path.parts):
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    min_size = max(0, int(os.environ.get("LIBRARY_MIN_FILE_SIZE_MB", "30"))) * 1024 * 1024
    if stat.st_size < min_size:
        return None
    rel = path.relative_to(root)
    parent_title = rel.parts[0] if rel.parts else path.parent.name
    filename_title, filename_year = clean_title(path.name)
    folder_title, folder_year = clean_title(parent_title)
    title, year = (folder_title, folder_year) if folder_title != "Unknown" else (filename_title, filename_year)
    season = episode = episode_end = None
    name = path.stem
    match = SEASON_EPISODE_RE.search(name)
    if match:
        season, episode, episode_end = int(match.group(1)), int(match.group(2)), _safe_int(match.group(3))
    else:
        match = X_EPISODE_RE.search(name)
        if match:
            season, episode = int(match.group(1)), int(match.group(2))
        elif media_type == "tv":
            for text in (path.parent.name, *(p for p in reversed(rel.parts[:-1]))):
                season_match = SEASON_RE.search(text)
                if season_match:
                    season = int(season_match.group(1) or season_match.group(2))
                    break
            ep_match = EPISODE_RE.search(name)
            if ep_match:
                episode = int(ep_match.group(1))
    resolution_match = RESOLUTION_RE.search(name)
    resolution = (resolution_match.group(1).upper().replace("4K", "2160P") if resolution_match else "Unknown").replace("P", "p")
    codec_match = re.search(r"\b(x265|x264|hevc|h\.265|h\.264|av1)\b", name, re.I)
    codec = codec_match.group(1).upper() if codec_match else ""
    langs = [language for language in ("Hindi", "English", "Tamil", "Telugu", "Malayalam", "Kannada", "Punjabi", "Bengali", "Japanese", "Korean") if re.search(rf"\b{re.escape(language)}\b", name, re.I)]
    return {
        "title": title, "year": year, "relative_path": str(rel), "filename": path.name,
        "size": stat.st_size, "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "fingerprint": hashlib.sha1(f"{rel}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest(),
        "season": season, "episode": episode, "episode_end": episode_end, "resolution": resolution,
        "codec": codec, "languages": langs,
    }


class LibraryService:
    def __init__(self, tmdb_key: str, db_path: str | None = None) -> None:
        default_db = os.path.expanduser("~/.local/share/report-link-tools/library.db")
        self.db_path = db_path or os.environ.get("LIBRARY_DB_PATH", default_db)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.tmdb_key = tmdb_key
        self.language = os.environ.get("TMDB_LANGUAGE", "en-US")
        self._scan_lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS media_items (
              id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL, normalized_title TEXT NOT NULL,
              year INTEGER, tmdb_id INTEGER, match_confidence REAL DEFAULT 0, poster_path TEXT, backdrop_path TEXT,
              overview TEXT, genres TEXT DEFAULT '[]', original_language TEXT, local_languages TEXT DEFAULT '[]',
              available INTEGER DEFAULT 1, qualities TEXT DEFAULT '[]', total_files INTEGER DEFAULT 0, total_size INTEGER DEFAULT 0,
              root_key TEXT NOT NULL, relative_path TEXT, date_added TEXT, last_scanned_at TEXT, needs_match INTEGER DEFAULT 0,
              ignored INTEGER DEFAULT 0, metadata_json TEXT DEFAULT '{}', UNIQUE(type, root_key, normalized_title, year)
            );
            CREATE TABLE IF NOT EXISTS media_files (
              id TEXT PRIMARY KEY, media_item_id TEXT NOT NULL, relative_path TEXT NOT NULL, filename TEXT NOT NULL,
              size INTEGER NOT NULL, season_number INTEGER, episode_number INTEGER, episode_end INTEGER, resolution TEXT,
              codec TEXT, audio_languages TEXT DEFAULT '[]', modified_at TEXT, fingerprint TEXT UNIQUE, active INTEGER DEFAULT 1,
              FOREIGN KEY(media_item_id) REFERENCES media_items(id)
            );
            CREATE TABLE IF NOT EXISTS tmdb_cache (cache_key TEXT PRIMARY KEY, response TEXT NOT NULL, fetched_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS scan_jobs (id TEXT PRIMARY KEY, kind TEXT, status TEXT, progress TEXT, started_at TEXT, finished_at TEXT, error TEXT);
            CREATE TABLE IF NOT EXISTS manual_matches (media_item_id TEXT PRIMARY KEY, tmdb_id INTEGER NOT NULL, selected_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS admin_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, message TEXT NOT NULL,
              source_url TEXT DEFAULT '', detail TEXT DEFAULT '', created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_settings (
              key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS content_sources (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL UNIQUE,
              enabled INTEGER NOT NULL DEFAULT 1, priority INTEGER NOT NULL DEFAULT 100,
              parser_type TEXT NOT NULL DEFAULT 'compatible-search', config_json TEXT DEFAULT '{}', last_status TEXT DEFAULT 'unknown',
              last_checked_at TEXT, last_error TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_items_type_title ON media_items(type, normalized_title);
            CREATE INDEX IF NOT EXISTS idx_items_tmdb ON media_items(tmdb_id);
            CREATE INDEX IF NOT EXISTS idx_items_available ON media_items(available);
            CREATE INDEX IF NOT EXISTS idx_items_added ON media_items(date_added);
            CREATE INDEX IF NOT EXISTS idx_files_item_episode ON media_files(media_item_id, season_number, episode_number);
            CREATE INDEX IF NOT EXISTS idx_admin_events_category_created ON admin_events(category, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_content_sources_priority ON content_sources(enabled, priority);
            """)
            # Existing installations predate configurable authorized sources.
            # SQLite has no ADD COLUMN IF NOT EXISTS, so inspect first.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(content_sources)").fetchall()}
            if "config_json" not in columns:
                db.execute("ALTER TABLE content_sources ADD COLUMN config_json TEXT DEFAULT '{}'")

    def settings(self) -> dict[str, Any]:
        with self._connection() as db:
            rows = db.execute("SELECT key,value FROM app_settings").fetchall()
        return {row["key"]: _loads(row["value"], row["value"]) for row in rows}

    def update_settings(self, values: dict[str, Any]) -> None:
        allowed = {
            "jellyfin_url", "jellyfin_api_key", "jellyfin_library_ids", "path_mappings",
            "movie_paths", "tv_paths", "tmdb_api_key", "min_file_size_mb", "auto_sync_minutes", "admin_password_hash",
        }
        now = _now()
        with self._connection() as db:
            for key, value in values.items():
                if key not in allowed:
                    continue
                db.execute(
                    "INSERT INTO app_settings(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                    (key, _json(value), now),
                )

    def list_sources(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute("SELECT * FROM content_sources ORDER BY enabled DESC, priority, name").fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"]), "config": _loads(row["config_json"], {})} for row in rows]

    def save_source(self, source: dict[str, Any]) -> dict[str, Any]:
        source_id = str(source.get("id") or uuid.uuid4())
        name = str(source.get("name") or "").strip()
        base_url = str(source.get("base_url") or "").strip().rstrip("/")
        if not name or not base_url:
            raise ValueError("Source name and URL are required")
        now = _now()
        with self._connection() as db:
            db.execute(
                "INSERT INTO content_sources(id,name,base_url,enabled,priority,parser_type,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name,base_url=excluded.base_url,enabled=excluded.enabled,priority=excluded.priority,parser_type=excluded.parser_type,config_json=excluded.config_json,updated_at=excluded.updated_at",
                (source_id, name, base_url, int(bool(source.get("enabled", True))), int(source.get("priority") or 100), str(source.get("parser_type") or "compatible-search"), _json(source.get("config") or {}), now, now),
            )
        return next(item for item in self.list_sources() if item["id"] == source_id)

    def delete_source(self, source_id: str) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM content_sources WHERE id=?", (source_id,))

    def record_source_check(self, source_id: str, status: str, error: str = "") -> None:
        with self._connection() as db:
            db.execute("UPDATE content_sources SET last_status=?,last_checked_at=?,last_error=? WHERE id=?", (status, _now(), error[:500], source_id))

    @staticmethod
    def roots_for(media_type: str) -> list[Path]:
        names = ("MOVIES_PATHS", "MOVIES_PATH") if media_type == "movie" else ("TV_SHOWS_PATHS", "TV_SHOWS_PATH")
        raw = next((os.environ.get(name, "").strip() for name in names if os.environ.get(name, "").strip()), "")
        return [Path(item.strip()).expanduser().resolve() for item in raw.split(",") if item.strip()]

    def validate_roots(self) -> list[dict[str, str]]:
        rows = []
        for kind in ("movie", "tv"):
            roots = self.roots_for(kind)
            if not roots:
                rows.append({"type": kind, "error": f"No {kind} folders configured"})
            for root in roots:
                if not root.is_dir() or not os.access(root, os.R_OK):
                    rows.append({"type": kind, "error": "The media folder could not be accessed.", "root": root.name})
        return rows

    def _tmdb(self, path: str) -> dict[str, Any] | None:
        if not self.tmdb_key:
            return None
        cache_key = path
        now = time.time()
        with self._connection() as db:
            row = db.execute("SELECT response, fetched_at FROM tmdb_cache WHERE cache_key=?", (cache_key,)).fetchone()
        if row and now - row["fetched_at"] < 7 * 86400:
            return _loads(row["response"], None)
        url = f"https://api.themoviedb.org/3/{path}{'&' if '?' in path else '?'}api_key={quote_plus(self.tmdb_key)}&language={quote_plus(self.language)}"
        try:
            req = Request(url, headers={"user-agent": "FreemiumIndex/1.0"})
            with urlopen(req, timeout=12) as res:
                value = json.loads(res.read().decode())
            with self._connection() as db:
                db.execute("INSERT OR REPLACE INTO tmdb_cache(cache_key,response,fetched_at) VALUES(?,?,?)", (cache_key, _json(value), now))
            return value
        except Exception:
            return None

    def _match_tmdb(self, media_type: str, title: str, year: int | None) -> dict[str, Any] | None:
        if not self.tmdb_key:
            return None
        endpoint = "movie" if media_type == "movie" else "tv"
        path = f"search/{endpoint}?query={quote_plus(title)}&include_adult=false"
        if year:
            path += f"&{'year' if media_type == 'movie' else 'first_air_date_year'}={year}"
        data = self._tmdb(path) or {}
        results = data.get("results") or []
        if not results:
            return None
        target = normalized_title(title)
        best, score = None, 0.0
        for result in results[:10]:
            names = (result.get("title", ""), result.get("original_title", ""), result.get("name", ""), result.get("original_name", ""))
            exact = any(normalized_title(name) == target for name in names if name)
            result_year = _safe_int((result.get("release_date") or result.get("first_air_date") or "")[:4])
            candidate_score = 0.72 if exact else 0.45
            if year and result_year == year: candidate_score += 0.22
            if result.get("poster_path"): candidate_score += 0.04
            if candidate_score > score: best, score = result, candidate_score
        if not best:
            return None
        detail = self._tmdb(f"{endpoint}/{best['id']}") or best
        detail["_confidence"] = round(min(score, .99), 2)
        return detail

    def start_scan(self, kind: str = "full", item_id: str | None = None, refresh_metadata: bool = False) -> str:
        if not self._scan_lock.acquire(blocking=False):
            active = next((job_id for job_id, job in self._jobs.items() if job.get("status") == "running"), "")
            raise RuntimeError(f"A library scan is already running ({active}).")
        job_id = str(uuid.uuid4())
        self._jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "startedAt": _now(), "progress": {"percentage": 0, "foldersChecked": 0, "filesChecked": 0, "newItems": 0, "updatedItems": 0, "removedItems": 0, "matchingItems": 0, "errors": []}}
        with self._connection() as db:
            db.execute("INSERT INTO scan_jobs(id,kind,status,progress,started_at) VALUES(?,?,?,?,?)", (job_id, kind, "running", _json(self._jobs[job_id]["progress"]), self._jobs[job_id]["startedAt"]))
        threading.Thread(target=self._scan_worker, args=(job_id, kind, item_id, refresh_metadata), daemon=True).start()
        return job_id

    def _scan_worker(self, job_id: str, kind: str, item_id: str | None, refresh_metadata: bool) -> None:
        job = self._jobs[job_id]
        progress = job["progress"]
        try:
            if item_id:
                # The action exposed in the detail drawer must refresh local
                # availability too.  Metadata-only refreshes leave newly
                # uploaded episodes yellow until somebody runs a full scan.
                self._rescan_item_files(item_id, progress)
                with self._connection() as db:
                    row = db.execute("SELECT * FROM media_items WHERE id=?", (item_id,)).fetchone()
                if row:
                    self._refresh_item_metadata(dict(row), force=True)
                    progress["updatedItems"] += 1
            elif refresh_metadata:
                with self._connection() as db:
                    rows = [dict(row) for row in db.execute("SELECT * FROM media_items WHERE ignored=0")]
                for index, row in enumerate(rows):
                    self._refresh_item_metadata(row, force=True)
                    progress["matchingItems"] += 1
                    progress["percentage"] = int((index + 1) * 100 / max(len(rows), 1))
            else:
                all_seen: set[str] = set()
                roots = [("movie", root) for root in self.roots_for("movie")] + [("tv", root) for root in self.roots_for("tv")]
                for root_index, (media_type, root) in enumerate(roots):
                    if not root.is_dir() or not os.access(root, os.R_OK):
                        progress["errors"].append(f"{media_type.title()} folder could not be accessed: {root.name}")
                        continue
                    progress["foldersChecked"] += 1
                    root_key = hashlib.sha1(str(root).encode()).hexdigest()[:16]
                    for path in root.rglob("*"):
                        if not path.is_file():
                            continue
                        item = parse_media_file(path, root, media_type)
                        if not item:
                            continue
                        progress["filesChecked"] += 1
                        item_key = self._upsert_file(media_type, root_key, item)
                        all_seen.add(item_key)
                        if progress["filesChecked"] % 20 == 0:
                            progress["percentage"] = min(95, 5 + root_index * 35)
                    # A root-specific stale pass avoids making another root unavailable.
                    with self._connection() as db:
                        stale = db.execute("SELECT id FROM media_items WHERE root_key=?", (root_key,)).fetchall()
                        for stale_row in stale:
                            if stale_row["id"] not in all_seen:
                                db.execute("UPDATE media_items SET available=0 WHERE id=?", (stale_row["id"],))
                                progress["removedItems"] += 1
                progress["percentage"] = 98
                with self._connection() as db:
                    rows = [dict(row) for row in db.execute("SELECT * FROM media_items WHERE tmdb_id IS NULL AND needs_match=0 AND ignored=0")]
                for row in rows:
                    self._refresh_item_metadata(row)
                    progress["matchingItems"] += 1
            progress["percentage"] = 100
            job["status"] = "completed"
        except Exception as exc:
            progress["errors"].append(str(exc))
            job["status"] = "completed_with_warnings"
        finally:
            job["finishedAt"] = _now()
            job["progress"] = progress
            with self._connection() as db:
                db.execute("UPDATE scan_jobs SET status=?, progress=?, finished_at=?, error=? WHERE id=?", (job["status"], _json(progress), job["finishedAt"], "\n".join(progress["errors"]), job_id))
            self._scan_lock.release()

    def _rescan_item_files(self, item_id: str, progress: dict[str, Any]) -> None:
        """Rescan only the configured library folder belonging to one item."""
        with self._connection() as db:
            item = db.execute("SELECT * FROM media_items WHERE id=?", (item_id,)).fetchone()
            sample = db.execute("SELECT relative_path FROM media_files WHERE media_item_id=? ORDER BY relative_path LIMIT 1", (item_id,)).fetchone()
        if not item:
            raise ValueError("Library item not found")

        roots = [root for root in self.roots_for(item["type"])
                 if hashlib.sha1(str(root).encode()).hexdigest()[:16] == item["root_key"]]
        if not roots:
            raise ValueError("Configured media folder for this item was not found")
        root = roots[0]
        relative = str(sample["relative_path"] if sample else item["relative_path"] or "")
        first_part = Path(relative).parts[0] if Path(relative).parts else ""
        item_dir = (root / first_part).resolve() if first_part else root
        if root not in item_dir.parents and item_dir != root:
            raise ValueError("Invalid library item path")
        if not item_dir.is_dir() or not os.access(item_dir, os.R_OK):
            raise ValueError("The media folder could not be accessed")

        # Mark old entries inactive first; every file discovered below turns
        # active again.  This also removes deleted episodes from green status.
        with self._connection() as db:
            db.execute("UPDATE media_files SET active=0 WHERE media_item_id=?", (item_id,))

        discovered = 0
        for path in item_dir.rglob("*"):
            if not path.is_file():
                continue
            parsed = parse_media_file(path, root, item["type"])
            if not parsed:
                continue
            if normalized_title(parsed["title"]) != item["normalized_title"]:
                continue
            self._upsert_file(item["type"], item["root_key"], parsed)
            discovered += 1
            progress["filesChecked"] += 1

        with self._connection() as db:
            self._recalculate_item(db, item_id)
            db.execute("UPDATE media_items SET last_scanned_at=? WHERE id=?", (_now(), item_id))
        progress["foldersChecked"] += 1
        if discovered:
            progress["updatedItems"] += 1

    def _upsert_file(self, media_type: str, root_key: str, item: dict[str, Any]) -> str:
        norm = normalized_title(item["title"])
        with self._connection() as db:
            row = db.execute("SELECT * FROM media_items WHERE type=? AND root_key=? AND normalized_title=? AND year IS ?", (media_type, root_key, norm, item["year"])).fetchone()
            # Do not collapse remakes or unrelated titles that share a name
            # (for example, "Alpha (2018)" and "Alpha (2026)").  An unyearled
            # legacy entry may be upgraded when a later scan finds its year,
            # but two explicit, different years are always separate items.
            if not row and item["year"] is not None:
                row = db.execute(
                    "SELECT * FROM media_items WHERE type=? AND root_key=? AND normalized_title=? AND year IS NULL ORDER BY last_scanned_at DESC LIMIT 1",
                    (media_type, root_key, norm),
                ).fetchone()
                if row:
                    db.execute("UPDATE media_items SET year=? WHERE id=?", (item["year"], row["id"]))
            elif not row:
                row = db.execute(
                    "SELECT * FROM media_items WHERE type=? AND root_key=? AND normalized_title=? AND year IS NULL ORDER BY last_scanned_at DESC LIMIT 1",
                    (media_type, root_key, norm),
                ).fetchone()
            if row:
                media_id = row["id"]
                db.execute("UPDATE media_items SET available=1,last_scanned_at=? WHERE id=?", (_now(), media_id))
            else:
                media_id = str(uuid.uuid4())
                db.execute("INSERT INTO media_items(id,type,title,normalized_title,year,root_key,relative_path,date_added,last_scanned_at) VALUES(?,?,?,?,?,?,?,?,?)", (media_id, media_type, item["title"], norm, item["year"], root_key, str(Path(item["relative_path"]).parent), _now(), _now()))
            file_row = db.execute("SELECT id FROM media_files WHERE fingerprint=?", (item["fingerprint"],)).fetchone()
            values = (media_id, item["relative_path"], item["filename"], item["size"], item["season"], item["episode"], item["episode_end"], item["resolution"], item["codec"], _json(item["languages"]), item["modified_at"], item["fingerprint"])
            if file_row:
                db.execute("UPDATE media_files SET active=1,media_item_id=?,relative_path=?,filename=?,size=?,season_number=?,episode_number=?,episode_end=?,resolution=?,codec=?,audio_languages=?,modified_at=? WHERE fingerprint=?", values[:-1] + (item["fingerprint"],))
            else:
                db.execute("INSERT INTO media_files(id,media_item_id,relative_path,filename,size,season_number,episode_number,episode_end,resolution,codec,audio_languages,modified_at,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()),) + values)
            self._recalculate_item(db, media_id)
        return media_id

    def _recalculate_item(self, db: sqlite3.Connection, media_id: str) -> None:
        rows = db.execute("SELECT size,resolution,audio_languages FROM media_files WHERE media_item_id=? AND active=1", (media_id,)).fetchall()
        langs = sorted({lang for row in rows for lang in _loads(row["audio_languages"], [])})
        qualities = sorted({row["resolution"] for row in rows if row["resolution"] and row["resolution"] != "Unknown"}, reverse=True)
        db.execute("UPDATE media_items SET total_files=?,total_size=?,local_languages=?,qualities=?,available=? WHERE id=?", (len(rows), sum(row["size"] for row in rows), _json(langs), _json(qualities), int(bool(rows)), media_id))

    def _refresh_item_metadata(self, item: dict[str, Any], force: bool = False) -> None:
        if item.get("tmdb_id") and not force:
            return
        match = self._match_tmdb(item["type"], item["title"], item.get("year"))
        with self._connection() as db:
            if not match:
                db.execute("UPDATE media_items SET needs_match=1,last_scanned_at=? WHERE id=?", (_now(), item["id"]))
                return
            name = match.get("title") or match.get("name") or item["title"]
            date = match.get("release_date") or match.get("first_air_date") or ""
            # Folder year is the local identity key.  A TMDB result may have
            # a different release year (festival/region dates), so never
            # overwrite it and accidentally collide with another local item.
            db.execute("UPDATE media_items SET title=?,tmdb_id=?,match_confidence=?,poster_path=?,backdrop_path=?,overview=?,genres=?,original_language=?,needs_match=0,metadata_json=?,last_scanned_at=? WHERE id=?", (name, match.get("id"), match.get("_confidence", 1), match.get("poster_path") or "", match.get("backdrop_path") or "", match.get("overview") or "", _json([g.get("name") for g in match.get("genres", [])]), match.get("original_language") or "", _json(match), _now(), item["id"]))

    def _item(self, row: sqlite3.Row, include_files: bool = False) -> dict[str, Any]:
        value = dict(row)
        for key in ("genres", "local_languages", "qualities", "metadata_json"):
            value[key.replace("_json", "")] = _loads(value.pop(key, None), {} if key == "metadata_json" else [])
        value["available"] = bool(value["available"])
        value["needsMatch"] = bool(value.pop("needs_match"))
        value["dateAdded"] = value.pop("date_added")
        value["lastScannedAt"] = value.pop("last_scanned_at")
        value["posterUrl"] = f"/api/tmdb-image?path=w342{value['poster_path']}" if value.get("poster_path") else ""
        value["backdropUrl"] = f"/api/tmdb-image?path=w1280{value['backdrop_path']}" if value.get("backdrop_path") else ""
        metadata = value.get("metadata", {})
        value["originalTitle"] = metadata.get("original_title") or metadata.get("original_name") or ""
        episode_runs = metadata.get("episode_run_time") or [None]
        value["runtime"] = metadata.get("runtime") or episode_runs[0]
        value["rating"] = metadata.get("vote_average")
        value["tmdbStatus"] = metadata.get("status") or ""
        if value["type"] == "tv":
            with self._connection() as db:
                season_rows = db.execute("SELECT DISTINCT season_number FROM media_files WHERE media_item_id=? AND active=1 AND season_number > 0", (value["id"],)).fetchall()
                episode_count = db.execute("SELECT count(DISTINCT season_number || ':' || episode_number) FROM media_files WHERE media_item_id=? AND active=1 AND season_number > 0 AND episode_number IS NOT NULL", (value["id"],)).fetchone()[0]
            seasons_meta = [s for s in metadata.get("seasons", []) if s.get("season_number", 0) > 0]
            value["availableSeasons"] = min(len(season_rows), len(seasons_meta)) if seasons_meta else len(season_rows)
            value["availableEpisodes"] = episode_count
            value["totalSeasons"] = len(seasons_meta) or None
            value["totalEpisodes"] = sum(int(s.get("episode_count") or 0) for s in seasons_meta) or None
            value["progress"] = min(100, round(100 * episode_count / value["totalEpisodes"])) if value["totalEpisodes"] else None
        if include_files:
            with self._connection() as db:
                files = db.execute("SELECT id,relative_path,filename,size,season_number,episode_number,episode_end,resolution,codec,audio_languages,modified_at FROM media_files WHERE media_item_id=? AND active=1 ORDER BY season_number,episode_number,filename", (value["id"],)).fetchall()
            value["files"] = [{**dict(f), "audioLanguages": _loads(f["audio_languages"], [])} for f in files]
        return value

    def list_items(self, media_type: str, params: dict[str, str]) -> dict[str, Any]:
        page, limit = max(1, int(params.get("page", "1"))), min(50, max(1, int(params.get("limit", "30"))))
        clauses, values = ["type=?", "ignored=0"], [media_type]
        if params.get("search"):
            clauses.append("(title LIKE ? OR normalized_title LIKE ?)"); values.extend([f"%{params['search']}%", f"%{normalized_title(params['search'])}%"])
        if params.get("year", "").isdigit(): clauses.append("year=?"); values.append(int(params["year"]))
        status = params.get("status", "")
        if status == "available": clauses.append("available=1")
        elif status == "missing": clauses.append("available=0")
        elif status == "unmatched": clauses.append("needs_match=1")
        quality = params.get("quality", "")
        if quality: clauses.append("qualities LIKE ?"); values.append(f"%{quality}%")
        language = params.get("language", "")
        if language: clauses.append("local_languages LIKE ?"); values.append(f"%{language}%")
        where = " AND ".join(clauses)
        sort_map = {"title": "title COLLATE NOCASE", "year": "year", "dateAdded": "date_added", "rating": "json_extract(metadata_json, '$.vote_average')", "fileSize": "total_size"}
        sort = sort_map.get(params.get("sort", "title"), "title COLLATE NOCASE")
        order = "DESC" if params.get("order", "asc").lower() == "desc" else "ASC"
        with self._connection() as db:
            total = db.execute(f"SELECT count(*) FROM media_items WHERE {where}", values).fetchone()[0]
            rows = db.execute(f"SELECT * FROM media_items WHERE {where} ORDER BY {sort} {order} LIMIT ? OFFSET ?", values + [limit, (page-1)*limit]).fetchall()
        return {"page": page, "limit": limit, "total": total, "totalPages": max(1, (total + limit - 1)//limit), "items": [self._item(row) for row in rows]}

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        with self._connection() as db:
            row = db.execute("SELECT * FROM media_items WHERE id=? AND ignored=0", (item_id,)).fetchone()
        return self._item(row, True) if row else None

    def seasons(self, item_id: str, season_number: int | None = None) -> list[dict[str, Any]]:
        item = self.get_item(item_id)
        if not item or item["type"] != "tv": return []
        meta = item.get("metadata", {})
        include_specials = os.environ.get("INCLUDE_SPECIALS", "false").lower() in {"1", "true", "yes"}
        expected = {int(s.get("season_number")): s for s in meta.get("seasons", []) if s.get("season_number") is not None and (include_specials or int(s.get("season_number")) > 0)}
        with self._connection() as db:
            rows = db.execute("SELECT * FROM media_files WHERE media_item_id=? AND active=1" + (" AND season_number=?" if season_number is not None else ""), (item_id,) if season_number is None else (item_id, season_number)).fetchall()
        grouped: dict[int, list[sqlite3.Row]] = defaultdict(list)
        for row in rows: grouped[int(row["season_number"] or 0)].append(row)
        output = []
        for number in sorted(number for number in (set(expected) | set(grouped)) if include_specials or number > 0):
            files = grouped[number]; season_meta = expected.get(number, {})
            actual_eps = {int(f["episode_number"]) for f in files if f["episode_number"] is not None}
            total = int(season_meta.get("episode_count") or len(actual_eps))
            file_by_episode = {int(f["episode_number"]): f for f in files if f["episode_number"] is not None}
            remote_episodes: list[dict[str, Any]] = []
            if item.get("tmdb_id") and number >= 0:
                season_data = self._tmdb(f"tv/{item['tmdb_id']}/season/{number}") or {}
                remote_episodes = season_data.get("episodes") or []
            episode_rows: list[dict[str, Any]] = []
            if remote_episodes:
                now_date = datetime.now(timezone.utc).date().isoformat()
                for remote in remote_episodes:
                    episode_no = _safe_int(remote.get("episode_number"))
                    if episode_no is None: continue
                    aired = str(remote.get("air_date") or "")
                    if aired and aired > now_date and os.environ.get("SHOW_UNAIRED_AS_MISSING", "false").lower() not in {"1", "true", "yes"}:
                        continue
                    local = file_by_episode.get(episode_no)
                    episode_rows.append({"episodeNumber": episode_no, "title": remote.get("name") or f"Episode {episode_no}", "overview": remote.get("overview") or "", "airDate": aired, "stillPath": remote.get("still_path") or "", "runtime": remote.get("runtime"), "quality": local["resolution"] if local else "", "languages": _loads(local["audio_languages"], []) if local else [], "fileSize": local["size"] if local else 0, "available": bool(local), "filename": local["filename"] if local else ""})
            else:
                episode_rows = [{"episodeNumber": f["episode_number"], "quality": f["resolution"], "languages": _loads(f["audio_languages"], []), "fileSize": f["size"], "available": True, "filename": f["filename"]} for f in files]
            output.append({"seasonNumber": number, "name": season_meta.get("name") or f"Season {number}", "totalEpisodes": total, "availableEpisodes": len(actual_eps), "status": "COMPLETE" if total and len(actual_eps) >= total else ("PARTIAL" if actual_eps else "MISSING"), "episodes": episode_rows})
        return output

    def stats(self) -> dict[str, Any]:
        with self._connection() as db:
            rows = db.execute("SELECT type,count(*) count,sum(total_size) total_size,sum(available) available FROM media_items WHERE ignored=0 GROUP BY type").fetchall()
            last = db.execute("SELECT finished_at FROM scan_jobs WHERE status LIKE 'completed%' ORDER BY finished_at DESC LIMIT 1").fetchone()
        stats = {r["type"]: {"count": r["count"], "available": r["available"] or 0, "totalSize": r["total_size"] or 0} for r in rows}
        return {"movies": stats.get("movie", {"count":0,"available":0,"totalSize":0}), "tv": stats.get("tv", {"count":0,"available":0,"totalSize":0}), "lastScan": last["finished_at"] if last else None, "configurationErrors": self.validate_roots()}

    def log_admin_event(self, category: str, message: str, source_url: str = "", detail: str = "") -> None:
        """Keep a short, server-side operational history for the admin view."""
        if category not in {"broken_link", "resolver_failure"}:
            return
        with self._connection() as db:
            db.execute(
                "INSERT INTO admin_events(category,message,source_url,detail,created_at) VALUES(?,?,?,?,?)",
                (category, message[:500], source_url[:2000], detail[:2000], _now()),
            )
            # This is diagnostic history, not an audit archive. Bound the DB growth.
            db.execute("DELETE FROM admin_events WHERE id NOT IN (SELECT id FROM admin_events ORDER BY id DESC LIMIT 500)")

    def admin_dashboard(self) -> dict[str, Any]:
        """Return operational data without exposing filesystem paths to the browser."""
        with self._connection() as db:
            events = [dict(row) for row in db.execute(
                "SELECT id,category,message,source_url,detail,created_at FROM admin_events ORDER BY id DESC LIMIT 30"
            ).fetchall()]
            scans = [dict(row) for row in db.execute(
                "SELECT id,kind,status,progress,started_at,finished_at,error FROM scan_jobs ORDER BY started_at DESC LIMIT 12"
            ).fetchall()]
            unmatched_rows = db.execute(
                "SELECT * FROM media_items WHERE needs_match=1 AND ignored=0 ORDER BY last_scanned_at DESC LIMIT 30"
            ).fetchall()
            duplicate_rows = db.execute("""
                SELECT type, normalized_title, year, count(*) copies, group_concat(id) ids
                FROM media_items
                WHERE ignored=0
                GROUP BY type, normalized_title, COALESCE(year, 0)
                HAVING count(DISTINCT root_key) > 1
                ORDER BY copies DESC, normalized_title COLLATE NOCASE
                LIMIT 30
            """).fetchall()
            duplicates = []
            for row in duplicate_rows:
                ids = str(row["ids"] or "").split(",")
                title_rows = db.execute(
                    "SELECT id,title,year,type,total_files,total_size FROM media_items WHERE id IN (%s)" % ",".join("?" * len(ids)), ids
                ).fetchall()
                duplicates.append({"type": row["type"], "title": title_rows[0]["title"] if title_rows else row["normalized_title"], "year": row["year"], "copies": row["copies"], "items": [dict(item) for item in title_rows]})
        for scan in scans:
            scan["progress"] = _loads(scan.get("progress"), {})
        return {
            "events": events,
            "scans": scans,
            "unmatched": [self._item(row) for row in unmatched_rows],
            "duplicates": duplicates,
            "stats": self.stats(),
        }

    def missing(self, kind: str) -> list[dict[str, Any]]:
        with self._connection() as db:
            if kind == "unmatched": rows = db.execute("SELECT * FROM media_items WHERE needs_match=1 AND ignored=0 ORDER BY title").fetchall()
            else: rows = db.execute("SELECT * FROM media_items WHERE available=0 AND ignored=0 ORDER BY title").fetchall()
        return [self._item(row) for row in rows]

    def recent(self, limit: int = 40) -> list[dict[str, Any]]:
        with self._connection() as db:
            rows = db.execute("SELECT DISTINCT i.* FROM media_items i JOIN media_files f ON f.media_item_id=i.id WHERE f.active=1 ORDER BY f.modified_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._item(row) for row in rows]

    def manual_match(self, item_id: str, tmdb_id: int) -> None:
        with self._connection() as db:
            item = db.execute("SELECT * FROM media_items WHERE id=?", (item_id,)).fetchone()
        if not item: raise ValueError("Library item not found")
        endpoint = "movie" if item["type"] == "movie" else "tv"
        detail = self._tmdb(f"{endpoint}/{tmdb_id}")
        if not detail: raise ValueError("Metadata is temporarily unavailable.")
        detail["_confidence"] = 1
        self._refresh_item_metadata({**dict(item), "tmdb_id": None}, force=True) if False else None
        with self._connection() as db:
            db.execute("INSERT OR REPLACE INTO manual_matches(media_item_id,tmdb_id,selected_at) VALUES(?,?,?)", (item_id, tmdb_id, _now()))
            db.execute("UPDATE media_items SET tmdb_id=?,match_confidence=1,poster_path=?,backdrop_path=?,overview=?,genres=?,original_language=?,needs_match=0,metadata_json=? WHERE id=?", (tmdb_id, detail.get("poster_path") or "", detail.get("backdrop_path") or "", detail.get("overview") or "", _json([g.get("name") for g in detail.get("genres", [])]), detail.get("original_language") or "", _json(detail), item_id))

    def search_tmdb(self, media_type: str, query: str) -> list[dict[str, Any]]:
        endpoint = "movie" if media_type == "movie" else "tv"
        result = self._tmdb(f"search/{endpoint}?query={quote_plus(query)}&include_adult=false") or {}
        return [{"id": r.get("id"), "title": r.get("title") or r.get("name"), "year": (r.get("release_date") or r.get("first_air_date") or "")[:4], "posterPath": r.get("poster_path") or ""} for r in (result.get("results") or [])[:10]]
