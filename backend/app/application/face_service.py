from __future__ import annotations

import base64
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from app.application.search_aggregate import LIFECYCLE_LABELS, preview_locator
from app.domain.errors import AppError, NotConfiguredError, NotFoundError
from app.domain.face import (
    FACE_DIM,
    FaceSettings,
    cosine_similarity,
    crop_face_jpeg,
    decode_vector_blob,
    encode_vector_blob,
    l2_normalize,
)
from app.domain.lifecycle import VectorLocator
from app.domain.models import asset_source_url
from app.infra.object_keys import display_name_from_key


class FaceService:
    """Opt-in face detection, 512-d vector search, people clustering, and tag sync."""

    def __init__(
        self,
        metadata,
        file_store,
        recognizer,
        face_index=None,
        settings_getter: Callable[[], FaceSettings] | None = None,
    ) -> None:
        self._metadata = metadata
        self._store = file_store
        self._recognizer = recognizer
        self._index = face_index
        self._settings_getter = settings_getter

    def get_settings(self) -> FaceSettings:
        if callable(self._settings_getter):
            return self._settings_getter()
        return FaceSettings()

    def is_enabled(self) -> bool:
        return bool(self.get_settings().enabled)

    def public_status(self) -> dict[str, Any]:
        settings = self.get_settings()
        rec = dict(self._recognizer.public_status()) if self._recognizer is not None else {
            "status": "disabled",
            "enabled": False,
            "available": False,
            "backend": "disabled",
            "model": None,
            "dim": FACE_DIM,
            "message": "人脸识别未装配。",
        }
        rec["enabled"] = bool(settings.enabled)
        rec["cluster_threshold"] = float(settings.cluster_threshold)
        rec["sync_person_tags"] = bool(settings.sync_person_tags)
        rec["people_count"] = self._metadata.count_people(include_hidden=False)
        rec["faces_count"] = self._metadata.count_faces()
        if not settings.enabled:
            rec["status"] = "disabled"
            rec["available"] = False
            rec["message"] = "人脸识别默认关闭，可在「设置 → 人脸识别」或设置 PICA_FACE_ENABLED=true 开启。"
        return rec

    def _require_ready(self) -> FaceSettings:
        settings = self.get_settings()
        if not settings.enabled:
            raise NotConfiguredError("人脸识别当前已关闭，请先在设置中启用（PICA_FACE_ENABLED）。", code="face_disabled")
        if self._recognizer is None or not self._recognizer.available():
            msg = self._recognizer.status_message() if self._recognizer is not None else "人脸识别后端尚未就绪。"
            raise NotConfiguredError(msg, code="face_unavailable")
        return settings

    def _sync_tag_for_asset_name(self, asset_id: str, old_name: str | None, new_name: str | None, *, sync_tags: bool) -> None:
        old_clean = (old_name or "").strip()
        new_clean = (new_name or "").strip()
        if old_clean and old_clean != new_clean:
            if not self._metadata.asset_has_person_named(asset_id, old_clean):
                self._metadata.remove_tag(asset_id, old_clean)
        if new_clean and sync_tags:
            self._metadata.add_tag(asset_id, new_clean)

    def _match_existing_person(
        self,
        embedding: list[float],
        people_cache: list[tuple[dict[str, Any], list[float]]],
        threshold: float,
    ) -> dict[str, Any] | None:
        best_person: dict[str, Any] | None = None
        best_score = -1.0
        for person, centroid in people_cache:
            if not centroid:
                continue
            sim = cosine_similarity(embedding, centroid)
            if sim >= threshold and sim > best_score:
                best_score = sim
                best_person = person
        return best_person

    def _load_people_centroids(self) -> list[tuple[dict[str, Any], list[float]]]:
        all_people = self._metadata.list_people(include_hidden=True, limit=10000, offset=0)
        out: list[tuple[dict[str, Any], list[float]]] = []
        for p in all_people:
            vec = decode_vector_blob(p.get("centroid_blob"))
            if vec:
                out.append((p, vec))
        return out

    def _load_frame_bytes(self, asset: Any, frame: dict[str, Any]) -> bytes | None:
        """Prefer full-resolution source image bytes for static photos before falling back to previews."""
        extra = frame.get("extra") if isinstance(frame.get("extra"), dict) else {}
        if getattr(asset, "kind", None) == "image":
            for local_key in ("local_full_path", "local_path"):
                lp = extra.get(local_key)
                if lp:
                    p = Path(str(lp))
                    if p.is_file():
                        try:
                            data = p.read_bytes()
                            if data:
                                return data
                        except OSError:
                            pass
            obj_key = getattr(asset, "object_key", None)
            if (
                obj_key
                and self._store is not None
                and self._store.available()
                and (
                    getattr(asset, "origin", "") == "hosted"
                    or extra.get("full_frame_source") == "original_object_bytes"
                )
            ):
                try:
                    data = self._store.get_bytes(obj_key)
                    if data:
                        return data
                except Exception:
                    pass
        else:
            lp = extra.get("local_path")
            if lp:
                p = Path(str(lp))
                if p.is_file():
                    try:
                        data = p.read_bytes()
                        if data:
                            return data
                    except OSError:
                        pass

        if self._store is not None and self._store.available():
            candidate_keys = [frame.get("preview_key"), frame.get("model_input_key")]
            if getattr(asset, "kind", None) == "image":
                candidate_keys.append(getattr(asset, "cover_key", None))
            for key in candidate_keys:
                if not key:
                    continue
                try:
                    data = self._store.get_bytes(str(key))
                    if data:
                        return data
                except Exception:
                    continue
        return None

    def index_asset_faces(
        self,
        asset_id: str,
        frame_inputs: list[tuple[int, float, bytes]] | None = None,
        *,
        max_video_frames: int = 16,
    ) -> dict[str, Any]:
        settings = self.get_settings()
        if not settings.enabled or self._recognizer is None or not self._recognizer.available():
            return {"skipped": True, "reason": "disabled_or_unavailable", "faces_indexed": 0}
        asset = self._metadata.get_asset(asset_id)
        if asset is None or getattr(asset, "excluded", False):
            return {"skipped": True, "reason": "asset_missing_or_excluded", "faces_indexed": 0}

        # Clear any stale faces on this asset before re-indexing
        self.purge_asset_faces(asset_id)

        frames_to_scan: list[tuple[int, float, bytes]] = list(frame_inputs or [])
        if not frames_to_scan:
            kept_frames = [f for f in self._metadata.list_frames(asset_id) if f.get("kept")]
            if max_video_frames > 0 and len(kept_frames) > max_video_frames:
                step = (len(kept_frames) - 1) / float(max_video_frames - 1)
                selected_indices = sorted({int(round(i * step)) for i in range(max_video_frames)})
                kept_frames = [kept_frames[i] for i in selected_indices]
            for frame in kept_frames:
                idx = int(frame.get("index") if frame.get("index") is not None else frame.get("frame_index") or 0)
                ts = float(frame.get("time_seconds") or 0.0)
                raw = self._load_frame_bytes(asset, frame)
                if raw:
                    frames_to_scan.append((idx, ts, raw))

        if not frames_to_scan:
            return {"skipped": False, "faces_indexed": 0, "people_touched": 0}

        people_cache = self._load_people_centroids()
        threshold = float(settings.cluster_threshold or 0.40)
        use_index = self._index is not None and self._index.available()
        store_available = self._store is not None and self._store.available()
        collection = ""
        if use_index and hasattr(self._index, "name_for"):
            try:
                collection = str(self._index.name_for("face") or "")
            except Exception:
                collection = ""

        pending_faces: list[dict[str, Any]] = []
        for frame_index, time_seconds, image_bytes in frames_to_scan:
            detected = self._recognizer.detect_and_encode(image_bytes)
            for face_ord, det in enumerate(detected):
                face_id = str(uuid.uuid4())
                crop_key: str | None = None
                crop_bytes: bytes | None = None
                if store_available:
                    try:
                        crop_bytes = det.crop_jpeg or crop_face_jpeg(image_bytes, det.bbox, size=160)
                        crop_key = f"faces/{asset_id}/{face_id}.jpg"
                    except Exception:
                        crop_key = None
                        crop_bytes = None
                pending_faces.append(
                    {
                        "face_id": face_id,
                        "frame_index": int(frame_index),
                        "time_seconds": float(time_seconds),
                        "face_ord": face_ord,
                        "det": det,
                        "crop_key": crop_key,
                        "crop_bytes": crop_bytes,
                    }
                )

        if store_available:
            to_upload = [f for f in pending_faces if f["crop_key"] and f["crop_bytes"]]
            if len(to_upload) > 1:
                def _upload_crop(item: dict[str, Any]) -> None:
                    try:
                        self._store.put_bytes(item["crop_key"], item["crop_bytes"], "image/jpeg")
                    except Exception:
                        item["crop_key"] = None

                with ThreadPoolExecutor(max_workers=min(6, len(to_upload))) as pool:
                    list(pool.map(_upload_crop, to_upload))
            elif len(to_upload) == 1:
                item = to_upload[0]
                try:
                    self._store.put_bytes(item["crop_key"], item["crop_bytes"], "image/jpeg")
                except Exception:
                    item["crop_key"] = None

        inserted = 0
        touched_people: set[str] = set()
        vector_records: list[dict[str, Any]] = []

        for item in pending_faces:
            face_id = item["face_id"]
            det = item["det"]
            crop_key = item["crop_key"]
            frame_index = item["frame_index"]
            time_seconds = item["time_seconds"]
            face_ord = item["face_ord"]

            unit_vec = l2_normalize(det.embedding)
            matched_person = self._match_existing_person(unit_vec, people_cache, threshold)
            if matched_person is None:
                matched_person = self._metadata.create_person(
                    name=None,
                    cover_face_id=face_id,
                    centroid_blob=encode_vector_blob(unit_vec),
                    face_count=1,
                    hidden=False,
                    locked=False,
                )
                people_cache.append((matched_person, unit_vec))
            person_id = str(matched_person["id"])
            touched_people.add(person_id)

            remote_id = face_id if use_index else ""
            unit_index = int(frame_index) * 100 + face_ord
            self._metadata.insert_face(
                {
                    "id": face_id,
                    "asset_id": asset_id,
                    "frame_index": int(frame_index),
                    "time_seconds": float(time_seconds),
                    "bbox": list(det.bbox),
                    "det_score": float(det.det_score),
                    "crop_key": crop_key,
                    "embedding_blob": encode_vector_blob(unit_vec),
                    "person_id": person_id,
                    "collection": collection if use_index else "",
                    "remote_vector_id": remote_id,
                }
            )
            if use_index:
                vector_records.append(
                    {
                        "id": remote_id,
                        "vector": unit_vec,
                        "asset_id": asset_id,
                        "version": int(getattr(asset, "version", 1) or 1),
                        "unit_index": unit_index,
                        "time_seconds": float(time_seconds),
                        "preview_key": crop_key or "",
                    }
                )
            inserted += 1

        if use_index and vector_records:
            self._index.upsert_records("face", vector_records)

        for pid in touched_people:
            updated = self._metadata.recompute_person_stats(pid)
            if updated:
                # Refresh cache entry for subsequent frames in same batch
                vec = decode_vector_blob(updated.get("centroid_blob"))
                for idx, (cached_p, _) in enumerate(people_cache):
                    if cached_p["id"] == pid:
                        people_cache[idx] = (updated, vec)
                        break
                pname = (updated.get("name") or "").strip()
                if pname and settings.sync_person_tags:
                    self._metadata.add_tag(asset_id, pname)

        return {"skipped": False, "faces_indexed": inserted, "people_touched": len(touched_people)}

    def purge_asset_faces(self, asset_id: str) -> int:
        deleted = self._metadata.delete_faces_by_asset(asset_id)
        if not deleted:
            return 0
        if self._index is not None and self._index.available():
            locators = [
                VectorLocator(
                    collection=str(f.get("collection") or ""),
                    remote_id=str(f.get("remote_vector_id") or ""),
                    modality="face",
                    asset_id=asset_id,
                )
                for f in deleted
                if f.get("collection") and f.get("remote_vector_id")
            ]
            if locators:
                try:
                    self._index.delete_vectors(locators)
                except Exception:
                    pass
        if self._store is not None and self._store.available():
            keys = [str(f["crop_key"]) for f in deleted if f.get("crop_key")]
            if keys:
                try:
                    deleter = getattr(self._store, "delete_objects", None)
                    if callable(deleter):
                        deleter(keys)
                    else:
                        for key in keys:
                            self._store.delete_object(key)
                except Exception:
                    pass
        return len(deleted)

    def _person_out(self, p: dict[str, Any], index_num: int | None = None) -> dict[str, Any]:
        cover_id = p.get("cover_face_id")
        asset_count = self._metadata.count_distinct_assets_by_person(p["id"])
        name = (p.get("name") or "").strip() or None
        fallback_num = index_num if index_num is not None else 1
        return {
            "id": p["id"],
            "name": name,
            "display_name": name or f"人物 #{fallback_num}",
            "cover_face_id": cover_id,
            "avatar_url": f"/api/faces/{cover_id}/crop" if cover_id else None,
            "face_count": int(p.get("face_count") or 0),
            "asset_count": asset_count,
            "hidden": bool(p.get("hidden")),
            "locked": bool(p.get("locked")),
            "created_at": p.get("created_at"),
            "updated_at": p.get("updated_at"),
        }

    def list_people(
        self,
        *,
        include_hidden: bool = False,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        rows = self._metadata.list_people(include_hidden=include_hidden, q=q, limit=limit, offset=offset)
        total = self._metadata.count_people(include_hidden=include_hidden, q=q)
        items = [self._person_out(row, offset + idx + 1) for idx, row in enumerate(rows)]
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_person(self, person_id: str) -> dict[str, Any]:
        person = self._metadata.get_person(person_id)
        if person is None:
            raise NotFoundError("人物不存在")
        return self._person_out(person)

    def get_person_assets(self, person_id: str) -> dict[str, Any]:
        person = self._metadata.get_person(person_id)
        if person is None:
            raise NotFoundError("人物不存在")
        faces = self._metadata.list_faces_by_person(person_id, exclude_excluded_assets=True)
        by_asset: dict[str, list[dict[str, Any]]] = {}
        for f in faces:
            by_asset.setdefault(f["asset_id"], []).append(f)

        items: list[dict[str, Any]] = []
        tags_map = self._metadata.list_tags_for_assets(list(by_asset.keys()))
        for aid, afaces in by_asset.items():
            asset = self._metadata.get_asset(aid)
            if asset is None or getattr(asset, "excluded", False):
                continue
            best_face = max(afaces, key=lambda x: float(x.get("det_score") or 0.0))
            best_ts = float(best_face.get("time_seconds") or 0.0)
            frame_idx = int(best_face.get("frame_index") or 0)
            cover_loc = f"/api/assets/{aid}/cover"
            preview_loc = preview_locator(aid, frame_idx, "frame")
            items.append(
                {
                    "asset_id": aid,
                    "kind": asset.kind,
                    "display_name": display_name_from_key(getattr(asset, "display_name", None) or asset.object_key),
                    "object_key": asset.object_key,
                    "description": getattr(asset, "description", "") or "",
                    "origin": getattr(asset, "origin", "external"),
                    "source_url": asset_source_url(asset),
                    "tags": tags_map.get(aid) or [],
                    "cover_locator": cover_loc,
                    "video_cover_locator": cover_loc,
                    "preview_locator": preview_loc,
                    "best_time_seconds": best_ts,
                    "best_frame_index": frame_idx,
                    "face_count": len(afaces),
                    "faces": [
                        {
                            "id": f["id"],
                            "frame_index": f["frame_index"],
                            "time_seconds": f["time_seconds"],
                            "bbox": f["bbox"],
                            "det_score": f["det_score"],
                            "crop_url": f"/api/faces/{f['id']}/crop",
                        }
                        for f in afaces
                    ],
                }
            )
        return {"person": self._person_out(person), "items": items, "total": len(items)}

    def update_person(
        self,
        person_id: str,
        *,
        name: str | None = None,
        has_name: bool = False,
        hidden: bool | None = None,
        cover_face_id: str | None = None,
    ) -> dict[str, Any]:
        current = self._metadata.get_person(person_id)
        if current is None:
            raise NotFoundError("人物不存在")
        old_name = (current.get("name") or "").strip() or None
        updates: dict[str, Any] = {}
        if has_name:
            cleaned = (name or "").strip() or None
            updates["name"] = cleaned
            if cleaned is not None:
                updates["locked"] = True
        if hidden is not None:
            updates["hidden"] = bool(hidden)
        if cover_face_id is not None:
            face = self._metadata.get_face(cover_face_id)
            if face is None or face.get("person_id") != person_id:
                raise AppError("invalid", "指定的头像不属于该人物。")
            updates["cover_face_id"] = cover_face_id

        updated = self._metadata.update_person(person_id, **updates)
        assert updated is not None
        new_name = (updated.get("name") or "").strip() or None
        if has_name and old_name != new_name:
            sync = bool(self.get_settings().sync_person_tags)
            for aid in self._metadata.list_asset_ids_by_person(person_id):
                self._sync_tag_for_asset_name(aid, old_name, new_name, sync_tags=sync)
        return self._person_out(updated)

    def merge_people(self, target_id: str, source_ids: list[str]) -> dict[str, Any]:
        target = self._metadata.get_person(target_id)
        if target is None:
            raise NotFoundError("目标人物不存在")
        target_name = (target.get("name") or "").strip() or None
        sync = bool(self.get_settings().sync_person_tags)
        moved_count = 0

        seen_sources: list[str] = []
        for sid in source_ids:
            if sid and sid != target_id and sid not in seen_sources:
                seen_sources.append(sid)

        for sid in seen_sources:
            src = self._metadata.get_person(sid)
            if src is None:
                continue
            src_name = (src.get("name") or "").strip() or None
            src_faces = self._metadata.list_faces_by_person(sid, exclude_excluded_assets=False)
            touched_assets = {f["asset_id"] for f in src_faces}
            for f in src_faces:
                self._metadata.update_face_person(f["id"], target_id)
                moved_count += 1
            self._metadata.delete_person(sid)
            if not target_name and src_name:
                # Inherit name if target was unnamed and source was named
                target_name = src_name
                self._metadata.update_person(target_id, name=target_name, locked=True)
            for aid in touched_assets:
                self._sync_tag_for_asset_name(aid, src_name, target_name, sync_tags=sync)

        if target_name and sync:
            for aid in self._metadata.list_asset_ids_by_person(target_id):
                self._metadata.add_tag(aid, target_name)

        self._metadata.update_person(target_id, locked=True)
        updated = self._metadata.recompute_person_stats(target_id) or target
        return {"person": self._person_out(updated), "merged_faces": moved_count}

    def reassign_face(
        self,
        face_id: str,
        *,
        target_person_id: str | None = None,
        new_person_name: str | None = None,
    ) -> dict[str, Any]:
        face = self._metadata.get_face(face_id)
        if face is None:
            raise NotFoundError("人脸记录不存在")
        old_pid = face.get("person_id")
        old_person = self._metadata.get_person(old_pid) if old_pid else None
        old_name = (old_person.get("name") or "").strip() or None if old_person else None

        if target_person_id:
            target = self._metadata.get_person(target_person_id)
            if target is None:
                raise NotFoundError("目标人物不存在")
            new_pid = target["id"]
        else:
            cleaned = (new_person_name or "").strip() or None
            created = self._metadata.create_person(
                name=cleaned,
                cover_face_id=face_id,
                centroid_blob=face.get("embedding_blob"),
                face_count=1,
                locked=True,
            )
            new_pid = created["id"]

        self._metadata.update_face_person(face_id, new_pid)
        if old_pid and old_pid != new_pid:
            self._metadata.recompute_person_stats(old_pid)
        self._metadata.update_person(new_pid, locked=True)
        new_person = self._metadata.recompute_person_stats(new_pid) or self._metadata.get_person(new_pid)
        assert new_person is not None
        new_name = (new_person.get("name") or "").strip() or None

        sync = bool(self.get_settings().sync_person_tags)
        self._sync_tag_for_asset_name(face["asset_id"], old_name, new_name, sync_tags=sync)
        return {"face_id": face_id, "person": self._person_out(new_person)}

    def asset_faces(self, asset_id: str) -> dict[str, Any]:
        asset = self._metadata.get_asset(asset_id)
        if asset is None:
            raise NotFoundError("资源不存在")
        faces = self._metadata.list_faces_by_asset(asset_id)
        people_rows = self._metadata.list_people_for_asset(asset_id)
        people = [
            {
                "id": p["id"],
                "name": (p.get("name") or "").strip() or None,
                "display_name": (p.get("name") or "").strip() or f"人物 #{idx + 1}",
                "avatar_url": f"/api/faces/{p.get('asset_face_id') or p.get('cover_face_id')}/crop"
                if (p.get("asset_face_id") or p.get("cover_face_id"))
                else None,
                "matched_faces": int(p.get("matched_faces") or 1),
                "best_time_seconds": float(p.get("best_time_seconds") or 0.0),
                "hidden": bool(p.get("hidden")),
            }
            for idx, p in enumerate(people_rows)
        ]
        face_items = [
            {
                "id": f["id"],
                "frame_index": f["frame_index"],
                "time_seconds": f["time_seconds"],
                "bbox": f["bbox"],
                "det_score": f["det_score"],
                "person_id": f.get("person_id"),
                "crop_url": f"/api/faces/{f['id']}/crop",
            }
            for f in faces
        ]
        return {"asset_id": asset_id, "people": people, "faces": face_items}

    def crop_bytes(self, face_id: str) -> tuple[bytes, str]:
        face = self._metadata.get_face(face_id)
        if face is None:
            raise NotFoundError("人脸头像不存在")
        crop_key = face.get("crop_key")
        if crop_key and self._store is not None and self._store.available():
            try:
                return self._store.get_bytes(crop_key), "image/jpeg"
            except Exception:
                pass
        # Fallback: extract on-demand from full source / stored frame preview
        frame = self._metadata.get_frame(face["asset_id"], int(face.get("frame_index") or 0))
        asset = self._metadata.get_asset(face["asset_id"])
        if frame and asset is not None:
            raw = self._load_frame_bytes(asset, frame)
            if raw:
                return crop_face_jpeg(raw, face.get("bbox") or [], size=160), "image/jpeg"
        raise NotFoundError("人脸头像文件不可用")

    def recluster_faces(self, threshold: float | None = None) -> dict[str, Any]:
        th = float(threshold if threshold is not None else self.get_settings().cluster_threshold or 0.40)
        all_people = self._metadata.list_people(include_hidden=True, limit=10000, offset=0)
        locked_ids = {p["id"] for p in all_people if p.get("locked") or p.get("hidden") or (p.get("name") or "").strip()}
        anchors: list[tuple[dict[str, Any], list[float]]] = []
        for p in all_people:
            if p["id"] in locked_ids:
                vec = decode_vector_blob(p.get("centroid_blob"))
                if vec:
                    anchors.append((p, vec))

        all_faces = self._metadata.list_all_faces(exclude_excluded_assets=True)
        temp_clusters: list[tuple[dict[str, Any], list[list[float]]]] = []
        reassigned = 0
        sync = bool(self.get_settings().sync_person_tags)

        for face in all_faces:
            pid = face.get("person_id")
            if pid in locked_ids:
                continue
            vec = decode_vector_blob(face.get("embedding_blob"))
            if not vec:
                continue
            matched_anchor = self._match_existing_person(vec, anchors, th)
            if matched_anchor is not None:
                new_pid = matched_anchor["id"]
                if pid != new_pid:
                    self._metadata.update_face_person(face["id"], new_pid)
                    reassigned += 1
                pname = (matched_anchor.get("name") or "").strip()
                if pname and sync:
                    self._metadata.add_tag(face["asset_id"], pname)
                continue

            # Cluster against unlocked dynamic groups
            best_cluster = None
            best_sim = -1.0
            for grp, members in temp_clusters:
                centroid = decode_vector_blob(grp.get("centroid_blob"))
                sim = cosine_similarity(vec, centroid)
                if sim >= th and sim > best_sim:
                    best_sim = sim
                    best_cluster = (grp, members)
            if best_cluster is not None:
                grp, members = best_cluster
                members.append(vec)
                from app.domain.face import average_normalized_vectors

                new_cent = average_normalized_vectors(members)
                if new_cent:
                    grp["centroid_blob"] = encode_vector_blob(new_cent)
                new_pid = grp["id"]
            else:
                new_grp = self._metadata.create_person(
                    name=None,
                    cover_face_id=face["id"],
                    centroid_blob=encode_vector_blob(vec),
                    face_count=1,
                )
                temp_clusters.append((new_grp, [vec]))
                new_pid = new_grp["id"]

            if pid != new_pid:
                self._metadata.update_face_person(face["id"], new_pid)
                reassigned += 1

        # Recompute stats & auto-prune empty unlocked people
        for p in self._metadata.list_people(include_hidden=True, limit=10000, offset=0):
            self._metadata.recompute_person_stats(p["id"])
        for grp, _ in temp_clusters:
            self._metadata.recompute_person_stats(grp["id"])

        return {
            "threshold": th,
            "reassigned_faces": reassigned,
            "people_count": self._metadata.count_people(include_hidden=True),
        }

    def backfill_all_assets(self, *, force: bool = False) -> dict[str, Any]:
        self._require_ready()
        assets = self._metadata.list_assets()
        processed = 0
        total_faces = 0
        for asset in assets:
            if getattr(asset, "excluded", False) or asset.kind not in {"image", "video"}:
                continue
            if not force and self._metadata.list_faces_by_asset(asset.id):
                continue
            res = self.index_asset_faces(asset.id)
            if not res.get("skipped"):
                processed += 1
                total_faces += int(res.get("faces_indexed") or 0)
        return {
            "processed_assets": processed,
            "total_faces": total_faces,
            "people_count": self._metadata.count_people(include_hidden=False),
        }

    def search_by_image(
        self,
        image_bytes: bytes,
        *,
        face_index: int | None = None,
        top_n: int = 20,
        score_threshold: float | None = None,
    ) -> dict[str, Any]:
        settings = self._require_ready()
        if not image_bytes:
            raise AppError("invalid", "请上传包含人脸的图片。")
        detected = self._recognizer.detect_and_encode(image_bytes)
        if not detected:
            return {
                "kind": "face",
                "items": [],
                "detected_faces": [],
                "selected_face_index": None,
                "metric": "cosine",
                "score_meaning": "cosine_similarity",
                "reranker": False,
                "notes": [],
                "message": "未在查询图片中检测到清晰人脸。",
            }

        selected_idx = face_index if (face_index is not None and 0 <= face_index < len(detected)) else 0
        detected_choices = []
        for idx, det in enumerate(detected):
            crop = det.crop_jpeg or crop_face_jpeg(image_bytes, det.bbox, size=128)
            b64 = base64.b64encode(crop).decode("ascii")
            detected_choices.append(
                {
                    "index": idx,
                    "bbox": det.bbox,
                    "det_score": round(float(det.det_score), 4),
                    "crop_data_url": f"data:image/jpeg;base64,{b64}",
                }
            )

        qvec = l2_normalize(detected[selected_idx].embedding)
        limit_n = max(1, min(int(top_n or 20), 200))
        candidate_hits: list[tuple[str, float]] = []

        if self._index is not None and self._index.available():
            try:
                rows = self._index.search("face", qvec, limit=limit_n * 5, offset=0)
                for row in rows:
                    fid = str(row.get("id") or "")
                    sc = float(row.get("score") or 0.0)
                    if fid:
                        candidate_hits.append((fid, sc))
            except Exception:
                candidate_hits = []

        if not candidate_hits:
            for face_row in self._metadata.list_all_faces(exclude_excluded_assets=True):
                vec = decode_vector_blob(face_row.get("embedding_blob"))
                if not vec:
                    continue
                sim = cosine_similarity(qvec, vec)
                candidate_hits.append((face_row["id"], sim))
            candidate_hits.sort(key=lambda item: item[1], reverse=True)

        # Aggregate best matching face per active non-excluded asset
        effective_threshold = (
            float(score_threshold)
            if score_threshold is not None
            else max(0.25, float(settings.cluster_threshold or 0.40) - 0.10)
        )
        best_by_asset: dict[str, dict[str, Any]] = {}
        people_cache: dict[str, dict[str, Any] | None] = {}
        for fid, score in candidate_hits:
            if score < effective_threshold:
                continue
            face_row = self._metadata.get_face(fid)
            if face_row is None:
                continue
            aid = face_row["asset_id"]
            asset = self._metadata.get_asset(aid)
            if asset is None or getattr(asset, "excluded", False):
                continue
            prev_best = best_by_asset.get(aid)
            if prev_best is None or score > prev_best["score"]:
                pid = face_row.get("person_id")
                if pid and pid not in people_cache:
                    people_cache[pid] = self._metadata.get_person(pid)
                person = people_cache.get(pid) if pid else None
                best_by_asset[aid] = {
                    "asset": asset,
                    "face": face_row,
                    "person": person,
                    "score": float(score),
                }

        ranked = sorted(best_by_asset.values(), key=lambda x: x["score"], reverse=True)[:limit_n]
        tags_map = self._metadata.list_tags_for_assets([r["asset"].id for r in ranked])
        items: list[dict[str, Any]] = []
        for r in ranked:
            asset = r["asset"]
            face_row = r["face"]
            person = r["person"]
            aid = asset.id
            frame_idx = int(face_row.get("frame_index") or 0)
            ts = float(face_row.get("time_seconds") or 0.0)
            lifecycle = getattr(asset, "lifecycle", "active")
            source_deleted = bool(getattr(asset, "source_deleted", False))
            available = (not source_deleted) and lifecycle not in {"missing", "source_invalid", "excluded"}
            previews = [
                {
                    "frame_index": frame_idx,
                    "unit_index": frame_idx,
                    "time_seconds": ts,
                    "locator": preview_locator(aid, frame_idx, "frame"),
                    "score": float(r["score"]),
                }
            ]
            # Append up to 4 other kept frames so ScreeningRoom filmstrip works
            kept_frames = [f for f in self._metadata.list_frames(aid) if f.get("kept") and f.get("preview_key")]
            for kf in kept_frames:
                kidx = int(kf.get("index") if kf.get("index") is not None else kf.get("frame_index") or 0)
                if kidx == frame_idx:
                    continue
                previews.append(
                    {
                        "frame_index": kidx,
                        "unit_index": kidx,
                        "time_seconds": float(kf.get("time_seconds") or 0.0),
                        "locator": preview_locator(aid, kidx, "frame"),
                        "score": None,
                    }
                )
                if len(previews) >= 5:
                    break

            person_name = (person.get("name") or "").strip() or None if person else None
            items.append(
                {
                    "asset_id": aid,
                    "kind": asset.kind,
                    "object_key": asset.object_key,
                    "display_name": display_name_from_key(getattr(asset, "display_name", None) or asset.object_key),
                    "description": getattr(asset, "description", "") or "",
                    "origin": getattr(asset, "origin", "external"),
                    "lifecycle": lifecycle,
                    "lifecycle_label": LIFECYCLE_LABELS.get(lifecycle, lifecycle),
                    "visibility": getattr(asset, "visibility", "visible"),
                    "source_available": available,
                    "source_availability": "available" if available else "unavailable",
                    "source_url": asset_source_url(asset),
                    "tags": tags_map.get(aid) or [],
                    "cover_key": getattr(asset, "cover_key", None),
                    "video_cover_locator": f"/api/assets/{aid}/cover",
                    "score": r["score"],
                    "metric": "cosine",
                    "score_meaning": "cosine_similarity",
                    "hit_source": "face",
                    "hit_method": "face",
                    "time_seconds": ts,
                    "unit_index": frame_idx,
                    "previews": previews,
                    "person_id": person["id"] if person else None,
                    "person_name": person_name,
                    "matched_face_id": face_row["id"],
                    "matched_bbox": face_row.get("bbox"),
                    "matched_face_crop_locator": f"/api/faces/{face_row['id']}/crop",
                    "model_id": (self._recognizer.public_status() or {}).get("model") or "buffalo_sc",
                    "dim": FACE_DIM,
                }
            )

        return {
            "kind": "face",
            "metric": "cosine",
            "score_meaning": "cosine_similarity",
            "reranker": False,
            "notes": [],
            "items": items,
            "detected_faces": detected_choices,
            "selected_face_index": selected_idx,
        }
