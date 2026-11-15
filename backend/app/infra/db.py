from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from app.infra.clock import iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    endpoint TEXT NOT NULL DEFAULT '',
    bucket TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'input',
    is_platform_storage INTEGER NOT NULL DEFAULT 0,
    access_key TEXT,
    secret_key TEXT,
    last_probe_status TEXT NOT NULL DEFAULT 'unconfigured',
    last_probe_message TEXT NOT NULL DEFAULT '尚未探测',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    object_key TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'other',
    vectorize INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'registered',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_id, object_key),
    FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    source_id TEXT,
    asset_id TEXT,
    stage TEXT NOT NULL DEFAULT 'queued',
    step TEXT NOT NULL DEFAULT '',
    progress REAL NOT NULL DEFAULT 0,
    queue_position INTEGER,
    error_code TEXT,
    error_message TEXT,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    payload_json TEXT NOT NULL DEFAULT '{}',
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runtime_flags (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS frames (
    asset_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    frame_index INTEGER NOT NULL,
    time_seconds REAL NOT NULL,
    kept INTEGER NOT NULL,
    drop_reason TEXT,
    preview_key TEXT,
    model_input_key TEXT,
    extra_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (asset_id, frame_index),
    FOREIGN KEY(asset_id) REFERENCES assets(id)
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS subtitle_associations (
    id TEXT PRIMARY KEY,
    video_asset_id TEXT NOT NULL,
    subtitle_asset_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(video_asset_id, subtitle_asset_id),
    FOREIGN KEY(video_asset_id) REFERENCES assets(id),
    FOREIGN KEY(subtitle_asset_id) REFERENCES assets(id)
);

CREATE TABLE IF NOT EXISTS subtitle_cues (
    association_id TEXT NOT NULL,
    cue_index INTEGER NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    text TEXT NOT NULL,
    PRIMARY KEY (association_id, cue_index),
    FOREIGN KEY(association_id) REFERENCES subtitle_associations(id)
);

CREATE TABLE IF NOT EXISTS subtitle_groups (
    association_id TEXT NOT NULL,
    group_index INTEGER NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    text TEXT NOT NULL,
    cue_indexes_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (association_id, group_index),
    FOREIGN KEY(association_id) REFERENCES subtitle_associations(id)
);

CREATE TABLE IF NOT EXISTS exclusions (
    source_id TEXT NOT NULL,
    object_key TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    restored_at TEXT,
    PRIMARY KEY (source_id, object_key)
);

CREATE TABLE IF NOT EXISTS vectorize_policies (
    source_id TEXT NOT NULL,
    prefix TEXT NOT NULL DEFAULT '',
    vectorize INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source_id, prefix)
);

CREATE TABLE IF NOT EXISTS scans (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    prefix TEXT NOT NULL DEFAULT '',
    job_id TEXT,
    status TEXT NOT NULL,
    complete INTEGER NOT NULL DEFAULT 0,
    pages INTEGER NOT NULL DEFAULT 0,
    listed_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    extra_json TEXT NOT NULL DEFAULT '{}'
);
"""

MIGRATION_4 = """
CREATE TABLE IF NOT EXISTS vector_units (
    vector_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    modality TEXT NOT NULL,
    unit_index INTEGER NOT NULL,
    time_seconds REAL,
    preview_key TEXT,
    collection TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'associated',
    created_at TEXT NOT NULL,
    UNIQUE(asset_id, version, modality, unit_index),
    FOREIGN KEY(asset_id) REFERENCES assets(id)
);
"""


MIGRATION_5 = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    token_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_runtime (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    enabled INTEGER NOT NULL DEFAULT 0,
    signing_key_source TEXT NOT NULL DEFAULT 'unset',
    signing_key TEXT,
    token_version INTEGER NOT NULL DEFAULT 1,
    expire_seconds INTEGER NOT NULL DEFAULT 86400,
    updated_at TEXT NOT NULL
);
"""



MIGRATION_6 = """
CREATE TABLE IF NOT EXISTS asset_index_profiles (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    dim INTEGER NOT NULL,
    preprocess_version TEXT NOT NULL,
    index_state TEXT NOT NULL DEFAULT 'none',
    indexed_content_version INTEGER NOT NULL DEFAULT 0,
    collection TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(asset_id, profile, model_id, model_version, dim, preprocess_version),
    FOREIGN KEY(asset_id) REFERENCES assets(id)
);
CREATE INDEX IF NOT EXISTS idx_asset_index_profiles_asset ON asset_index_profiles(asset_id);

CREATE TABLE IF NOT EXISTS rebuild_queue (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    dim INTEGER NOT NULL,
    preprocess_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    execute INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(asset_id, profile, model_id, model_version, dim, preprocess_version),
    FOREIGN KEY(asset_id) REFERENCES assets(id)
);
"""


ASSET_COLUMNS_V3 = [
    ("lifecycle", "TEXT NOT NULL DEFAULT 'active'"),
    ("index_state", "TEXT NOT NULL DEFAULT 'none'"),
    ("version", "INTEGER NOT NULL DEFAULT 1"),
    ("ready_version", "INTEGER NOT NULL DEFAULT 0"),
    ("size", "INTEGER"),
    ("etag", "TEXT"),
    ("last_modified", "TEXT"),
    ("excluded", "INTEGER NOT NULL DEFAULT 0"),
    ("source_deleted", "INTEGER NOT NULL DEFAULT 0"),
    ("vector_purge_status", "TEXT NOT NULL DEFAULT 'not_required'"),
    ("vector_purge_message", "TEXT"),
]


class _LockedCursor:
    def __init__(self, cursor: sqlite3.Cursor, lock: threading.RLock) -> None:
        self._cursor = cursor
        self._lock = lock

    def fetchone(self):
        with self._lock:
            return self._cursor.fetchone()

    def fetchall(self):
        with self._lock:
            return self._cursor.fetchall()

    def __iter__(self):
        with self._lock:
            rows = self._cursor.fetchall()
        return iter(rows)

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class SerialConnection:
    """同一 SQLite 连接可被调度线程与请求线程共享，串行化访问避免 InterfaceError。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.RLock()

    def execute(self, *args, **kwargs):
        with self._lock:
            return _LockedCursor(self._conn.execute(*args, **kwargs), self._lock)

    def executescript(self, *args, **kwargs):
        with self._lock:
            return self._conn.executescript(*args, **kwargs)

    def commit(self):
        with self._lock:
            return self._conn.commit()

    def close(self):
        with self._lock:
            return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def connect(path: Path) -> SerialConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return SerialConnection(conn)


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return int(row["v"] or 0) if row else 0


def _migrate_vector_units_space(conn) -> None:
    """Allow one asset version to have vectors in multiple model spaces. Do not drop old rows."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(vector_units)").fetchall()}
    if "preprocess_version" in cols and "model_id" in cols:
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vector_units_v7 (
            vector_id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            modality TEXT NOT NULL,
            unit_index INTEGER NOT NULL,
            time_seconds REAL,
            preview_key TEXT,
            collection TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'associated',
            created_at TEXT NOT NULL,
            profile TEXT NOT NULL DEFAULT '',
            model_id TEXT NOT NULL DEFAULT '',
            model_version TEXT NOT NULL DEFAULT '',
            dim INTEGER,
            preprocess_version TEXT NOT NULL DEFAULT '',
            UNIQUE(asset_id, version, modality, unit_index, profile, model_id, model_version, dim, preprocess_version),
            FOREIGN KEY(asset_id) REFERENCES assets(id)
        );
        """
    )
    rows = conn.execute("SELECT * FROM vector_units").fetchall()
    for row in rows:
        item = dict(row)
        collection = item.get("collection") or ""
        profile, model_id, model_version, dim, preprocess = "", "", "", None, ""
        # 既有 collection：pica_{profile}_{model}_{version}_{dim}_{modality}
        if collection.startswith("pica_"):
            parts = collection.split("_")
            # pica dev chinese clip vit b 16 int8 dynamic 512 frame
            if len(parts) >= 4 and parts[-1] in {"frame", "subtitle"}:
                maybe_dim = parts[-2]
                if maybe_dim.isdigit():
                    dim = int(maybe_dim)
                # drop pica and modality
                inner = parts[1:-2]
                if inner:
                    profile = inner[0]
                    rest = inner[1:]
                    # trailing version token often last before dim? uncertain.
                    # Prefer the known live collection backfill below.
                    model_id = "_".join(rest[:-1]) if len(rest) > 1 else "_".join(rest)
                    model_version = rest[-1] if rest else ""
        if collection == "pica_dev_chinese_clip_vit_b_16_int8_dynamic_512_frame" or collection.endswith("_512_frame") or collection.endswith("_512_subtitle"):
            profile = profile or "dev"
            if "chinese_clip_vit_b_16" in collection:
                model_id = "chinese-clip-vit-b-16"
                model_version = "int8_dynamic"
            dim = 512 if dim is None else dim
            preprocess = "legacy_center_crop_v0"
        conn.execute(
            """
            INSERT OR IGNORE INTO vector_units_v7(
                vector_id, asset_id, version, modality, unit_index, time_seconds,
                preview_key, collection, status, created_at,
                profile, model_id, model_version, dim, preprocess_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["vector_id"], item["asset_id"], int(item["version"]), item["modality"],
                int(item["unit_index"]), item.get("time_seconds"), item.get("preview_key"),
                collection, item.get("status") or "associated", item.get("created_at") or iso(),
                profile, model_id, model_version, dim, preprocess,
            ),
        )
    conn.execute("DROP TABLE vector_units")
    conn.execute("ALTER TABLE vector_units_v7 RENAME TO vector_units")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vector_units_asset ON vector_units(asset_id, version, modality)")



def _migrate_remote_vector_id(conn) -> None:
    """Separate local association PK from the actual remote entity id. Do not invent values here."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(vector_units)").fetchall()}
    if "remote_vector_id" not in cols:
        conn.execute("ALTER TABLE vector_units ADD COLUMN remote_vector_id TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_vector_units_remote ON vector_units(collection, remote_vector_id)"
    )


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (1, ?)",
        (iso(),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO runtime_flags(key, value) VALUES ('query_priority', '0')"
    )
    if _current_version(conn) < 2:
        conn.executescript(MIGRATION_2)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (2, ?)",
            (iso(),),
        )
    if _current_version(conn) < 3:
        _add_columns(conn, "assets", ASSET_COLUMNS_V3)
        _add_columns(conn, "sources", [("deleted", "INTEGER NOT NULL DEFAULT 0")])
        conn.executescript(MIGRATION_3)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (3, ?)",
            (iso(),),
        )
    if _current_version(conn) < 4:
        conn.executescript(MIGRATION_4)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (4, ?)",
            (iso(),),
        )
    if _current_version(conn) < 5:
        conn.executescript(MIGRATION_5)
        # 默认关闭登录，不根据环境变量自动启用，避免锁住已有开发实例。
        conn.execute(
            "INSERT OR IGNORE INTO auth_runtime(id, enabled, signing_key_source, signing_key, token_version, expire_seconds, updated_at) VALUES (1, 0, 'unset', NULL, 1, 86400, ?)",
            (iso(),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (5, ?)",
            (iso(),),
        )
    if _current_version(conn) < 6:
        conn.executescript(MIGRATION_6)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (6, ?)",
            (iso(),),
        )
    if _current_version(conn) < 7:
        _migrate_vector_units_space(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (7, ?)",
            (iso(),),
        )
    if _current_version(conn) < 8:
        _migrate_remote_vector_id(conn)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (8, ?)",
            (iso(),),
        )
    if _current_version(conn) < 9:
        _add_columns(
            conn,
            "assets",
            [
                ("origin", "TEXT NOT NULL DEFAULT 'external'"),
                ("display_name", "TEXT"),
            ],
        )
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS asset_tags (
                asset_id TEXT NOT NULL,
                tag TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (asset_id, tag),
                FOREIGN KEY(asset_id) REFERENCES assets(id)
            );
            CREATE INDEX IF NOT EXISTS idx_asset_tags_tag ON asset_tags(tag);
            CREATE INDEX IF NOT EXISTS idx_asset_tags_asset ON asset_tags(asset_id);
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (9, ?)",
            (iso(),),
        )
    if _current_version(conn) < 10:
        _add_columns(
            conn,
            "assets",
            [
                ("artifact_purge_status", "TEXT NOT NULL DEFAULT 'not_required'"),
                ("artifact_purge_message", "TEXT"),
            ],
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (10, ?)",
            (iso(),),
        )
    if _current_version(conn) < 11:
        _add_columns(conn, "assets", [("external_ref", "TEXT")])
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS api_tokens (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                token_hash TEXT NOT NULL,
                token_prefix TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_api_tokens_prefix ON api_tokens(token_prefix);
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (11, ?)",
            (iso(),),
        )
    if _current_version(conn) < 12:
        _add_columns(
            conn,
            "assets",
            [
                ("description", "TEXT NOT NULL DEFAULT ''"),
                ("cover_key", "TEXT"),
            ],
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (12, ?)",
            (iso(),),
        )
    conn.commit()


def _add_columns(conn: sqlite3.Connection, table: str, columns: list[tuple[str, str]]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, decl in columns:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
