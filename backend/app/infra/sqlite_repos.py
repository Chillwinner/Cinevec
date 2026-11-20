from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from typing import Any

from app.domain.errors import ConflictError, NotFoundError
from app.domain.face import average_normalized_vectors, decode_vector_blob, encode_vector_blob
from app.domain.models import Asset, Job, JobStatus, Source
from app.infra.clock import iso, now, parse_dt
from app.infra.object_keys import display_name_from_key

def _row_val(row: sqlite3.Row, key: str, default=None):
    try:
        return row[key]
    except (IndexError, KeyError):
        return default




def _source_from_row(row: sqlite3.Row) -> Source:
    return Source(
        id=row["id"],
        name=row["name"],
        endpoint=row["endpoint"],
        bucket=row["bucket"],
        region=row["region"] or "",
        role=row["role"],
        is_platform_storage=bool(row["is_platform_storage"]),
        access_key_configured=bool(row["access_key"]),
        secret_configured=bool(row["secret_key"]),
        last_probe_status=row["last_probe_status"],
        last_probe_message=row["last_probe_message"],
        created_at=parse_dt(row["created_at"]) or now(),
        updated_at=parse_dt(row["updated_at"]) or now(),
        deleted=bool(_row_val(row, "deleted", 0)),
    )


def _asset_from_row(row: sqlite3.Row) -> Asset:
    return Asset(
        id=row["id"],
        source_id=row["source_id"],
        object_key=row["object_key"],
        kind=row["kind"],
        vectorize=bool(row["vectorize"]),
        status=row["status"],
        created_at=parse_dt(row["created_at"]) or now(),
        updated_at=parse_dt(row["updated_at"]) or now(),
        lifecycle=_row_val(row, "lifecycle", "active") or "active",
        index_state=_row_val(row, "index_state", "none") or "none",
        version=int(_row_val(row, "version", 1) or 1),
        ready_version=int(_row_val(row, "ready_version", 0) or 0),
        size=_row_val(row, "size"),
        etag=_row_val(row, "etag"),
        last_modified=_row_val(row, "last_modified"),
        excluded=bool(_row_val(row, "excluded", 0)),
        source_deleted=bool(_row_val(row, "source_deleted", 0)),
        vector_purge_status=_row_val(row, "vector_purge_status", "not_required") or "not_required",
        vector_purge_message=_row_val(row, "vector_purge_message"),
        artifact_purge_status=_row_val(row, "artifact_purge_status", "not_required") or "not_required",
        artifact_purge_message=_row_val(row, "artifact_purge_message"),
        origin=_row_val(row, "origin", "external") or "external",
        display_name=_row_val(row, "display_name"),
        external_ref=_parse_json_obj(_row_val(row, "external_ref")),
        description=_row_val(row, "description", "") or "",
        cover_key=_row_val(row, "cover_key"),
    )


def _parse_json_obj(raw) -> dict[str, Any] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _dump_json_obj(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    return json.dumps(value, ensure_ascii=False)


def _job_from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        job_type=row["job_type"],
        status=row["status"],
        source_id=row["source_id"],
        asset_id=row["asset_id"],
        stage=row["stage"],
        step=row["step"],
        progress=row["progress"],
        queue_position=row["queue_position"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        checkpoint=json.loads(row["checkpoint_json"] or "{}"),
        payload=json.loads(row["payload_json"] or "{}"),
        lease_until=parse_dt(row["lease_until"]),
        created_at=parse_dt(row["created_at"]),
        updated_at=parse_dt(row["updated_at"]),
    )



def _frame_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "asset_id": row["asset_id"],
        "job_id": row["job_id"],
        "index": row["frame_index"],
        "time_seconds": row["time_seconds"],
        "kept": bool(row["kept"]),
        "drop_reason": row["drop_reason"],
        "preview_key": row["preview_key"],
        "model_input_key": row["model_input_key"],
        "extra": json.loads(row["extra_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class SqliteMetadataRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def add_source(self, source: Source, access_key: str | None, secret_key: str | None) -> Source:
        ts = iso()
        self._conn.execute(
            """
            INSERT INTO sources(
                id, name, endpoint, bucket, region, role, is_platform_storage,
                access_key, secret_key, last_probe_status, last_probe_message,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.id,
                source.name,
                source.endpoint,
                source.bucket,
                source.region,
                source.role,
                int(source.is_platform_storage),
                access_key,
                secret_key,
                source.last_probe_status,
                source.last_probe_message,
                ts,
                ts,
            ),
        )
        self._conn.commit()
        saved = self.get_source(source.id)
        assert saved is not None
        return saved

    def list_sources(self) -> list[Source]:
        rows = self._conn.execute("SELECT * FROM sources WHERE COALESCE(deleted, 0) = 0 ORDER BY created_at DESC").fetchall()
        return [_source_from_row(r) for r in rows]

    def get_source(self, source_id: str) -> Source | None:
        row = self._conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _source_from_row(row) if row else None

    def get_source_credentials(self, source_id: str) -> tuple[str | None, str | None]:
        row = self._conn.execute(
            "SELECT access_key, secret_key FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if not row:
            return None, None
        return row["access_key"], row["secret_key"]

    def update_source(self, source_id: str, **fields: Any) -> Source:
        existing = self.get_source(source_id)
        if existing is None:
            raise NotFoundError("存储源不存在")
        allowed = {
            "name",
            "endpoint",
            "bucket",
            "region",
            "role",
            "is_platform_storage",
            "access_key",
            "secret_key",
            "last_probe_status",
            "last_probe_message",
            "deleted",
        }
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if "is_platform_storage" in updates:
            updates["is_platform_storage"] = int(bool(updates["is_platform_storage"]))
        if "deleted" in updates:
            updates["deleted"] = int(bool(updates["deleted"]))
        if not updates:
            return existing
        updates["updated_at"] = iso()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(
            f"UPDATE sources SET {assignments} WHERE id = ?",
            (*updates.values(), source_id),
        )
        self._conn.commit()
        source = self.get_source(source_id)
        assert source is not None
        return source

    def delete_source(self, source_id: str) -> None:
        """软删除来源配置，保留来源行与资源登记，不触碰对象存储原文件。"""
        existing = self.get_source(source_id)
        if existing is None:
            raise NotFoundError("存储源不存在")
        self._conn.execute(
            "UPDATE sources SET deleted = 1, updated_at = ? WHERE id = ?",
            (iso(), source_id),
        )
        self._conn.execute(
            """
            UPDATE assets SET source_deleted = 1, lifecycle = CASE
                WHEN excluded = 1 THEN lifecycle
                ELSE 'source_invalid'
            END, updated_at = ?
            WHERE source_id = ?
            """,
            (iso(), source_id),
        )
        self._conn.commit()

    def set_source_probe(self, source_id: str, status: str, message: str) -> Source:
        return self.update_source(source_id, last_probe_status=status, last_probe_message=message)

    def upsert_asset(self, asset: Asset) -> Asset:
        ts = iso()
        try:
            self._conn.execute(
                """
                INSERT INTO assets(
                    id, source_id, object_key, kind, vectorize, status,
                    lifecycle, index_state, version, ready_version, size, etag, last_modified,
                    excluded, source_deleted, vector_purge_status, vector_purge_message,
                    origin, display_name, external_ref, description, cover_key,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id, object_key) DO UPDATE SET
                    kind=excluded.kind,
                    vectorize=excluded.vectorize,
                    status=excluded.status,
                    size=COALESCE(excluded.size, assets.size),
                    etag=COALESCE(excluded.etag, assets.etag),
                    last_modified=COALESCE(excluded.last_modified, assets.last_modified),
                    display_name=COALESCE(excluded.display_name, assets.display_name),
                    origin=excluded.origin,
                    external_ref=COALESCE(excluded.external_ref, assets.external_ref),
                    description=CASE WHEN excluded.description != '' THEN excluded.description ELSE assets.description END,
                    cover_key=COALESCE(excluded.cover_key, assets.cover_key),
                    updated_at=excluded.updated_at
                """,
                (
                    asset.id,
                    asset.source_id,
                    asset.object_key,
                    asset.kind,
                    int(asset.vectorize),
                    asset.status,
                    asset.lifecycle,
                    asset.index_state,
                    asset.version,
                    asset.ready_version,
                    asset.size,
                    asset.etag,
                    asset.last_modified,
                    int(asset.excluded),
                    int(asset.source_deleted),
                    asset.vector_purge_status,
                    asset.vector_purge_message,
                    asset.origin or "external",
                    asset.display_name,
                    _dump_json_obj(asset.external_ref),
                    asset.description or "",
                    asset.cover_key,
                    ts,
                    ts,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("资源登记冲突") from exc
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM assets WHERE source_id = ? AND object_key = ?",
            (asset.source_id, asset.object_key),
        ).fetchone()
        return _asset_from_row(row)

    def _asset_filter_sql(
        self,
        source_id: str | None = None,
        *,
        kind: str | None = None,
        excluded: bool | None = None,
        pending: bool | None = None,
        indexed: bool | None = None,
        query: str | None = None,
        ref_id: str | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if excluded is True:
            clauses.append("COALESCE(excluded, 0) = 1")
        elif excluded is False:
            clauses.append("COALESCE(excluded, 0) = 0")
        if pending is True:
            clauses.append("(COALESCE(excluded, 0) = 0 AND COALESCE(vectorize, 0) = 1 AND COALESCE(index_state, '') != 'ready')")
        elif pending is False:
            clauses.append("(COALESCE(excluded, 0) = 1 OR COALESCE(vectorize, 0) = 0 OR COALESCE(index_state, '') = 'ready')")
        if indexed is True:
            clauses.append("COALESCE(index_state, '') = 'ready'")
        elif indexed is False:
            clauses.append("COALESCE(index_state, '') != 'ready'")
        if query:
            like = self._like_contains(query.strip())
            clauses.append(
                "(object_key LIKE ? ESCAPE char(92) OR IFNULL(display_name, '') LIKE ? ESCAPE char(92) "
                "OR IFNULL(description, '') LIKE ? ESCAPE char(92) "
                "OR EXISTS (SELECT 1 FROM asset_tags x WHERE x.asset_id = assets.id AND x.tag LIKE ? ESCAPE char(92)))"
            )
            params.extend([like, like, like, like])
        if ref_id and ref_id.strip():
            cleaned = ref_id.strip()
            clauses.append("(object_key = ? OR json_extract(external_ref, '$.ref_id') = ?)")
            params.extend([f"plugin/{cleaned}", cleaned])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where, params

    def list_assets(
        self,
        source_id: str | None = None,
        *,
        kind: str | None = None,
        excluded: bool | None = None,
        pending: bool | None = None,
        indexed: bool | None = None,
        query: str | None = None,
        ref_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Asset]:
        where, params = self._asset_filter_sql(
            source_id,
            kind=kind,
            excluded=excluded,
            pending=pending,
            indexed=indexed,
            query=query,
            ref_id=ref_id,
        )
        sql = f"SELECT * FROM assets {where} ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        rows = self._conn.execute(sql, params).fetchall()
        return [_asset_from_row(r) for r in rows]

    def count_assets(
        self,
        source_id: str | None = None,
        *,
        kind: str | None = None,
        excluded: bool | None = None,
        pending: bool | None = None,
        indexed: bool | None = None,
        query: str | None = None,
        ref_id: str | None = None,
    ) -> int:
        where, params = self._asset_filter_sql(
            source_id,
            kind=kind,
            excluded=excluded,
            pending=pending,
            indexed=indexed,
            query=query,
            ref_id=ref_id,
        )
        row = self._conn.execute(f"SELECT COUNT(*) AS n FROM assets {where}", params).fetchone()
        return int(row["n"] if row else 0)

    def list_object_keys(self, source_id: str | None = None) -> list[str]:
        if source_id:
            rows = self._conn.execute(
                "SELECT object_key FROM assets WHERE source_id = ?",
                (source_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT object_key FROM assets").fetchall()
        return [str(row["object_key"]) for row in rows]

    def preview_covers(self, asset_ids: list[str]) -> dict[str, int]:
        if not asset_ids:
            return {}
        marks = ",".join("?" for _ in asset_ids)
        rows = self._conn.execute(
            f"""
            SELECT asset_id, MIN(frame_index) AS idx
            FROM frames
            WHERE asset_id IN ({marks}) AND kept = 1 AND IFNULL(preview_key, '') != ''
            GROUP BY asset_id
            """,
            asset_ids,
        ).fetchall()
        return {str(row["asset_id"]): int(row["idx"]) for row in rows}

    def get_asset(self, asset_id: str) -> Asset | None:
        row = self._conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return _asset_from_row(row) if row else None

    def update_asset_status(self, asset_id: str, status: str) -> Asset:
        if self.get_asset(asset_id) is None:
            raise NotFoundError("资源不存在")
        self._conn.execute(
            "UPDATE assets SET status = ?, updated_at = ? WHERE id = ?",
            (status, iso(), asset_id),
        )
        self._conn.commit()
        asset = self.get_asset(asset_id)
        assert asset is not None
        return asset

    def get_platform_storage(self) -> Source | None:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE is_platform_storage = 1 AND COALESCE(deleted, 0) = 0 ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        return _source_from_row(row) if row else None

    def upsert_frame(self, asset_id: str, job_id: str, frame) -> None:
        ts = iso()
        extra = getattr(frame, "extra", None) or {}
        self._conn.execute(
            """
            INSERT INTO frames(
                asset_id, job_id, frame_index, time_seconds, kept, drop_reason,
                preview_key, model_input_key, extra_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, frame_index) DO UPDATE SET
                job_id=excluded.job_id,
                time_seconds=excluded.time_seconds,
                kept=excluded.kept,
                drop_reason=excluded.drop_reason,
                preview_key=excluded.preview_key,
                model_input_key=excluded.model_input_key,
                extra_json=excluded.extra_json,
                updated_at=excluded.updated_at
            """,
            (
                asset_id,
                job_id,
                frame.index,
                frame.time_seconds,
                int(frame.kept),
                frame.drop_reason,
                frame.preview_key,
                frame.model_input_key,
                json.dumps(extra, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        self._conn.commit()

    def get_frame(self, asset_id: str, frame_index: int) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM frames WHERE asset_id = ? AND frame_index = ?",
            (asset_id, frame_index),
        ).fetchone()
        return _frame_from_row(row) if row else None

    def list_frames(self, asset_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM frames WHERE asset_id = ? ORDER BY frame_index ASC",
            (asset_id,),
        ).fetchall()
        return [_frame_from_row(r) for r in rows]

    def delete_frames(self, asset_id: str) -> int:
        cur = self._conn.execute("DELETE FROM frames WHERE asset_id = ?", (asset_id,))
        self._conn.commit()
        return int(cur.rowcount or 0)

    def merge_frame_extra(self, asset_id: str, frame_index: int, extra_update: dict[str, Any]) -> dict[str, Any]:
        existing = self.get_frame(asset_id, frame_index)
        if existing is None:
            raise NotFoundError("采样帧不存在")
        extra = dict(existing.get("extra") or {})
        for key, value in extra_update.items():
            if key == "model_inputs_by_preprocess" and isinstance(value, dict):
                merged = dict(extra.get("model_inputs_by_preprocess") or {})
                merged.update(value)
                extra["model_inputs_by_preprocess"] = merged
            else:
                extra[key] = value
        from app.domain.media import PreparedFrame
        self.upsert_frame(
            asset_id,
            existing.get("job_id") or "",
            PreparedFrame(
                index=int(existing["frame_index"] if "frame_index" in existing else existing.get("index") or frame_index),
                time_seconds=float(existing.get("time_seconds") or 0),
                kept=bool(existing.get("kept")),
                drop_reason=existing.get("drop_reason"),
                preview_key=existing.get("preview_key"),
                model_input_key=existing.get("model_input_key"),
                extra=extra,
            ),
        )
        saved = self.get_frame(asset_id, frame_index)
        assert saved is not None
        return saved


    def get_setting(self, key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT value_json FROM app_settings WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return None
        return json.loads(row["value_json"] or "{}")

    def set_setting(self, key: str, value: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO app_settings(key, value_json, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), iso()),
        )
        self._conn.commit()



    def get_asset_by_key(self, source_id: str, object_key: str) -> Asset | None:
        row = self._conn.execute(
            "SELECT * FROM assets WHERE source_id = ? AND object_key = ?",
            (source_id, object_key),
        ).fetchone()
        return _asset_from_row(row) if row else None

    def update_asset(self, asset_id: str, **fields: Any) -> Asset:
        if self.get_asset(asset_id) is None:
            raise NotFoundError("资源不存在")
        allowed = {
            "kind", "vectorize", "status", "lifecycle", "index_state", "version",
            "ready_version", "size", "etag", "last_modified", "excluded",
            "source_deleted", "vector_purge_status", "vector_purge_message",
            "artifact_purge_status", "artifact_purge_message",
            "origin", "display_name", "external_ref", "description", "cover_key",
        }
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"vectorize", "excluded", "source_deleted"}:
                value = int(bool(value))
            if key == "external_ref" and not isinstance(value, str):
                value = _dump_json_obj(value)
            updates[key] = value
        if not updates:
            asset = self.get_asset(asset_id)
            assert asset is not None
            return asset
        updates["updated_at"] = iso()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(f"UPDATE assets SET {assignments} WHERE id = ?", (*updates.values(), asset_id))
        self._conn.commit()
        asset = self.get_asset(asset_id)
        assert asset is not None
        return asset

    def search_catalog(self, query: str, source_id: str | None = None) -> list[Asset]:
        return [row["asset"] for row in self.search_lexical(query, source_id)]

    def _like_contains(self, query: str) -> str:
        escaped = (query or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

    def list_tags(self, asset_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT tag FROM asset_tags WHERE asset_id = ? ORDER BY tag COLLATE NOCASE",
            (asset_id,),
        ).fetchall()
        return [row["tag"] for row in rows]

    def list_tags_for_assets(self, asset_ids: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {aid: [] for aid in asset_ids}
        if not asset_ids:
            return out
        placeholders = ",".join("?" for _ in asset_ids)
        rows = self._conn.execute(
            f"SELECT asset_id, tag FROM asset_tags WHERE asset_id IN ({placeholders}) ORDER BY tag COLLATE NOCASE",
            tuple(asset_ids),
        ).fetchall()
        for row in rows:
            out.setdefault(row["asset_id"], []).append(row["tag"])
        return out

    def add_tag(self, asset_id: str, tag: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO asset_tags(asset_id, tag, created_at) VALUES (?, ?, ?)",
            (asset_id, tag, iso()),
        )
        self._conn.commit()

    def remove_tag(self, asset_id: str, tag: str) -> None:
        self._conn.execute("DELETE FROM asset_tags WHERE asset_id = ? AND tag = ?", (asset_id, tag))
        self._conn.commit()

    def search_lexical(self, query: str, source_id: str | None = None) -> list[dict[str, Any]]:
        like = self._like_contains(query.strip())
        params: list[Any] = [like, like, like, like]
        source_sql = ""
        if source_id:
            source_sql = "AND a.source_id = ?"
            params.append(source_id)
        rows = self._conn.execute(
            f"""
            SELECT a.*,
                   GROUP_CONCAT(t.tag, char(31)) AS tag_blob
            FROM assets a
            LEFT JOIN asset_tags t ON t.asset_id = a.id
            WHERE COALESCE(a.excluded, 0) = 0
              AND (
                a.object_key LIKE ? ESCAPE char(92)
                OR IFNULL(a.display_name, '') LIKE ? ESCAPE char(92)
                OR IFNULL(a.description, '') LIKE ? ESCAPE char(92)
                OR EXISTS (
                    SELECT 1 FROM asset_tags x
                    WHERE x.asset_id = a.id AND x.tag LIKE ? ESCAPE char(92)
                )
              )
            {source_sql}
            GROUP BY a.id
            """,
            params,
        ).fetchall()
        needle = query.strip().lower()
        items: list[dict[str, Any]] = []
        for row in rows:
            asset = _asset_from_row(row)
            tags = [part for part in (row["tag_blob"] or "").split("\x1f") if part]
            matched: list[str] = []
            key = (asset.object_key or "").lower()
            name = (asset.display_name or display_name_from_key(asset.object_key)).lower()
            desc = (getattr(asset, "description", "") or "").lower()
            if needle in key or needle in name:
                matched.append("filename")
            if any(needle in tag.lower() for tag in tags):
                matched.append("tag")
            if needle in desc:
                matched.append("description")
            items.append({"asset": asset, "tags": tags, "matched_on": matched})
        items.sort(
            key=lambda item: (
                0 if len(item["matched_on"]) >= 2 else 1 if "filename" in item["matched_on"] else 2,
                item["asset"].object_key,
            )
        )
        return items

    def is_excluded(self, source_id: str, object_key: str) -> bool:
        row = self._conn.execute(
            "SELECT active FROM exclusions WHERE source_id = ? AND object_key = ?",
            (source_id, object_key),
        ).fetchone()
        return bool(row and row["active"])

    def add_exclusion(self, source_id: str, object_key: str, reason: str) -> None:
        self._conn.execute(
            """
            INSERT INTO exclusions(source_id, object_key, reason, active, created_at, restored_at)
            VALUES (?, ?, ?, 1, ?, NULL)
            ON CONFLICT(source_id, object_key) DO UPDATE SET
                reason=excluded.reason, active=1, created_at=excluded.created_at, restored_at=NULL
            """,
            (source_id, object_key, reason, iso()),
        )
        self._conn.commit()

    def restore_exclusion(self, source_id: str, object_key: str) -> None:
        cur = self._conn.execute(
            "UPDATE exclusions SET active = 0, restored_at = ? WHERE source_id = ? AND object_key = ? AND active = 1",
            (iso(), source_id, object_key),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError("没有可恢复的排除记录")

    def list_exclusions(self, source_id: str | None = None, active_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM exclusions"
        params: list[Any] = []
        clauses = []
        if source_id:
            clauses.append("source_id = ?")
            params.append(source_id)
        if active_only:
            clauses.append("active = 1")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_policy(self, source_id: str, prefix: str) -> bool | None:
        row = self._conn.execute(
            "SELECT vectorize FROM vectorize_policies WHERE source_id = ? AND prefix = ?",
            (source_id, prefix),
        ).fetchone()
        if row is None:
            return None
        return bool(row["vectorize"])

    def list_policies(self, source_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM vectorize_policies WHERE source_id = ? ORDER BY length(prefix) DESC",
            (source_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_policy(self, source_id: str, prefix: str, vectorize: bool) -> None:
        self._conn.execute(
            """
            INSERT INTO vectorize_policies(source_id, prefix, vectorize, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id, prefix) DO UPDATE SET vectorize=excluded.vectorize, updated_at=excluded.updated_at
            """,
            (source_id, prefix, int(vectorize), iso()),
        )
        self._conn.commit()

    def resolve_policy(self, source_id: str, object_key: str) -> bool | None:
        policies = self.list_policies(source_id)
        # 最长前缀优先；完全未配置则返回 None（刷新时不擅自向量化）
        for item in policies:
            prefix = item["prefix"] or ""
            if object_key.startswith(prefix):
                return bool(item["vectorize"])
        return None

    def insert_scan(self, record: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO scans(id, source_id, prefix, job_id, status, complete, pages, listed_count,
                error_code, error_message, started_at, finished_at, extra_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["source_id"],
                record.get("prefix") or "",
                record.get("job_id"),
                record["status"],
                int(bool(record.get("complete"))),
                int(record.get("pages") or 0),
                int(record.get("listed_count") or 0),
                record.get("error_code"),
                record.get("error_message"),
                record.get("started_at"),
                record.get("finished_at"),
                json.dumps(record.get("extra") or {}, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def update_scan(self, scan_id: str, **fields: Any) -> dict[str, Any]:
        updates = {}
        mapping = {
            "status": "status",
            "complete": "complete",
            "pages": "pages",
            "listed_count": "listed_count",
            "error_code": "error_code",
            "error_message": "error_message",
            "finished_at": "finished_at",
            "job_id": "job_id",
        }
        for key, column in mapping.items():
            if key in fields:
                value = fields[key]
                if key == "complete":
                    value = int(bool(value))
                updates[column] = value
        if "extra" in fields:
            updates["extra_json"] = json.dumps(fields["extra"], ensure_ascii=False)
        if not updates:
            return self.get_scan(scan_id)
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(f"UPDATE scans SET {assignments} WHERE id = ?", (*updates.values(), scan_id))
        self._conn.commit()
        return self.get_scan(scan_id)

    def get_scan(self, scan_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            raise NotFoundError("扫描记录不存在")
        data = dict(row)
        data["complete"] = bool(data.get("complete"))
        data["extra"] = json.loads(data.pop("extra_json") or "{}")
        return data

    def list_scans(self, source_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        if source_id:
            rows = self._conn.execute(
                "SELECT * FROM scans WHERE source_id = ? ORDER BY started_at DESC LIMIT ?",
                (source_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM scans ORDER BY started_at DESC LIMIT ?", (limit,)
            ).fetchall()
        items = []
        for row in rows:
            data = dict(row)
            data["complete"] = bool(data.get("complete"))
            data["extra"] = json.loads(data.pop("extra_json") or "{}")
            items.append(data)
        return items

    def add_association(self, assoc_id: str, video_asset_id: str, subtitle_asset_id: str) -> dict[str, Any]:
        try:
            self._conn.execute(
                """
                INSERT INTO subtitle_associations(id, video_asset_id, subtitle_asset_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (assoc_id, video_asset_id, subtitle_asset_id, iso()),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError("该视频与字幕已经手动关联过") from exc
        self._conn.commit()
        return self.get_association(assoc_id)

    def get_association(self, assoc_id: str) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM subtitle_associations WHERE id = ?", (assoc_id,)).fetchone()
        if not row:
            raise NotFoundError("字幕关联不存在")
        return dict(row)

    def get_association_pair(self, video_asset_id: str, subtitle_asset_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM subtitle_associations WHERE video_asset_id = ? AND subtitle_asset_id = ?",
            (video_asset_id, subtitle_asset_id),
        ).fetchone()
        return dict(row) if row else None

    def list_associations(self, video_asset_id: str | None = None) -> list[dict[str, Any]]:
        if video_asset_id:
            rows = self._conn.execute(
                "SELECT * FROM subtitle_associations WHERE video_asset_id = ? ORDER BY created_at DESC",
                (video_asset_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM subtitle_associations ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def delete_association(self, assoc_id: str) -> None:
        self._conn.execute("DELETE FROM subtitle_cues WHERE association_id = ?", (assoc_id,))
        self._conn.execute("DELETE FROM subtitle_groups WHERE association_id = ?", (assoc_id,))
        cur = self._conn.execute("DELETE FROM subtitle_associations WHERE id = ?", (assoc_id,))
        self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError("字幕关联不存在")

    def replace_cues_and_groups(self, assoc_id: str, cues: list, groups: list) -> None:
        self._conn.execute("DELETE FROM subtitle_cues WHERE association_id = ?", (assoc_id,))
        self._conn.execute("DELETE FROM subtitle_groups WHERE association_id = ?", (assoc_id,))
        for cue in cues:
            self._conn.execute(
                """
                INSERT INTO subtitle_cues(association_id, cue_index, start_seconds, end_seconds, text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (assoc_id, cue.index, cue.start_seconds, cue.end_seconds, cue.text),
            )
        for group in groups:
            self._conn.execute(
                """
                INSERT INTO subtitle_groups(association_id, group_index, start_seconds, end_seconds, text, cue_indexes_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    assoc_id,
                    group.index,
                    group.start_seconds,
                    group.end_seconds,
                    group.text,
                    json.dumps(group.cue_indexes, ensure_ascii=False),
                ),
            )
        self._conn.commit()

    def list_cues(self, assoc_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM subtitle_cues WHERE association_id = ? ORDER BY cue_index ASC",
            (assoc_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_groups(self, assoc_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM subtitle_groups WHERE association_id = ? ORDER BY group_index ASC",
            (assoc_id,),
        ).fetchall()
        items = []
        for row in rows:
            data = dict(row)
            data["cue_indexes"] = json.loads(data.pop("cue_indexes_json") or "[]")
            items.append(data)
        return items

    def find_active_jobs(self, job_type: str, source_id: str | None = None, asset_id: str | None = None) -> list[Job]:
        sql = "SELECT * FROM jobs WHERE job_type = ? AND status IN ('waiting', 'running', 'paused')"
        params: list[Any] = [job_type]
        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)
        if asset_id:
            sql += " AND asset_id = ?"
            params.append(asset_id)
        rows = self._conn.execute(sql, params).fetchall()
        return [_job_from_row(r) for r in rows]


    def find_vector_unit_in_space(
        self,
        asset_id: str,
        version: int,
        modality: str,
        unit_index: int,
        *,
        profile: str = "",
        model_id: str = "",
        model_version: str = "",
        dim=None,
        preprocess_version: str = "",
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM vector_units
            WHERE asset_id = ? AND version = ? AND modality = ? AND unit_index = ?
              AND profile = ? AND model_id = ? AND model_version = ?
              AND ifnull(dim, -1) = ifnull(?, -1) AND preprocess_version = ?
            """,
            (
                asset_id,
                int(version),
                modality,
                int(unit_index),
                profile or "",
                model_id or "",
                model_version or "",
                dim,
                preprocess_version or "",
            ),
        ).fetchone()
        return dict(row) if row else None

    def upsert_vector_unit(self, unit: dict[str, Any]) -> None:
        incoming_remote = unit.get("remote_vector_id") or ""
        existing = self.find_vector_unit_in_space(
            unit["asset_id"],
            int(unit["version"]),
            unit["modality"],
            int(unit["unit_index"]),
            profile=unit.get("profile") or "",
            model_id=unit.get("model_id") or "",
            model_version=unit.get("model_version") or "",
            dim=unit.get("dim"),
            preprocess_version=unit.get("preprocess_version") or "",
        )
        vector_id = (existing or {}).get("vector_id") or unit["vector_id"]
        remote = incoming_remote or ((existing or {}).get("remote_vector_id") or "")
        if existing:
            self._conn.execute(
                """
                UPDATE vector_units SET
                    time_seconds=?, preview_key=?, collection=?, status=?,
                    profile=?, model_id=?, model_version=?, dim=?, preprocess_version=?,
                    remote_vector_id=CASE WHEN ? != '' THEN ? ELSE remote_vector_id END
                WHERE vector_id=?
                """,
                (
                    unit.get("time_seconds"),
                    unit.get("preview_key"),
                    unit.get("collection") or existing.get("collection") or "",
                    unit.get("status") or existing.get("status") or "associated",
                    unit.get("profile") or "",
                    unit.get("model_id") or "",
                    unit.get("model_version") or "",
                    unit.get("dim"),
                    unit.get("preprocess_version") or "",
                    remote,
                    remote,
                    existing["vector_id"],
                ),
            )
            self._conn.commit()
            return
        self._conn.execute(
            """
            INSERT INTO vector_units(
                vector_id, asset_id, version, modality, unit_index,
                time_seconds, preview_key, collection, status, created_at,
                profile, model_id, model_version, dim, preprocess_version,
                remote_vector_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vector_id) DO UPDATE SET
                asset_id=excluded.asset_id,
                version=excluded.version,
                modality=excluded.modality,
                unit_index=excluded.unit_index,
                time_seconds=excluded.time_seconds,
                preview_key=excluded.preview_key,
                collection=excluded.collection,
                status=excluded.status,
                profile=excluded.profile,
                model_id=excluded.model_id,
                model_version=excluded.model_version,
                dim=excluded.dim,
                preprocess_version=excluded.preprocess_version,
                remote_vector_id=CASE WHEN excluded.remote_vector_id != '' THEN excluded.remote_vector_id ELSE vector_units.remote_vector_id END
            """,
            (
                vector_id,
                unit["asset_id"],
                int(unit["version"]),
                unit["modality"],
                int(unit["unit_index"]),
                unit.get("time_seconds"),
                unit.get("preview_key"),
                unit.get("collection") or "",
                unit.get("status") or "associated",
                iso(),
                unit.get("profile") or "",
                unit.get("model_id") or "",
                unit.get("model_version") or "",
                unit.get("dim"),
                unit.get("preprocess_version") or "",
                remote,
            ),
        )
        self._conn.commit()

    def get_vector_unit(self, vector_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM vector_units WHERE vector_id = ?", (vector_id,)).fetchone()
        return dict(row) if row else None

    def list_vector_units(
        self,
        asset_id: str,
        version: int | None = None,
        modality: str | None = None,
        *,
        profile: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        dim=None,
        preprocess_version: str | None = None,
        collection: str | None = None,
        vector_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM vector_units WHERE asset_id = ?"
        params: list[Any] = [asset_id]
        if version is not None:
            sql += " AND version = ?"
            params.append(version)
        if modality is not None:
            sql += " AND modality = ?"
            params.append(modality)
        if profile is not None:
            sql += " AND profile = ?"
            params.append(profile)
        if model_id is not None:
            sql += " AND model_id = ?"
            params.append(model_id)
        if model_version is not None:
            sql += " AND model_version = ?"
            params.append(model_version)
        if dim is not None:
            sql += " AND ifnull(dim, -1) = ifnull(?, -1)"
            params.append(dim)
        if preprocess_version is not None:
            sql += " AND preprocess_version = ?"
            params.append(preprocess_version)
        if collection is not None:
            sql += " AND collection = ?"
            params.append(collection)
        if vector_ids:
            placeholders = ",".join("?" * len(vector_ids))
            sql += f" AND vector_id IN ({placeholders})"
            params.extend(vector_ids)
        sql += " ORDER BY modality, unit_index"
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def delete_vector_units(
        self,
        asset_id: str,
        version: int | None = None,
        modality: str | None = None,
        *,
        profile: str | None = None,
        model_id: str | None = None,
        model_version: str | None = None,
        dim=None,
        preprocess_version: str | None = None,
        collection: str | None = None,
        vector_ids: list[str] | None = None,
    ) -> list[str]:
        rows = self.list_vector_units(
            asset_id,
            version=version,
            modality=modality,
            profile=profile,
            model_id=model_id,
            model_version=model_version,
            dim=dim,
            preprocess_version=preprocess_version,
            collection=collection,
            vector_ids=vector_ids,
        )
        ids = [r["vector_id"] for r in rows]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        self._conn.execute(f"DELETE FROM vector_units WHERE vector_id IN ({placeholders})", ids)
        self._conn.commit()
        return ids

    def apply_remote_id_backfill(self, updates: list[dict[str, Any]]) -> int:
        changed = 0
        for item in updates or []:
            vector_id = item.get("vector_id")
            remote = item.get("remote_vector_id") or ""
            if not vector_id or not remote:
                continue
            cur = self._conn.execute(
                "UPDATE vector_units SET remote_vector_id=? WHERE vector_id=? AND remote_vector_id=''",
                (remote, vector_id),
            )
            changed += cur.rowcount or 0
        self._conn.commit()
        return changed

    def assets_by_ids(self, ids: list[str]) -> dict[str, Any]:
        if not ids:
            return {}
        unique = list(dict.fromkeys(ids))
        found = {}
        # 分批以免超参
        for i in range(0, len(unique), 200):
            chunk = unique[i:i + 200]
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(f"SELECT * FROM assets WHERE id IN ({placeholders})", chunk).fetchall()
            for row in rows:
                asset = _asset_from_row(row)
                found[asset.id] = asset
        return found



    def upsert_asset_index_profile(self, record: dict[str, Any]) -> dict[str, Any]:
        now_s = iso()
        existing = self.get_asset_index_profile(
            record["asset_id"],
            profile=record["profile"],
            model_id=record["model_id"],
            model_version=record["model_version"],
            dim=int(record["dim"]),
            preprocess_version=record["preprocess_version"],
        )
        row_id = (existing or {}).get("id") or record.get("id") or str(uuid.uuid4())
        created = (existing or {}).get("created_at") or now_s
        payload = {
            "id": row_id,
            "asset_id": record["asset_id"],
            "profile": record["profile"],
            "model_id": record["model_id"],
            "model_version": record["model_version"],
            "dim": int(record["dim"]),
            "preprocess_version": record["preprocess_version"],
            "index_state": record.get("index_state") or "none",
            "indexed_content_version": int(record.get("indexed_content_version") or 0),
            "collection": record.get("collection") or "",
            "created_at": created,
            "updated_at": now_s,
        }
        self._conn.execute(
            """
            INSERT INTO asset_index_profiles(
                id, asset_id, profile, model_id, model_version, dim, preprocess_version,
                index_state, indexed_content_version, collection, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, profile, model_id, model_version, dim, preprocess_version)
            DO UPDATE SET
                index_state=excluded.index_state,
                indexed_content_version=excluded.indexed_content_version,
                collection=excluded.collection,
                updated_at=excluded.updated_at
            """,
            (
                payload["id"], payload["asset_id"], payload["profile"], payload["model_id"],
                payload["model_version"], payload["dim"], payload["preprocess_version"],
                payload["index_state"], payload["indexed_content_version"], payload["collection"],
                payload["created_at"], payload["updated_at"],
            ),
        )
        self._conn.commit()
        return payload

    def get_asset_index_profile(
        self,
        asset_id: str,
        *,
        profile: str,
        model_id: str,
        model_version: str,
        dim: int,
        preprocess_version: str,
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM asset_index_profiles
            WHERE asset_id = ? AND profile = ? AND model_id = ? AND model_version = ?
              AND dim = ? AND preprocess_version = ?
            """,
            (asset_id, profile, model_id, model_version, int(dim), preprocess_version),
        ).fetchone()
        return dict(row) if row else None

    def list_asset_index_profiles(self, asset_id: str | None = None) -> list[dict[str, Any]]:
        if asset_id:
            rows = self._conn.execute(
                "SELECT * FROM asset_index_profiles WHERE asset_id = ? ORDER BY updated_at",
                (asset_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM asset_index_profiles ORDER BY asset_id, updated_at").fetchall()
        return [dict(r) for r in rows]

    def upsert_rebuild_item(self, record: dict[str, Any]) -> dict[str, Any]:
        now_s = iso()
        existing = self.get_rebuild_item(
            record["asset_id"],
            profile=record["profile"],
            model_id=record["model_id"],
            model_version=record["model_version"],
            dim=int(record["dim"]),
            preprocess_version=record["preprocess_version"],
        )
        row_id = (existing or {}).get("id") or record.get("id") or str(uuid.uuid4())
        created = (existing or {}).get("created_at") or now_s
        payload = record.get("payload") if isinstance(record.get("payload"), str) else json.dumps(record.get("payload") or {}, ensure_ascii=False)
        execute = 1 if record.get("execute") else 0
        if execute:
            raise ValueError("重建队列禁止置 execute=1：当前只允许 planned 记录，不会执行重嵌入。")
        row = {
            "id": row_id,
            "asset_id": record["asset_id"],
            "profile": record["profile"],
            "model_id": record["model_id"],
            "model_version": record["model_version"],
            "dim": int(record["dim"]),
            "preprocess_version": record["preprocess_version"],
            "status": record.get("status") or "planned",
            "execute": 0,
            "payload": payload,
            "created_at": created,
            "updated_at": now_s,
        }
        self._conn.execute(
            """
            INSERT INTO rebuild_queue(
                id, asset_id, profile, model_id, model_version, dim, preprocess_version,
                status, execute, payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            ON CONFLICT(asset_id, profile, model_id, model_version, dim, preprocess_version)
            DO UPDATE SET
                status=excluded.status,
                execute=0,
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            (
                row["id"], row["asset_id"], row["profile"], row["model_id"], row["model_version"],
                row["dim"], row["preprocess_version"], row["status"], row["payload"],
                row["created_at"], row["updated_at"],
            ),
        )
        self._conn.commit()
        return row

    def get_rebuild_item(
        self,
        asset_id: str,
        *,
        profile: str,
        model_id: str,
        model_version: str,
        dim: int,
        preprocess_version: str,
    ) -> dict[str, Any] | None:
        row = self._conn.execute(
            """
            SELECT * FROM rebuild_queue
            WHERE asset_id = ? AND profile = ? AND model_id = ? AND model_version = ?
              AND dim = ? AND preprocess_version = ?
            """,
            (asset_id, profile, model_id, model_version, int(dim), preprocess_version),
        ).fetchone()
        return dict(row) if row else None

    def list_rebuild_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM rebuild_queue WHERE status = ? ORDER BY created_at",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM rebuild_queue ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]



    def get_user(self) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM users ORDER BY created_at ASC LIMIT 1").fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None

    def create_user(self, user_id: str, username: str, password_hash: str, token_version: int = 1) -> dict[str, Any]:
        ts = iso()
        self._conn.execute(
            "INSERT INTO users(id, username, password_hash, token_version, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, password_hash, int(token_version), ts, ts),
        )
        self._conn.commit()
        user = self.get_user_by_username(username)
        assert user is not None
        return user

    def update_user(self, user_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {"username", "password_hash", "token_version"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            user = self.get_user()
            assert user is not None
            return user
        updates["updated_at"] = iso()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(f"UPDATE users SET {assignments} WHERE id = ?", (*updates.values(), user_id))
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        assert row is not None
        return dict(row)

    def get_auth_runtime(self) -> dict[str, Any]:
        row = self._conn.execute("SELECT * FROM auth_runtime WHERE id = 1").fetchone()
        if row:
            return dict(row)
        ts = iso()
        self._conn.execute(
            "INSERT OR IGNORE INTO auth_runtime(id, enabled, signing_key_source, signing_key, token_version, expire_seconds, updated_at) VALUES (1, 0, 'unset', NULL, 1, 86400, ?)",
            (ts,),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM auth_runtime WHERE id = 1").fetchone()
        assert row is not None
        return dict(row)

    def update_auth_runtime(self, **fields: Any) -> dict[str, Any]:
        allowed = {"enabled", "signing_key_source", "signing_key", "token_version", "expire_seconds"}
        current = self.get_auth_runtime()
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "enabled":
                value = int(bool(value))
            updates[key] = value
        if not updates:
            return current
        updates["updated_at"] = iso()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(f"UPDATE auth_runtime SET {assignments} WHERE id = 1", tuple(updates.values()))
        self._conn.commit()
        return self.get_auth_runtime()

    def add_api_token(self, token_id: str, name: str, token_hash: str, token_prefix: str) -> dict[str, Any]:
        ts = iso()
        self._conn.execute(
            """
            INSERT INTO api_tokens(id, name, token_hash, token_prefix, created_at, revoked_at)
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (token_id, name, token_hash, token_prefix, ts),
        )
        self._conn.commit()
        row = self._conn.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
        assert row is not None
        return dict(row)

    def list_api_tokens(self, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        if include_revoked:
            rows = self._conn.execute("SELECT * FROM api_tokens ORDER BY created_at DESC").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM api_tokens WHERE revoked_at IS NULL ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_api_token(self, token_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
        return dict(row) if row else None

    def find_api_token_by_prefix(self, token_prefix: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM api_tokens WHERE token_prefix = ? AND revoked_at IS NULL",
            (token_prefix,),
        ).fetchall()
        return [dict(row) for row in rows]

    def revoke_api_token(self, token_id: str) -> dict[str, Any]:
        row = self.get_api_token(token_id)
        if row is None:
            raise NotFoundError("API Token 不存在")
        if row.get("revoked_at"):
            return row
        ts = iso()
        self._conn.execute("UPDATE api_tokens SET revoked_at = ? WHERE id = ?", (ts, token_id))
        self._conn.commit()
        updated = self.get_api_token(token_id)
        assert updated is not None
        return updated

    # ---- People & Faces (Migration v13) ----

    def _face_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["bbox"] = json.loads(item.pop("bbox_json", "[]") or "[]")
        return item

    def _person_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["hidden"] = bool(item.get("hidden"))
        item["locked"] = bool(item.get("locked"))
        return item

    def insert_face(self, record: dict[str, Any]) -> dict[str, Any]:
        face_id = str(record.get("id") or uuid.uuid4())
        ts = record.get("created_at") or iso()
        bbox_json = json.dumps(list(record.get("bbox") or []))
        emb_blob = record.get("embedding_blob")
        if emb_blob is None and record.get("embedding") is not None:
            emb_blob = encode_vector_blob(record["embedding"])
        self._conn.execute(
            """
            INSERT OR REPLACE INTO faces(
                id, asset_id, frame_index, time_seconds, bbox_json, det_score,
                crop_key, embedding_blob, person_id, collection, remote_vector_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                face_id,
                str(record["asset_id"]),
                int(record.get("frame_index") or 0),
                float(record.get("time_seconds") or 0.0),
                bbox_json,
                float(record.get("det_score") or 0.0),
                record.get("crop_key"),
                bytes(emb_blob or b""),
                record.get("person_id"),
                str(record.get("collection") or ""),
                str(record.get("remote_vector_id") or ""),
                ts,
            ),
        )
        self._conn.commit()
        saved = self.get_face(face_id)
        assert saved is not None
        return saved

    def get_face(self, face_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM faces WHERE id = ?", (face_id,)).fetchone()
        return self._face_from_row(row) if row else None

    def list_faces_by_asset(self, asset_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM faces WHERE asset_id = ? ORDER BY frame_index ASC, det_score DESC",
            (asset_id,),
        ).fetchall()
        return [self._face_from_row(r) for r in rows]

    def list_faces_by_person(self, person_id: str, *, exclude_excluded_assets: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT f.* FROM faces f JOIN assets a ON a.id = f.asset_id WHERE f.person_id = ?"
        if exclude_excluded_assets:
            sql += " AND COALESCE(a.excluded, 0) = 0"
        sql += " ORDER BY f.det_score DESC, f.created_at ASC"
        rows = self._conn.execute(sql, (person_id,)).fetchall()
        return [self._face_from_row(r) for r in rows]

    def list_all_faces(self, *, exclude_excluded_assets: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT f.* FROM faces f JOIN assets a ON a.id = f.asset_id"
        if exclude_excluded_assets:
            sql += " WHERE COALESCE(a.excluded, 0) = 0"
        sql += " ORDER BY f.created_at ASC"
        rows = self._conn.execute(sql).fetchall()
        return [self._face_from_row(r) for r in rows]

    def update_face_person(self, face_id: str, person_id: str | None) -> dict[str, Any] | None:
        self._conn.execute("UPDATE faces SET person_id = ? WHERE id = ?", (person_id, face_id))
        self._conn.commit()
        return self.get_face(face_id)

    def delete_faces_by_asset(self, asset_id: str) -> list[dict[str, Any]]:
        existing = self.list_faces_by_asset(asset_id)
        if not existing:
            return []
        affected_people = {str(f["person_id"]) for f in existing if f.get("person_id")}
        self._conn.execute("DELETE FROM faces WHERE asset_id = ?", (asset_id,))
        self._conn.commit()
        for pid in affected_people:
            self.recompute_person_stats(pid)
        return existing

    def count_faces(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS cnt FROM faces f JOIN assets a ON a.id = f.asset_id WHERE COALESCE(a.excluded, 0) = 0"
        ).fetchone()
        return int(row["cnt"]) if row else 0

    def create_person(
        self,
        *,
        person_id: str | None = None,
        name: str | None = None,
        cover_face_id: str | None = None,
        centroid_blob: bytes | None = None,
        face_count: int = 0,
        hidden: bool = False,
        locked: bool = False,
    ) -> dict[str, Any]:
        pid = person_id or str(uuid.uuid4())
        ts = iso()
        self._conn.execute(
            """
            INSERT INTO people(
                id, name, cover_face_id, centroid_blob, face_count, hidden, locked, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pid,
                name,
                cover_face_id,
                centroid_blob,
                int(face_count),
                1 if hidden else 0,
                1 if locked else 0,
                ts,
                ts,
            ),
        )
        self._conn.commit()
        created = self.get_person(pid)
        assert created is not None
        return created

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        return self._person_from_row(row) if row else None

    def list_people(
        self,
        *,
        include_hidden: bool = False,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["face_count > 0"]
        params: list[Any] = []
        if not include_hidden:
            clauses.append("hidden = 0")
        if q and q.strip():
            clauses.append("IFNULL(name, '') LIKE ? ESCAPE char(92)")
            params.append(self._like_contains(q.strip()))
        where = "WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(
            f"""
            SELECT * FROM people
            {where}
            ORDER BY CASE WHEN IFNULL(name, '') != '' THEN 0 ELSE 1 END ASC, face_count DESC, created_at ASC
            LIMIT ? OFFSET ?
            """,
            (*params, int(limit), int(offset)),
        ).fetchall()
        return [self._person_from_row(r) for r in rows]

    def count_people(self, *, include_hidden: bool = False, q: str | None = None) -> int:
        clauses = ["face_count > 0"]
        params: list[Any] = []
        if not include_hidden:
            clauses.append("hidden = 0")
        if q and q.strip():
            clauses.append("IFNULL(name, '') LIKE ? ESCAPE char(92)")
            params.append(self._like_contains(q.strip()))
        where = "WHERE " + " AND ".join(clauses)
        row = self._conn.execute(f"SELECT COUNT(*) AS cnt FROM people {where}", tuple(params)).fetchone()
        return int(row["cnt"]) if row else 0

    def update_person(self, person_id: str, **fields: Any) -> dict[str, Any] | None:
        if self.get_person(person_id) is None:
            return None
        updates: dict[str, Any] = {}
        for col in ("name", "cover_face_id", "centroid_blob", "face_count"):
            if col in fields:
                updates[col] = fields[col]
        if "hidden" in fields:
            updates["hidden"] = 1 if fields["hidden"] else 0
        if "locked" in fields:
            updates["locked"] = 1 if fields["locked"] else 0
        if not updates:
            return self.get_person(person_id)
        updates["updated_at"] = iso()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(f"UPDATE people SET {assignments} WHERE id = ?", (*updates.values(), person_id))
        self._conn.commit()
        return self.get_person(person_id)

    def delete_person(self, person_id: str) -> None:
        self._conn.execute("UPDATE faces SET person_id = NULL WHERE person_id = ?", (person_id,))
        self._conn.execute("DELETE FROM people WHERE id = ?", (person_id,))
        self._conn.commit()

    def recompute_person_stats(self, person_id: str) -> dict[str, Any] | None:
        person = self.get_person(person_id)
        if person is None:
            return None
        faces = self.list_faces_by_person(person_id, exclude_excluded_assets=True)
        if not faces:
            if not person.get("locked") and not (person.get("name") or "").strip():
                self.delete_person(person_id)
                return None
            return self.update_person(person_id, face_count=0, centroid_blob=None, cover_face_id=None)
        face_ids = {f["id"] for f in faces}
        cover_id = person.get("cover_face_id")
        if not cover_id or cover_id not in face_ids:
            cover_id = faces[0]["id"]
        vecs = [decode_vector_blob(f.get("embedding_blob")) for f in faces]
        centroid = average_normalized_vectors([v for v in vecs if v])
        centroid_blob = encode_vector_blob(centroid) if centroid else None
        return self.update_person(
            person_id,
            face_count=len(faces),
            cover_face_id=cover_id,
            centroid_blob=centroid_blob,
        )

    def list_asset_ids_by_person(self, person_id: str) -> list[str]:
        rows = self._conn.execute(
            """
            SELECT DISTINCT f.asset_id
            FROM faces f
            JOIN assets a ON a.id = f.asset_id
            WHERE f.person_id = ? AND COALESCE(a.excluded, 0) = 0
            ORDER BY a.updated_at DESC
            """,
            (person_id,),
        ).fetchall()
        return [str(r["asset_id"]) for r in rows]

    def count_distinct_assets_by_person(self, person_id: str) -> int:
        row = self._conn.execute(
            """
            SELECT COUNT(DISTINCT f.asset_id) AS cnt
            FROM faces f
            JOIN assets a ON a.id = f.asset_id
            WHERE f.person_id = ? AND COALESCE(a.excluded, 0) = 0
            """,
            (person_id,),
        ).fetchone()
        return int(row["cnt"]) if row else 0

    def list_people_for_asset(self, asset_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT p.*, COUNT(f.id) AS matched_faces, MAX(f.det_score) AS max_det_score,
                   (SELECT f2.id FROM faces f2 WHERE f2.asset_id = ? AND f2.person_id = p.id ORDER BY f2.det_score DESC LIMIT 1) AS asset_face_id,
                   (SELECT f2.time_seconds FROM faces f2 WHERE f2.asset_id = ? AND f2.person_id = p.id ORDER BY f2.det_score DESC LIMIT 1) AS best_time_seconds
            FROM faces f
            JOIN people p ON p.id = f.person_id
            WHERE f.asset_id = ?
            GROUP BY p.id
            ORDER BY matched_faces DESC, max_det_score DESC
            """,
            (asset_id, asset_id, asset_id),
        ).fetchall()
        out = []
        for r in rows:
            item = self._person_from_row(r)
            out.append(item)
        return out

    def asset_has_person_named(self, asset_id: str, name: str, exclude_person_id: str | None = None) -> bool:
        target = (name or "").strip()
        if not target:
            return False
        sql = """
            SELECT 1 FROM faces f
            JOIN people p ON p.id = f.person_id
            WHERE f.asset_id = ? AND TRIM(IFNULL(p.name, '')) = ?
        """
        params: list[Any] = [asset_id, target]
        if exclude_person_id:
            sql += " AND p.id != ?"
            params.append(exclude_person_id)
        sql += " LIMIT 1"
        return self._conn.execute(sql, tuple(params)).fetchone() is not None


EMBED_LANE_STAGES = (
    "embed_pending",
    "embedding",
    "encoding",
    "embed",
    "writing",
    "associating",
    "associated",
    "activated",
)


class SqliteTaskRepository:


    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._claim_lock = threading.Lock()

    def add_job(self, job: Job) -> Job:
        ts = iso()
        self._conn.execute(
            """
            INSERT INTO jobs(
                id, job_type, status, source_id, asset_id, stage, step, progress,
                queue_position, error_code, error_message, checkpoint_json, payload_json,
                lease_until, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.job_type,
                job.status,
                job.source_id,
                job.asset_id,
                job.stage,
                job.step,
                job.progress,
                job.queue_position,
                job.error_code,
                job.error_message,
                json.dumps(job.checkpoint, ensure_ascii=False),
                json.dumps(job.payload, ensure_ascii=False),
                job.lease_until.isoformat() if job.lease_until else None,
                ts,
                ts,
            ),
        )
        self._conn.commit()
        saved = self.get_job(job.id)
        assert saved is not None
        return saved

    def list_jobs(self, status: JobStatus | None = None) -> list[Job]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY created_at ASC",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        jobs = [_job_from_row(r) for r in rows]
        positions = self.waiting_positions()
        for job in jobs:
            if job.status == "waiting":
                job.queue_position = positions.get(job.id)
        return jobs

    def list_jobs_by_batch(self, batch_id: str) -> list[Job]:
        rows = self._conn.execute(
            """
            SELECT * FROM jobs
            WHERE json_extract(payload_json, '$.batch_id') = ?
            ORDER BY created_at ASC
            """,
            (batch_id,),
        ).fetchall()
        jobs = [_job_from_row(r) for r in rows]
        positions = self.waiting_positions()
        for job in jobs:
            if job.status == "waiting":
                job.queue_position = positions.get(job.id)
        return jobs

    def get_job(self, job_id: str) -> Job | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        job = _job_from_row(row)
        if job.status == "waiting":
            job.queue_position = self.waiting_positions().get(job.id)
        return job

    def update_job(self, job_id: str, **fields: Any) -> Job:
        if self.get_job(job_id) is None:
            raise NotFoundError("任务不存在")
        mapping = {
            "job_type": "job_type",
            "status": "status",
            "source_id": "source_id",
            "asset_id": "asset_id",
            "stage": "stage",
            "step": "step",
            "progress": "progress",
            "queue_position": "queue_position",
            "error_code": "error_code",
            "error_message": "error_message",
            "lease_until": "lease_until",
        }
        updates: dict[str, Any] = {}
        for key, column in mapping.items():
            if key in fields:
                value = fields[key]
                if key == "lease_until" and hasattr(value, "isoformat"):
                    value = value.isoformat()
                updates[column] = value
        if "checkpoint" in fields:
            updates["checkpoint_json"] = json.dumps(fields["checkpoint"], ensure_ascii=False)
        if "payload" in fields:
            updates["payload_json"] = json.dumps(fields["payload"], ensure_ascii=False)
        updates["updated_at"] = iso()
        assignments = ", ".join(f"{k} = ?" for k in updates)
        self._conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*updates.values(), job_id))
        self._conn.commit()
        job = self.get_job(job_id)
        assert job is not None
        return job

    def claim_next(self, worker_id: str, lease_seconds: int, *, lane: str | None = None) -> Job | None:
        with self._claim_lock:
            now_s = iso()
            base = "(status = 'waiting' OR (status = 'running' AND (lease_until IS NULL OR lease_until < ?)))"
            params: list[Any] = [now_s]
            marks = ", ".join("?" for _ in EMBED_LANE_STAGES)
            if lane == "preprocess":
                lane_clause = (
                    " AND job_type != 'reindex_profile'"
                    f" AND NOT (job_type IN ('index_asset', 'index_subtitles') AND COALESCE(stage, '') IN ({marks}))"
                )
                params.extend(EMBED_LANE_STAGES)
                order_by = "ORDER BY CASE WHEN job_type = 'vector_purge' THEN 0 ELSE 1 END ASC, created_at ASC"
            elif lane == "embed":
                lane_clause = (
                    " AND (job_type = 'reindex_profile'"
                    f" OR (job_type IN ('index_asset', 'index_subtitles') AND COALESCE(stage, '') IN ({marks})))"
                )
                params.extend(EMBED_LANE_STAGES)
                order_by = "ORDER BY created_at ASC"
            else:
                lane_clause = ""
                order_by = "ORDER BY CASE WHEN job_type = 'vector_purge' THEN 0 ELSE 1 END ASC, created_at ASC"
            row = self._conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE {base}{lane_clause}
                {order_by}
                LIMIT 1
                """,
                params,
            ).fetchone()
            if not row:
                return None
            job = _job_from_row(row)
            from datetime import timedelta

            lease = now_dt = __import__("app.infra.clock", fromlist=["now"]).now()
            lease = now_dt + timedelta(seconds=lease_seconds)
            checkpoint = dict(job.checkpoint)
            checkpoint["worker_id"] = worker_id
            self.update_job(
                job.id,
                status="running",
                lease_until=lease,
                checkpoint=checkpoint,
            )
            return self.get_job(job.id)

    def waiting_positions(self) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT id FROM jobs WHERE status = 'waiting' ORDER BY created_at ASC"
        ).fetchall()
        return {row["id"]: index + 1 for index, row in enumerate(rows)}

    def delete_job(self, job_id: str) -> None:
        cur = self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError("任务不存在")

    def get_flag(self, key: str) -> str:
        row = self._conn.execute("SELECT value FROM runtime_flags WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else "0"

    def set_flag(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO runtime_flags(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def find_active_jobs(self, job_type: str, source_id: str | None = None, asset_id: str | None = None) -> list[Job]:
        sql = "SELECT * FROM jobs WHERE job_type = ? AND status IN ('waiting', 'running', 'paused')"
        params: list[Any] = [job_type]
        if source_id:
            sql += " AND source_id = ?"
            params.append(source_id)
        if asset_id:
            sql += " AND asset_id = ?"
            params.append(asset_id)
        rows = self._conn.execute(sql, params).fetchall()
        return [_job_from_row(r) for r in rows]

