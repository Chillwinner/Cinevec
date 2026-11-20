import { AnimatePresence, motion } from "motion/react";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpRight,
  ArrowsClockwise,
  Check,
  Eye,
  EyeSlash,
  FilmStrip,
  GitMerge,
  ImageSquare,
  MagnifyingGlass,
  PencilSimple,
  SpinnerGap,
  User,
  Users,
  X,
} from "@phosphor-icons/react";
import { api, authorizedMediaUrl } from "../api";
import ScreeningRoom, { type ScreeningTarget } from "../cinema/ScreeningRoom";
import { Banner, Empty, PageHead, Pill } from "../components/ui";
import { useI18n } from "../i18n";
import { EASE } from "../motion";
import type { FaceStatus, PersonAssetItem, PersonAssetsResponse, PersonSummary } from "../types";

function personItemToTarget(item: PersonAssetItem): ScreeningTarget {
  const cover = item.preview_locator || item.cover_locator || `/api/previews/${item.asset_id}/frames/0`;
  return {
    assetId: item.asset_id,
    kind: item.kind,
    title: item.display_name || item.object_key.split("/").pop() || item.object_key,
    coverLocator: cover,
    videoCoverLocator: item.video_cover_locator || item.cover_locator || cover,
    description: item.description ?? "",
    sourceUrl: item.source_url ?? null,
    storeOriginal: item.origin ? item.origin === "hosted" : undefined,
    startSeconds: item.best_time_seconds,
    objectKey: item.object_key,
    tags: item.tags ?? [],
    previews: [
      {
        frame_index: item.best_frame_index,
        time_seconds: item.best_time_seconds,
        locator: cover,
      },
    ],
  };
}

export default function PeoplePage() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("id");

  const [faceStatus, setFaceStatus] = useState<FaceStatus | null>(null);
  const [people, setPeople] = useState<PersonSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [detail, setDetail] = useState<PersonAssetsResponse | null>(null);

  const [searchQ, setSearchQ] = useState("");
  const [includeHidden, setIncludeHidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [nameDraft, setNameDraft] = useState("");

  const [mergeMode, setMergeMode] = useState(false);
  const [mergeIds, setMergeIds] = useState<string[]>([]);

  const [detailNameDraft, setDetailNameDraft] = useState("");
  const [screening, setScreening] = useState<ScreeningTarget | null>(null);

  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const [statusData, peopleData] = await Promise.all([
        api.status(),
        api.people({
          includeHidden,
          q: searchQ.trim() || undefined,
          limit: 200,
          offset: 0,
        }),
      ]);
      setFaceStatus(statusData.face_recognition ?? null);
      setPeople(peopleData.items || []);
      setTotal(peopleData.total || 0);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Failed to load people", "人物相册加载失败"));
    } finally {
      setLoading(false);
    }
  }, [includeHidden, searchQ, t]);

  const loadDetail = useCallback(
    async (pid: string) => {
      setLoading(true);
      try {
        const [statusData, peopleData, detailData] = await Promise.all([
          api.status(),
          api.people({ includeHidden: true, limit: 200, offset: 0 }),
          api.personAssets(pid),
        ]);
        setFaceStatus(statusData.face_recognition ?? null);
        setPeople(peopleData.items || []);
        setDetail(detailData);
        setDetailNameDraft(detailData.person.name || "");
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : t("Failed to load person details", "人物详情加载失败"));
        setDetail(null);
      } finally {
        setLoading(false);
      }
    },
    [t],
  );

  useEffect(() => {
    if (selectedId) {
      void loadDetail(selectedId);
    } else {
      setDetail(null);
      void loadList();
    }
  }, [selectedId, loadList, loadDetail]);

  async function savePersonName(personId: string, nextName: string) {
    setBusy(true);
    setError(null);
    try {
      const cleaned = nextName.trim();
      const updated = await api.updatePerson(personId, { name: cleaned || null });
      setEditingId(null);
      setNotice(
        cleaned
          ? t(`Saved name "${cleaned}" and synced asset tags.`, `已保存名称「${cleaned}」，并自动同步至关联资产标签。`)
          : t("Cleared person name.", "已清除人物名称。"),
      );
      if (selectedId === personId) {
        await loadDetail(personId);
      } else {
        setPeople((prev) => prev.map((p) => (p.id === personId ? updated : p)));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Failed to rename person", "重命名失败"));
    } finally {
      setBusy(false);
    }
  }

  async function toggleHide(person: PersonSummary) {
    setBusy(true);
    setError(null);
    try {
      await api.updatePerson(person.id, { hidden: !person.hidden });
      setNotice(
        !person.hidden
          ? t(`Hidden "${person.display_name}".`, `已隐藏「${person.display_name}」。`)
          : t(`Restored "${person.display_name}".`, `已取消隐藏「${person.display_name}」。`),
      );
      if (selectedId === person.id) {
        await loadDetail(person.id);
      } else {
        await loadList();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Update failed", "操作失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleMergeSelected() {
    if (mergeIds.length < 2) return;
    const [targetId, ...sourceIds] = mergeIds;
    setBusy(true);
    setError(null);
    try {
      const res = await api.mergePeople(targetId, sourceIds);
      setMergeMode(false);
      setMergeIds([]);
      setNotice(
        t(
          `Merged ${sourceIds.length + 1} people into "${res.person.display_name}" (${res.merged_faces} faces moved).`,
          `已合并 ${sourceIds.length + 1} 个人物至「${res.person.display_name}」（共迁移 ${res.merged_faces} 张人脸）。`,
        ),
      );
      await loadList();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Merge failed", "合并失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleBackfill() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.backfillFaces(false);
      setNotice(
        t(
          `Backfill finished: ${String(res.processed_assets ?? 0)} assets scanned, ${String(res.total_faces ?? 0)} faces indexed.`,
          `回填完成：处理 ${String(res.processed_assets ?? 0)} 个资产，共索引 ${String(res.total_faces ?? 0)} 张人脸。`,
        ),
      );
      if (selectedId) await loadDetail(selectedId);
      else await loadList();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Backfill failed", "回填失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleRecluster() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.reclusterFaces();
      setNotice(
        t(
          `Reclustering finished: ${String(res.reassigned_faces ?? 0)} faces reassigned across ${String(res.people_count ?? 0)} clusters.`,
          `重聚类完成：重新分配 ${String(res.reassigned_faces ?? 0)} 张人脸，当前共 ${String(res.people_count ?? 0)} 个簇。`,
        ),
      );
      if (selectedId) await loadDetail(selectedId);
      else await loadList();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Recluster failed", "重聚类失败"));
    } finally {
      setBusy(false);
    }
  }

  async function handleReassignFace(faceId: string, targetValue: string) {
    if (!targetValue) return;
    setBusy(true);
    setError(null);
    try {
      const body =
        targetValue === "__new__"
          ? { new_person_name: null }
          : { target_person_id: targetValue };
      const res = await api.reassignFace(faceId, body);
      setNotice(
        t(
          `Reassigned face to "${res.person.display_name}".`,
          `已将人脸迁移至「${res.person.display_name}」。`,
        ),
      );
      if (selectedId) await loadDetail(selectedId);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Reassign failed", "迁移失败"));
    } finally {
      setBusy(false);
    }
  }

  function onSearchSubmit(event: FormEvent) {
    event.preventDefault();
    void loadList();
  }

  function toggleMergeCard(id: string) {
    setMergeIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  }

  return (
    <div>
      <PageHead
        eyebrow="PEOPLE ALBUM"
        title={t("People", "人物相册")}
        desc={t(
          "Auto-clustered faces across your library. Naming a person automatically writes tags to their media for Lexical and CineGram search.",
          "自动聚类资料库中的人脸。给人物起名后会自动同步写入关联媒体的标签，网页词法搜与 CineGram /m 均可直接搜到。",
        )}
        actions={
          <div className="people-head-actions">
            {faceStatus ? (
              <Pill status={faceStatus.enabled ? (faceStatus.available ? "ok" : "warn") : "unconfigured"}>
                {faceStatus.enabled
                  ? `${faceStatus.people_count} ${t("people", "个人物")} · ${faceStatus.faces_count} ${t("faces", "张脸")}`
                  : t("Face Recognition Off", "人脸识别未开启")}
              </Pill>
            ) : null}
            <button type="button" className="btn" disabled={busy} onClick={() => void handleRecluster()}>
              <ArrowsClockwise size={14} />
              {t("Recluster", "重新聚类")}
            </button>
            <button
              type="button"
              className="btn primary"
              disabled={busy || !faceStatus?.enabled}
              onClick={() => void handleBackfill()}
            >
              {busy ? <SpinnerGap size={14} className="spin" /> : <Users size={14} />}
              {t("Backfill Faces", "扫描已有媒体")}
            </button>
          </div>
        }
      />

      {faceStatus && !faceStatus.enabled ? (
        <Banner kind="info" title={t("Face recognition is disabled by default", "人脸识别默认关闭")}>
          <span>
            {t(
              "Enable it in Settings or set PICA_FACE_ENABLED=true to auto-cluster faces and search by portrait.",
              "请先在「设置 → 人脸识别」开启，或配置 PICA_FACE_ENABLED=true，开启后新入库媒体会自动归类人脸并开放以脸搜人。",
            )}
          </span>{" "}
          <Link className="people-inline-link" to="/settings">
            {t("Open Settings", "去设置开启")} <ArrowUpRight size={13} />
          </Link>
        </Banner>
      ) : null}

      <AnimatePresence>
        {error ? (
          <motion.div key="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Banner kind="danger" title={t("Action failed", "操作未完成")}>{error}</Banner>
          </motion.div>
        ) : null}
        {notice ? (
          <motion.div key="notice" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Banner kind="info" title={t("Updated", "已更新")}>{notice}</Banner>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {selectedId && detail ? (
        <section className="people-detail">
          <div className="people-detail-bar">
            <button
              type="button"
              className="btn"
              onClick={() => {
                setParams({});
              }}
            >
              <ArrowLeft size={14} />
              {t("All People", "返回全部人物")}
            </button>

            <button type="button" className="btn" disabled={busy} onClick={() => void toggleHide(detail.person)}>
              {detail.person.hidden ? <Eye size={14} /> : <EyeSlash size={14} />}
              {detail.person.hidden ? t("Unhide Person", "取消隐藏") : t("Hide Person", "隐藏该人物")}
            </button>
          </div>

          <div className="person-detail-head">
            <div className="person-avatar lg">
              {detail.person.avatar_url ? (
                <img src={authorizedMediaUrl(detail.person.avatar_url)} alt={detail.person.display_name} />
              ) : (
                <User size={34} />
              )}
            </div>
            <div className="person-detail-copy">
              <div className="person-detail-meta">
                <span className="eyebrow">PERSON PROFILE</span>
                <span>
                  {detail.person.asset_count} {t("media", "媒体")} · {detail.person.face_count} {t("faces", "张人脸")}
                </span>
              </div>
              <form
                className="person-rename-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  void savePersonName(detail.person.id, detailNameDraft);
                }}
              >
                <input
                  className="input"
                  value={detailNameDraft}
                  onChange={(e) => setDetailNameDraft(e.target.value)}
                  placeholder={t("Enter person name (auto-syncs tags)...", "输入人名（自动同步为媒体标签）...")}
                  disabled={busy}
                />
                <button type="submit" className="btn primary" disabled={busy}>
                  <Check size={14} />
                  {t("Save Name & Sync Tags", "保存人名并同步标签")}
                </button>
              </form>
            </div>
          </div>

          {detail.items.length === 0 ? (
            <Empty title={t("No active media for this person", "该人物暂无可展示的媒体")}>
              {t("Associated media may have been excluded.", "关联媒体可能已被移出检索库。")}
            </Empty>
          ) : (
            <div className="hit-grid">
              {detail.items.map((item) => {
                const thumb = item.preview_locator || item.cover_locator || `/api/previews/${item.asset_id}/frames/0`;
                const otherPeople = people.filter((p) => p.id !== detail.person.id);
                return (
                  <article className="hit-card" key={item.asset_id}>
                    <button
                      type="button"
                      className="hit-media"
                      onClick={() => setScreening(personItemToTarget(item))}
                    >
                      <img src={authorizedMediaUrl(thumb)} alt="" loading="lazy" />
                      <span className="hit-kind">
                        {item.kind === "video" ? <FilmStrip size={13} /> : <ImageSquare size={13} />}
                        {item.kind === "video" ? t("Video", "视频") : t("Image", "图片")}
                      </span>
                      {item.kind === "video" ? (
                        <span className="hit-time">{item.best_time_seconds.toFixed(1)}s</span>
                      ) : null}
                      <span className="hit-open">
                        <ArrowUpRight size={15} />
                      </span>
                    </button>

                    <div className="hit-body">
                      <strong className="hit-name">{item.display_name || item.object_key}</strong>
                      <p className="hit-meta">
                        <span>{t("Matched faces:", "命中人脸帧:")} {item.face_count}</span>
                      </p>

                      {item.tags.length > 0 ? (
                        <div className="hit-tags">
                          {item.tags.map((tag) => (
                            <span className="tag-chip" key={tag}>{tag}</span>
                          ))}
                        </div>
                      ) : null}

                      <div className="person-face-strip">
                        {item.faces.map((face) => (
                          <div className="person-face-item" key={face.id}>
                            <img src={authorizedMediaUrl(face.crop_url)} alt="" />
                            <select
                              aria-label={t("Reassign face", "迁移此人脸")}
                              defaultValue=""
                              disabled={busy}
                              onChange={(e) => {
                                const val = e.target.value;
                                e.target.value = "";
                                void handleReassignFace(face.id, val);
                              }}
                            >
                              <option value="" disabled>
                                {t("Move face...", "纠偏迁移...")}
                              </option>
                              <option value="__new__">{t("+ Split into new person", "+ 拆分为新人物")}</option>
                              {otherPeople.map((p) => (
                                <option key={p.id} value={p.id}>
                                  {p.display_name}
                                </option>
                              ))}
                            </select>
                          </div>
                        ))}
                      </div>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>
      ) : (
        <section>
          <div className="people-toolbar">
            <form className="people-search-form" onSubmit={onSearchSubmit}>
              <div className="people-search-box">
                <MagnifyingGlass size={15} />
                <input
                  value={searchQ}
                  onChange={(e) => setSearchQ(e.target.value)}
                  placeholder={t("Search people by name...", "按人名筛选...")}
                />
                {searchQ ? (
                  <button
                    type="button"
                    className="dock-icon-btn"
                    onClick={() => setSearchQ("")}
                    aria-label={t("Clear input", "清空输入")}
                  >
                    <X size={13} />
                  </button>
                ) : null}
              </div>
              <button type="submit" className="btn">{t("Filter", "筛选")}</button>
            </form>

            <div className="people-toolbar-right">
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={includeHidden}
                  onChange={(e) => setIncludeHidden(e.target.checked)}
                />
                <span>{t("Show hidden", "显示已隐藏")}</span>
              </label>

              <button
                type="button"
                className={`btn${mergeMode ? " primary" : ""}`}
                onClick={() => {
                  setMergeMode((prev) => !prev);
                  setMergeIds([]);
                }}
              >
                <GitMerge size={14} />
                {mergeMode ? t("Cancel Merge", "退出合并") : t("Merge People", "合并人物")}
              </button>

              {mergeMode && mergeIds.length >= 2 ? (
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy}
                  onClick={() => void handleMergeSelected()}
                >
                  <Check size={14} />
                  {t("Merge Selected", "合并已选")} ({mergeIds.length})
                </button>
              ) : null}
            </div>
          </div>

          {mergeMode ? (
            <Banner kind="info" title={t("Merge Mode", "多选合并模式")}>
              {t(
                "Click 2 or more person cards to merge them into the first selected person. Names and asset tags will sync automatically.",
                "依次点选 2 个及以上人物，将合并至第一个选中的人物名下，并自动同步对应标签。",
              )}
            </Banner>
          ) : null}

          {loading ? (
            <div className="stage-center-state" style={{ minHeight: 220 }}>
              <SpinnerGap size={26} className="spin" />
            </div>
          ) : people.length === 0 ? (
            <Empty
              title={t("No people clustered yet", "还没有人物聚类结果")}
              action={
                <button
                  type="button"
                  className="btn primary"
                  disabled={busy || !faceStatus?.enabled}
                  onClick={() => void handleBackfill()}
                >
                  {t("Scan Existing Media", "扫描已有媒体人脸")}
                </button>
              }
            >
              {t(
                "Turn on Face Recognition in Settings and index or backfill media with faces to see clustered people here.",
                "开启人脸识别并对包含人物的照片/视频执行「扫描已有媒体」后，自动聚类的人物相册会出现在这里。",
              )}
            </Empty>
          ) : (
            <div className="people-grid">
              {people.map((person, index) => {
                const mergePos = mergeIds.indexOf(person.id);
                const selectedForMerge = mergePos !== -1;
                const isEditing = editingId === person.id;
                return (
                  <motion.article
                    key={person.id}
                    className={`person-card${selectedForMerge ? " selected" : ""}${person.hidden ? " is-hidden" : ""}`}
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.35, ease: EASE, delay: Math.min(index * 0.025, 0.25) }}
                    onClick={() => {
                      if (isEditing) return;
                      if (mergeMode) {
                        toggleMergeCard(person.id);
                      } else {
                        setParams({ id: person.id });
                      }
                    }}
                  >
                    <div className="person-avatar">
                      {person.avatar_url ? (
                        <img src={authorizedMediaUrl(person.avatar_url)} alt={person.display_name} loading="lazy" />
                      ) : (
                        <User size={28} />
                      )}
                      {mergeMode && selectedForMerge ? (
                        <span className="person-merge-badge">
                          {mergePos === 0 ? t("Target", "主") : mergePos + 1}
                        </span>
                      ) : null}
                    </div>

                    <div className="person-card-body">
                      {isEditing ? (
                        <form
                          className="person-inline-edit"
                          onClick={(e) => e.stopPropagation()}
                          onSubmit={(e) => {
                            e.preventDefault();
                            void savePersonName(person.id, nameDraft);
                          }}
                        >
                          <input
                            autoFocus
                            value={nameDraft}
                            onChange={(e) => setNameDraft(e.target.value)}
                            placeholder={t("Person name...", "输入姓名...")}
                            disabled={busy}
                          />
                          <button type="submit" className="dock-icon-btn" disabled={busy} aria-label={t("Save", "保存")}>
                            <Check size={13} />
                          </button>
                          <button
                            type="button"
                            className="dock-icon-btn"
                            onClick={() => setEditingId(null)}
                            aria-label={t("Cancel", "取消")}
                          >
                            <X size={13} />
                          </button>
                        </form>
                      ) : (
                        <>
                          <strong className="person-name">{person.display_name}</strong>
                          <span className="person-counts">
                            {person.asset_count} {t("media", "媒体")} · {person.face_count} {t("faces", "脸")}
                          </span>
                        </>
                      )}
                    </div>

                    {!mergeMode && !isEditing ? (
                      <div className="person-card-actions" onClick={(e) => e.stopPropagation()}>
                        <button
                          type="button"
                          className="dock-icon-btn"
                          title={t("Rename person", "起名 / 重命名")}
                          aria-label={t("Rename person", "起名 / 重命名")}
                          onClick={() => {
                            setEditingId(person.id);
                            setNameDraft(person.name || "");
                          }}
                        >
                          <PencilSimple size={14} />
                        </button>
                        <button
                          type="button"
                          className="dock-icon-btn"
                          title={person.hidden ? t("Unhide", "取消隐藏") : t("Hide", "隐藏")}
                          aria-label={person.hidden ? t("Unhide", "取消隐藏") : t("Hide", "隐藏")}
                          onClick={() => void toggleHide(person)}
                        >
                          {person.hidden ? <Eye size={14} /> : <EyeSlash size={14} />}
                        </button>
                      </div>
                    ) : null}
                  </motion.article>
                );
              })}
            </div>
          )}

          {total > people.length ? (
            <p className="hint" style={{ marginTop: 14 }}>
              {t(`Showing ${people.length} of ${total} people.`, `当前展示 ${people.length} / ${total} 个人物。`)}
            </p>
          ) : null}
        </section>
      )}

      <AnimatePresence>
        {screening ? (
          <ScreeningRoom
            key={screening.assetId}
            target={screening}
            onClose={() => setScreening(null)}
          />
        ) : null}
      </AnimatePresence>
    </div>
  );
}
