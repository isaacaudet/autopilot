"""SQLite data layer for Clipper.

Replaces scattered JSON files (queue/pending/*.json, history.json,
performance.json, learned_weights.json, game_stats.json, facecam_profiles.json,
releases/*.json) with a single queue/clipper.db database.

Thread-safe: uses one connection per thread via threading.local().
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_local = threading.local()

# ---------------------------------------------------------------------------
#  Connection management
# ---------------------------------------------------------------------------

_DB_FILENAME = "clipper.db"


def get_db(config: dict) -> sqlite3.Connection:
    """Return a thread-local SQLite connection, creating/migrating the DB on first call.

    On first creation of the database file, automatically imports all existing
    JSON data (history, clips, performance, etc.) so callers can switch to
    SQLite-only reads immediately.
    """
    conn = getattr(_local, "conn", None)
    db_path = getattr(_local, "db_path", None)
    expected = str(Path(config["_queue_dir"]) / _DB_FILENAME)

    if conn is not None and db_path == expected:
        return conn

    db_file = Path(expected)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    is_new = not db_file.exists()

    conn = sqlite3.connect(expected, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    _ensure_schema(conn)

    _local.conn = conn
    _local.db_path = expected

    # Auto-migrate JSON data on first DB creation
    if is_new:
        try:
            counts = migrate_json(config)
            logger.info("Auto-migrated JSON → SQLite: %s", counts)
        except Exception as e:
            logger.warning("Auto-migration failed (non-fatal): %s", e)

    return conn


def close_db() -> None:
    """Close the thread-local connection if open."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        _local.db_path = None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables if they don't exist, and apply incremental migrations."""
    conn.executescript(_SCHEMA)
    _apply_migrations(conn)


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Add columns that may be missing from older schemas."""
    columns = {r[1] for r in conn.execute("PRAGMA table_info(performance)").fetchall()}
    if "retention_curve" not in columns:
        conn.execute("ALTER TABLE performance ADD COLUMN retention_curve TEXT")
        conn.commit()

    clip_columns = {r[1] for r in conn.execute("PRAGMA table_info(clips)").fetchall()}
    if "hook_duration" not in clip_columns:
        conn.execute("ALTER TABLE clips ADD COLUMN hook_duration REAL")
        conn.execute("ALTER TABLE clips ADD COLUMN hook_text_override TEXT")
        conn.commit()

    # Multi-platform upload IDs
    for col in ("tiktok_id", "instagram_id", "facebook_id"):
        if col not in clip_columns:
            conn.execute(f"ALTER TABLE clips ADD COLUMN {col} TEXT")
    conn.commit()

    rel_cols = {r[1] for r in conn.execute("PRAGMA table_info(releases)").fetchall()}
    if "platform" not in rel_cols:
        conn.execute("ALTER TABLE releases ADD COLUMN platform TEXT DEFAULT 'youtube'")
        conn.commit()


_SCHEMA = """\
CREATE TABLE IF NOT EXISTS clips (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    title TEXT,
    url TEXT,
    duration REAL,
    view_count INTEGER DEFAULT 0,
    streamer TEXT,
    game TEXT,
    platform TEXT DEFAULT 'twitch',
    thumbnail_url TEXT,
    language TEXT,
    created_at TEXT,
    fetched_at TEXT DEFAULT (datetime('now')),
    channel TEXT,
    is_shorts INTEGER DEFAULT 0,
    shorts_layout TEXT,
    score REAL,
    processed_path TEXT,
    subtitle_path TEXT,
    source_path TEXT,
    output_name TEXT,
    analysis TEXT,
    audio_energy_db REAL,
    video_id TEXT,
    privacy TEXT,
    title_override TEXT,
    description_override TEXT,
    tags_override TEXT,
    hook_duration REAL,
    hook_text_override TEXT,
    meta_json TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);
CREATE INDEX IF NOT EXISTS idx_clips_game ON clips(game);
CREATE INDEX IF NOT EXISTS idx_clips_channel ON clips(channel);
CREATE INDEX IF NOT EXISTS idx_clips_video_id ON clips(video_id);

CREATE TABLE IF NOT EXISTS history (
    clip_id TEXT PRIMARY KEY,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS performance (
    clip_id TEXT PRIMARY KEY,
    collected_at TEXT,
    features TEXT NOT NULL,
    youtube TEXT NOT NULL,
    retention_curve TEXT
);

CREATE TABLE IF NOT EXISTS scoring_weights (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    updated_at TEXT,
    sample_size INTEGER,
    weights TEXT NOT NULL,
    correlations TEXT
);

CREATE TABLE IF NOT EXISTS game_stats (
    game TEXT PRIMARY KEY,
    uploads INTEGER,
    avg_views REAL,
    success_rate REAL,
    multiplier REAL DEFAULT 1.0,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS facecam_profiles (
    streamer TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    video_id TEXT,
    privacy TEXT DEFAULT 'unlisted',
    meta_path TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_releases_status ON releases(status);
CREATE INDEX IF NOT EXISTS idx_releases_scheduled ON releases(scheduled_at);
"""


# ---------------------------------------------------------------------------
#  History (deduplication)
# ---------------------------------------------------------------------------


def is_clip_seen(config: dict, clip_id: str) -> bool:
    conn = get_db(config)
    row = conn.execute("SELECT 1 FROM history WHERE clip_id = ?", (clip_id,)).fetchone()
    return row is not None


def mark_clips_seen(config: dict, clip_ids: list[str]) -> None:
    if not clip_ids:
        return
    conn = get_db(config)
    conn.executemany(
        "INSERT OR IGNORE INTO history (clip_id) VALUES (?)",
        [(cid,) for cid in clip_ids],
    )
    conn.commit()


def all_seen_ids(config: dict) -> set[str]:
    conn = get_db(config)
    rows = conn.execute("SELECT clip_id FROM history").fetchall()
    return {r["clip_id"] for r in rows}


# ---------------------------------------------------------------------------
#  Clips
# ---------------------------------------------------------------------------

# Fields stored as direct columns (everything else goes into meta_json)
_CLIP_COLUMNS = {
    "id", "status", "title", "url", "duration", "view_count", "streamer",
    "game", "platform", "thumbnail_url", "language", "created_at", "fetched_at",
    "channel", "is_shorts", "shorts_layout", "score", "processed_path",
    "subtitle_path", "source_path", "output_name", "analysis",
    "audio_energy_db", "video_id", "privacy", "title_override",
    "description_override", "tags_override", "hook_duration",
    "hook_text_override", "tiktok_id", "instagram_id", "facebook_id",
    "meta_json", "updated_at",
}

# Mapping from clip dict underscore-prefixed keys to column names
_KEY_REMAP = {
    "_target_channel": "channel",
    "_score": "score",
    "_shorts_layout": "shorts_layout",
    "_subtitle_path": "subtitle_path",
    "_source_path": "source_path",
    "_audio_energy_db": "audio_energy_db",
    "_title_override": "title_override",
    "_description_override": "description_override",
    "_tags_override": "tags_override",
    "_hook_duration": "hook_duration",
    "_hook_text_override": "hook_text_override",
    "_analysis": "analysis",
    "_tiktok_id": "tiktok_id",
    "_instagram_id": "instagram_id",
    "_facebook_id": "facebook_id",
}


def _clip_to_row(clip: dict) -> dict:
    """Convert a clip dict (with underscore-prefixed keys) to a flat column dict."""
    row: dict = {}
    extra: dict = {}

    for k, v in clip.items():
        # Remap underscore keys
        col = _KEY_REMAP.get(k, k)

        if col in _CLIP_COLUMNS:
            # JSON-encode complex values
            if col in ("analysis", "tags_override") and not isinstance(v, str):
                row[col] = json.dumps(v) if v is not None else None
            elif col == "is_shorts":
                row[col] = 1 if v else 0
            else:
                row[col] = v
        elif not k.startswith("_"):
            extra[k] = v

    # Stash extra fields in meta_json
    if extra:
        row["meta_json"] = json.dumps(extra)

    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    return row


def _row_to_clip(row: sqlite3.Row) -> dict:
    """Convert a DB row back to a clip dict compatible with existing code."""
    clip = dict(row)

    # Decode JSON blobs
    for key in ("analysis", "tags_override"):
        val = clip.get(key)
        if isinstance(val, str):
            try:
                clip[key] = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass

    # Merge meta_json back into clip
    meta = clip.pop("meta_json", None)
    if meta:
        try:
            clip.update(json.loads(meta))
        except (json.JSONDecodeError, ValueError):
            pass

    # Re-add underscore-prefixed aliases for backward compat
    if clip.get("channel"):
        clip["_target_channel"] = clip["channel"]
    if clip.get("score") is not None:
        clip["_score"] = clip["score"]
    if clip.get("shorts_layout"):
        clip["_shorts_layout"] = clip["shorts_layout"]
    if clip.get("subtitle_path"):
        clip["_subtitle_path"] = clip["subtitle_path"]
    if clip.get("source_path"):
        clip["_source_path"] = clip["source_path"]
    if clip.get("audio_energy_db") is not None:
        clip["_audio_energy_db"] = clip["audio_energy_db"]
    if clip.get("title_override"):
        clip["_title_override"] = clip["title_override"]
    if clip.get("description_override"):
        clip["_description_override"] = clip["description_override"]
    if clip.get("tags_override"):
        clip["_tags_override"] = clip["tags_override"]
    if clip.get("analysis"):
        raw_analysis = clip["analysis"]
        if isinstance(raw_analysis, str):
            try:
                raw_analysis = json.loads(raw_analysis)
            except (json.JSONDecodeError, ValueError):
                pass
        clip["_analysis"] = raw_analysis
    if clip.get("hook_duration") is not None:
        clip["_hook_duration"] = clip["hook_duration"]
    if clip.get("hook_text_override"):
        clip["_hook_text_override"] = clip["hook_text_override"]
    if clip.get("tiktok_id"):
        clip["_tiktok_id"] = clip["tiktok_id"]
    if clip.get("instagram_id"):
        clip["_instagram_id"] = clip["instagram_id"]
    if clip.get("facebook_id"):
        clip["_facebook_id"] = clip["facebook_id"]

    # Convert is_shorts back to bool
    clip["is_shorts"] = bool(clip.get("is_shorts"))

    # Remove None values for cleaner dicts
    return {k: v for k, v in clip.items() if v is not None}


def save_clip(config: dict, clip: dict, status: str = "pending") -> None:
    """Insert or update a clip in the database."""
    row = _clip_to_row(clip)
    row.setdefault("status", status)
    row.setdefault("id", clip.get("id"))

    if not row.get("id"):
        raise ValueError("Clip must have an 'id' field")

    conn = get_db(config)
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "id")

    sql = f"INSERT INTO clips ({', '.join(cols)}) VALUES ({placeholders}) ON CONFLICT(id) DO UPDATE SET {updates}"
    conn.execute(sql, [row[c] for c in cols])
    conn.commit()


def save_clips_batch(config: dict, clips: list[dict], status: str = "pending") -> int:
    """Bulk insert clips. Returns number of new clips inserted (not updated)."""
    if not clips:
        return 0

    conn = get_db(config)
    seen = all_seen_ids(config)
    inserted = 0

    for clip in clips:
        cid = clip.get("id")
        if not cid or cid in seen:
            continue

        row = _clip_to_row(clip)
        row.setdefault("status", status)
        row.setdefault("id", cid)

        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)

        try:
            conn.execute(
                f"INSERT OR IGNORE INTO clips ({', '.join(cols)}) VALUES ({placeholders})",
                [row[c] for c in cols],
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    mark_clips_seen(config, [c["id"] for c in clips if c.get("id")])
    return inserted


def get_clip(config: dict, clip_id: str) -> dict | None:
    conn = get_db(config)
    row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return _row_to_clip(row) if row else None


def list_clips(
    config: dict,
    status: str | None = None,
    game: str | None = None,
    channel: str | None = None,
    streamer: str | None = None,
    sort: str = "fetched_at",
    limit: int = 500,
    has_video_id: bool | None = None,
    exclude_compilations: bool = False,
) -> list[dict]:
    """Query clips with optional filters."""
    conn = get_db(config)
    conditions = []
    params: list = []

    if status:
        conditions.append("status = ?")
        params.append(status)
    if game:
        conditions.append("LOWER(game) LIKE ?")
        params.append(f"%{game.lower()}%")
    if channel:
        conditions.append("channel = ?")
        params.append(channel)
    if streamer:
        conditions.append("LOWER(streamer) LIKE ?")
        params.append(f"%{streamer.lower()}%")
    if has_video_id is True:
        conditions.append("video_id IS NOT NULL AND video_id != ''")
    elif has_video_id is False:
        conditions.append("(video_id IS NULL OR video_id = '')")
    if exclude_compilations:
        conditions.append("id NOT LIKE 'compilation_%'")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    sort_map = {
        "fetched_at": "fetched_at DESC",
        "recent": "updated_at DESC",
        "score": "score DESC",
        "views": "view_count DESC",
        "duration": "duration DESC",
        "title": "title ASC",
    }
    order = sort_map.get(sort, "fetched_at DESC")

    rows = conn.execute(
        f"SELECT * FROM clips {where} ORDER BY {order} LIMIT ?",
        params + [limit],
    ).fetchall()
    return [_row_to_clip(r) for r in rows]


def update_clip(config: dict, clip_id: str, **fields) -> bool:
    """Update specific fields on a clip. Returns True if a row was updated."""
    if not fields:
        return False

    conn = get_db(config)

    # Remap underscore keys
    remapped = {}
    extra: dict = {}
    for k, v in fields.items():
        col = _KEY_REMAP.get(k, k)
        if col in _CLIP_COLUMNS:
            if col in ("analysis", "tags_override") and not isinstance(v, str):
                remapped[col] = json.dumps(v) if v is not None else None
            elif col == "is_shorts":
                remapped[col] = 1 if v else 0
            else:
                remapped[col] = v
        else:
            extra[k] = v

    # Merge unknown keys into meta_json so per-clip extras (e.g. _layout_override)
    # survive round-trips through SQLite.
    if extra:
        existing_meta: dict = {}
        row = conn.execute("SELECT meta_json FROM clips WHERE id = ?", (clip_id,)).fetchone()
        raw_meta = row["meta_json"] if row else None
        if isinstance(raw_meta, str) and raw_meta.strip():
            try:
                parsed = json.loads(raw_meta)
                if isinstance(parsed, dict):
                    existing_meta = parsed
            except (json.JSONDecodeError, ValueError):
                existing_meta = {}

        for k, v in extra.items():
            if v is None:
                existing_meta.pop(k, None)
            else:
                existing_meta[k] = v
        remapped["meta_json"] = json.dumps(existing_meta) if existing_meta else None

    if not remapped:
        return False

    remapped["updated_at"] = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k} = ?" for k in remapped)
    result = conn.execute(
        f"UPDATE clips SET {sets} WHERE id = ?",
        list(remapped.values()) + [clip_id],
    )
    conn.commit()
    return result.rowcount > 0


def count_clips(config: dict, status: str | None = None) -> int:
    conn = get_db(config)
    if status:
        row = conn.execute("SELECT COUNT(*) FROM clips WHERE status = ?", (status,)).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM clips").fetchone()
    return row[0] if row else 0


def delete_clip(config: dict, clip_id: str) -> bool:
    conn = get_db(config)
    result = conn.execute("DELETE FROM clips WHERE id = ?", (clip_id,))
    conn.commit()
    return result.rowcount > 0


# ---------------------------------------------------------------------------
#  Performance log
# ---------------------------------------------------------------------------


def save_performance(
    config: dict, clip_id: str, collected_at: str, features: dict, youtube: dict,
    retention_curve: list[float] | None = None,
) -> None:
    conn = get_db(config)
    conn.execute(
        "INSERT OR REPLACE INTO performance (clip_id, collected_at, features, youtube, retention_curve) "
        "VALUES (?, ?, ?, ?, ?)",
        (clip_id, collected_at, json.dumps(features), json.dumps(youtube),
         json.dumps(retention_curve) if retention_curve else None),
    )
    conn.commit()


def list_performance(config: dict) -> list[dict]:
    conn = get_db(config)
    rows = conn.execute("SELECT * FROM performance ORDER BY collected_at DESC").fetchall()
    result = []
    for r in rows:
        entry = {
            "clip_id": r["clip_id"],
            "collected_at": r["collected_at"],
            "features": json.loads(r["features"]),
            "youtube": json.loads(r["youtube"]),
        }
        rc = r["retention_curve"]
        if rc:
            entry["retention_curve"] = json.loads(rc)
        result.append(entry)
    return result


def performance_ids(config: dict) -> set[str]:
    conn = get_db(config)
    rows = conn.execute("SELECT clip_id FROM performance").fetchall()
    return {r["clip_id"] for r in rows}


# ---------------------------------------------------------------------------
#  Scoring weights
# ---------------------------------------------------------------------------


def save_weights(config: dict, weights: dict, correlations: dict, sample_size: int) -> None:
    conn = get_db(config)
    conn.execute(
        "INSERT OR REPLACE INTO scoring_weights (id, updated_at, sample_size, weights, correlations) VALUES (1, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), sample_size, json.dumps(weights), json.dumps(correlations)),
    )
    conn.commit()


def get_weights(config: dict) -> dict | None:
    """Load learned weights. Returns {weights, correlations, sample_size, updated_at} or None."""
    conn = get_db(config)
    row = conn.execute("SELECT * FROM scoring_weights WHERE id = 1").fetchone()
    if not row:
        return None
    return {
        "updated_at": row["updated_at"],
        "sample_size": row["sample_size"],
        "weights": json.loads(row["weights"]),
        "correlations": json.loads(row["correlations"]) if row["correlations"] else {},
    }


# ---------------------------------------------------------------------------
#  Game stats
# ---------------------------------------------------------------------------


def save_game_stats(config: dict, games: dict[str, dict]) -> None:
    """Bulk upsert game stats."""
    conn = get_db(config)
    now = datetime.now(timezone.utc).isoformat()
    for game, stats in games.items():
        conn.execute(
            "INSERT OR REPLACE INTO game_stats (game, uploads, avg_views, success_rate, multiplier, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (game, stats.get("uploads", 0), stats.get("avg_views", 0), stats.get("success_rate", 0),
             stats.get("multiplier", 1.0), now),
        )
    conn.commit()


def get_game_stats(config: dict) -> dict[str, dict]:
    conn = get_db(config)
    rows = conn.execute("SELECT * FROM game_stats").fetchall()
    return {
        r["game"]: {
            "uploads": r["uploads"],
            "avg_views": r["avg_views"],
            "success_rate": r["success_rate"],
            "multiplier": r["multiplier"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    }


def get_game_multiplier_db(config: dict, game: str) -> float:
    """Get a game's scoring multiplier. Returns 1.0 if not found."""
    conn = get_db(config)
    # Exact match
    row = conn.execute("SELECT multiplier FROM game_stats WHERE game = ?", (game,)).fetchone()
    if row:
        return row["multiplier"]
    # Case-insensitive
    row = conn.execute("SELECT multiplier FROM game_stats WHERE LOWER(game) = LOWER(?)", (game,)).fetchone()
    if row:
        return row["multiplier"]
    return 1.0


# ---------------------------------------------------------------------------
#  Facecam / layout profiles
# ---------------------------------------------------------------------------


def save_facecam_profile(config: dict, streamer: str, profile: dict) -> None:
    conn = get_db(config)
    conn.execute(
        "INSERT OR REPLACE INTO facecam_profiles (streamer, profile, updated_at) VALUES (?, ?, ?)",
        (streamer.strip().lower(), json.dumps(profile), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def get_facecam_profile(config: dict, streamer: str) -> dict | None:
    conn = get_db(config)
    row = conn.execute(
        "SELECT profile FROM facecam_profiles WHERE streamer = ?",
        (streamer.strip().lower(),),
    ).fetchone()
    if row:
        return json.loads(row["profile"])
    return None


def list_facecam_profiles(config: dict) -> dict[str, dict]:
    conn = get_db(config)
    rows = conn.execute("SELECT streamer, profile FROM facecam_profiles").fetchall()
    return {r["streamer"]: json.loads(r["profile"]) for r in rows}


def delete_facecam_profile_db(config: dict, streamer: str) -> bool:
    conn = get_db(config)
    result = conn.execute(
        "DELETE FROM facecam_profiles WHERE streamer = ?",
        (streamer.strip().lower(),),
    )
    conn.commit()
    return result.rowcount > 0


# ---------------------------------------------------------------------------
#  Releases
# ---------------------------------------------------------------------------


def create_release(config: dict, clip_id: str, channel: str, scheduled_at: str,
                   privacy: str = "unlisted", meta_path: str | None = None,
                   platform: str = "youtube") -> int:
    conn = get_db(config)
    cursor = conn.execute(
        "INSERT INTO releases (clip_id, channel, scheduled_at, privacy, meta_path, platform) VALUES (?, ?, ?, ?, ?, ?)",
        (clip_id, channel, scheduled_at, privacy, meta_path, platform),
    )
    conn.commit()
    return cursor.lastrowid


def list_releases(config: dict, channel: str | None = None, status: str | None = None) -> list[dict]:
    conn = get_db(config)
    conditions = []
    params: list = []
    if channel:
        conditions.append("channel = ?")
        params.append(channel)
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = conn.execute(
        f"SELECT * FROM releases {where} ORDER BY scheduled_at ASC",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def update_release(config: dict, release_id: int, **fields) -> bool:
    if not fields:
        return False
    conn = get_db(config)
    sets = ", ".join(f"{k} = ?" for k in fields)
    result = conn.execute(
        f"UPDATE releases SET {sets} WHERE id = ?",
        list(fields.values()) + [release_id],
    )
    conn.commit()
    return result.rowcount > 0


def pending_releases_due(config: dict) -> list[dict]:
    """Get releases that are due (scheduled_at <= now) and still pending or uploaded."""
    conn = get_db(config)
    now = datetime.now().isoformat()
    rows = conn.execute(
        "SELECT * FROM releases WHERE scheduled_at <= ? AND status IN ('pending', 'uploaded') ORDER BY scheduled_at",
        (now,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
#  Migration: JSON files → SQLite
# ---------------------------------------------------------------------------


def migrate_json(config: dict) -> dict[str, int]:
    """One-time import of existing JSON data into SQLite.

    Idempotent: uses INSERT OR IGNORE, safe to run multiple times.
    Returns counts of rows imported per table.
    """
    counts = {"history": 0, "clips": 0, "performance": 0, "weights": 0,
              "game_stats": 0, "facecam_profiles": 0, "releases": 0}

    queue_dir = Path(config["_queue_dir"])
    output_dir = Path(config["_output_dir"])
    conn = get_db(config)

    # 1. History
    history_path = queue_dir / "history.json"
    if history_path.exists():
        try:
            with open(history_path) as f:
                ids = json.load(f)
            conn.executemany(
                "INSERT OR IGNORE INTO history (clip_id) VALUES (?)",
                [(cid,) for cid in ids],
            )
            conn.commit()
            counts["history"] = len(ids)
        except Exception as e:
            logger.warning("history.json migration failed: %s", e)

    # 2. Clips from queue directories
    status_dirs = {
        "pending": queue_dir / "pending",
        "approved": queue_dir / "approved",
        "skipped": queue_dir / "skipped",
    }
    for status, d in status_dirs.items():
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            try:
                clip = json.loads(p.read_text())
                clip.setdefault("id", p.stem)
                row = _clip_to_row(clip)
                row["status"] = status
                row.setdefault("id", clip["id"])

                cols = list(row.keys())
                placeholders = ", ".join("?" for _ in cols)
                conn.execute(
                    f"INSERT OR IGNORE INTO clips ({', '.join(cols)}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
                counts["clips"] += 1
            except Exception as e:
                logger.debug("Skipping clip %s: %s", p.name, e)

    # 3. Output clips
    if output_dir.exists():
        for p in output_dir.glob("*.json"):
            try:
                clip = json.loads(p.read_text())
                clip.setdefault("id", p.stem)
                row = _clip_to_row(clip)
                row["status"] = "output"
                row.setdefault("id", clip["id"])

                cols = list(row.keys())
                placeholders = ", ".join("?" for _ in cols)
                conn.execute(
                    f"INSERT OR IGNORE INTO clips ({', '.join(cols)}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )
                counts["clips"] += 1
            except Exception as e:
                logger.debug("Skipping output %s: %s", p.name, e)

    conn.commit()

    # 4. Performance
    perf_path = queue_dir / "performance.json"
    if perf_path.exists():
        try:
            with open(perf_path) as f:
                perf_data = json.load(f)
            for entry in perf_data:
                conn.execute(
                    "INSERT OR IGNORE INTO performance (clip_id, collected_at, features, youtube) VALUES (?, ?, ?, ?)",
                    (entry["clip_id"], entry.get("collected_at", ""),
                     json.dumps(entry.get("features", {})), json.dumps(entry.get("youtube", {}))),
                )
                counts["performance"] += 1
            conn.commit()
        except Exception as e:
            logger.warning("performance.json migration failed: %s", e)

    # 5. Weights
    weights_path = queue_dir / "learned_weights.json"
    if weights_path.exists():
        try:
            with open(weights_path) as f:
                w = json.load(f)
            conn.execute(
                "INSERT OR IGNORE INTO scoring_weights (id, updated_at, sample_size, weights, correlations) VALUES (1, ?, ?, ?, ?)",
                (w.get("updated_at", ""), w.get("sample_size", 0),
                 json.dumps(w.get("weights", {})), json.dumps(w.get("correlations", {}))),
            )
            conn.commit()
            counts["weights"] = 1
        except Exception as e:
            logger.warning("learned_weights.json migration failed: %s", e)

    # 6. Game stats
    stats_path = queue_dir / "game_stats.json"
    if stats_path.exists():
        try:
            with open(stats_path) as f:
                gs = json.load(f)
            updated = gs.get("updated_at", "")
            for game, stats in gs.get("games", {}).items():
                conn.execute(
                    "INSERT OR IGNORE INTO game_stats (game, uploads, avg_views, success_rate, multiplier, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (game, stats.get("uploads", 0), stats.get("avg_views", 0),
                     stats.get("success_rate", 0), stats.get("multiplier", 1.0), updated),
                )
                counts["game_stats"] += 1
            conn.commit()
        except Exception as e:
            logger.warning("game_stats.json migration failed: %s", e)

    # 7. Facecam profiles
    profiles_path = queue_dir / "facecam_profiles.json"
    if profiles_path.exists():
        try:
            with open(profiles_path) as f:
                fp = json.load(f)
            raw = fp.get("profiles", fp) if isinstance(fp, dict) else {}
            updated = fp.get("updated_at", "") if isinstance(fp, dict) else ""
            for streamer, profile in raw.items():
                if streamer == "updated_at":
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO facecam_profiles (streamer, profile, updated_at) VALUES (?, ?, ?)",
                    (streamer.strip().lower(), json.dumps(profile), updated),
                )
                counts["facecam_profiles"] += 1
            conn.commit()
        except Exception as e:
            logger.warning("facecam_profiles.json migration failed: %s", e)

    # 8. Releases
    releases_dir = queue_dir / "releases"
    if releases_dir.exists():
        for p in releases_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
                conn.execute(
                    "INSERT OR IGNORE INTO releases (clip_id, channel, scheduled_at, status, video_id, privacy, meta_path) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (data.get("clip_id", ""), data.get("channel", ""),
                     data.get("scheduled_at", ""), data.get("status", "pending"),
                     data.get("video_id"), data.get("privacy", "unlisted"),
                     data.get("meta_path")),
                )
                counts["releases"] += 1
            except Exception as e:
                logger.debug("Skipping release %s: %s", p.name, e)
        conn.commit()

    logger.info("Migration complete: %s", counts)
    return counts
