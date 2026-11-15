import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { motion } from "motion/react";
import {
  ArrowUpRight,
  FilmStrip,
  ImageSquare,
  MagnifyingGlass,
  Play,
  Plus,
  Sparkle,
  Subtitles,
  Tag,
  X,
} from "@phosphor-icons/react";
import { api, authorizedMediaUrl } from "../api";
import { Portal } from "../components/ui";
import { useI18n } from "../i18n";
import { EASE } from "../motion";
import type { Association, FrameRecord } from "../types";

export type ScreeningTarget = {
  assetId: string;
  kind: string;
  title: string;
  coverLocator?: string | null;
  videoCoverLocator?: string | null;
  description?: string | null;
  sourceUrl?: string | null;
  storeOriginal?: boolean;
  startSeconds?: number | null;
  sourceMissing?: boolean;
  score?: number | null;
  hitSource?: string | null;
  subtitleExcerpt?: string | null;
  objectKey?: string | null;
  collection?: string | null;
  dim?: number | null;
  modelId?: string | null;
  tags?: string[] | null;
  previews?: {
    frame_index: number;
    time_seconds: number | null;
    locator: string;
    score?: number | null;
    near_subtitle?: boolean | string | null;
  }[];
};

const hitMethodMap: Record<string, [string, string]> = {
  visual: ["Visual Vector", "画面向量"],
  subtitle: ["Subtitle Semantic", "字幕语义"],
  mixed: ["Visual + Subtitle", "画面 + 字幕"],
  visual_only: ["Visual Only", "仅画面"],
  subtitle_only: ["Subtitle Only", "仅字幕"],
  visual_and_subtitle: ["Visual + Subtitle", "画面 + 字幕"],
};

function fmtTime(sec: number | null | undefined) {
  if (sec == null || Number.isNaN(sec)) return null;
  const mins = Math.floor(sec / 60);
  const secs = Math.floor(sec % 60);
  const tenths = Math.floor((sec % 1) * 10);
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${tenths}`;
}

export default function ScreeningRoom({
  target,
  onClose,
  onSearchSimilar,
}: {
  target: ScreeningTarget;
  onClose: () => void;
  onSearchSimilar?: (file: File) => void;
}) {
  const { lang, t } = useI18n();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [mediaError, setMediaError] = useState(false);
  const [hdReady, setHdReady] = useState(false);
  const [activeCover, setActiveCover] = useState<string | null>(target.coverLocator ?? null);
  const [videoCover, setVideoCover] = useState<string | null>(target.videoCoverLocator ?? target.coverLocator ?? null);
  const [description, setDescription] = useState<string>(target.description ?? "");
  const [descBusy, setDescBusy] = useState(false);
  const [descNotice, setDescNotice] = useState<string | null>(null);
  const [coverBusy, setCoverBusy] = useState(false);
  const [activeTime, setActiveTime] = useState<number | null>(target.startSeconds ?? null);
  const [frames, setFrames] = useState<FrameRecord[]>([]);
  const [associations, setAssociations] = useState<Association[]>([]);
  const [tags, setTags] = useState<string[]>(target.tags ?? []);
  const [tagInput, setTagInput] = useState("");
  const [tagBusy, setTagBusy] = useState(false);
  const [similarBusy, setSimilarBusy] = useState(false);

  const mediaUrl = authorizedMediaUrl(`/api/assets/${target.assetId}/media`);
  const coverUrl = activeCover ? authorizedMediaUrl(activeCover) : null;
  const videoCoverUrl = videoCover ? authorizedMediaUrl(videoCover) : null;

  useEffect(() => {
    setMediaError(false);
    setHdReady(false);
    setActiveCover(target.coverLocator ?? null);
    setVideoCover(target.videoCoverLocator ?? target.coverLocator ?? null);
    setDescription(target.description ?? "");
    setDescNotice(null);
    setActiveTime(target.startSeconds ?? null);
    setTags(target.tags ?? []);
    setFrames([]);
    setAssociations([]);

    let cancelled = false;
    void api
      .frames(target.assetId)
      .then((res) => {
        if (!cancelled) setFrames(res.items || []);
      })
      .catch(() => undefined);

    if (target.kind === "video") {
      void api
        .associations(target.assetId)
        .then((res) => {
          if (!cancelled) setAssociations(res.items || []);
        })
        .catch(() => undefined);
    }

    return () => {
      cancelled = true;
    };
  }, [target]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function jumpToTime(seconds: number, locator?: string | null) {
    setActiveTime(seconds);
    if (locator) setActiveCover(locator);
    const el = videoRef.current;
    if (el && !mediaError && !target.sourceMissing) {
      el.currentTime = Math.max(0, seconds);
      void el.play().catch(() => undefined);
    }
  }

  function handleVideoLoaded() {
    const el = videoRef.current;
    if (!el || target.startSeconds == null) return;
    if (target.startSeconds > 0 && Number.isFinite(target.startSeconds)) {
      el.currentTime = target.startSeconds;
    }
  }

  async function handleSaveDescription() {
    if (descBusy) return;
    setDescBusy(true);
    setDescNotice(null);
    try {
      const updated = await api.updateAsset(target.assetId, { description });
      setDescription(updated.description ?? description);
      setDescNotice(t("Saved", "已保存"));
    } finally {
      setDescBusy(false);
    }
  }

  async function handleUploadCover(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || coverBusy) return;
    setCoverBusy(true);
    try {
      const updated = await api.uploadCover(target.assetId, file);
      setVideoCover(updated.video_cover_locator || updated.cover_locator || `/api/assets/${target.assetId}/cover?v=${Date.now()}`);
    } finally {
      setCoverBusy(false);
    }
  }

  async function handleAddTag(event: FormEvent) {
    event.preventDefault();
    const cleaned = tagInput.trim();
    if (!cleaned || tagBusy) return;
    setTagBusy(true);
    try {
      const res = await api.addTag(target.assetId, cleaned);
      setTags(res.items || []);
      setTagInput("");
    } finally {
      setTagBusy(false);
    }
  }

  async function handleRemoveTag(tag: string) {
    if (tagBusy) return;
    setTagBusy(true);
    try {
      const res = await api.removeTag(target.assetId, tag);
      setTags(res.items || []);
    } finally {
      setTagBusy(false);
    }
  }

  async function handleSearchSimilar() {
    if (!onSearchSimilar || similarBusy) return;
    setSimilarBusy(true);
    try {
      const candidateUrl =
        target.kind === "image" && !target.sourceMissing && !mediaError
          ? mediaUrl
          : coverUrl || authorizedMediaUrl(`/api/previews/${target.assetId}/frames/0`);
      const res = await fetch(candidateUrl);
      if (!res.ok) throw new Error("无法读取画面用于相似检索");
      const blob = await res.blob();
      const ext = blob.type.includes("png") ? "png" : "jpg";
      const file = new File([blob], `${target.title}.${ext}`, { type: blob.type || "image/jpeg" });
      onSearchSimilar(file);
    } catch {
      // ignore fetch failures quietly
    } finally {
      setSimilarBusy(false);
    }
  }

  const timelineItems = useMemo(() => {
    const map = new Map<
      number,
      { frameIndex: number; timeSeconds: number; locator: string; score?: number; isHit?: boolean }
    >();
    for (const f of frames) {
      if (!f.kept && !f.preview_key) continue;
      map.set(f.index, {
        frameIndex: f.index,
        timeSeconds: f.time_seconds,
        locator: `/api/previews/${target.assetId}/frames/${f.index}`,
      });
    }
    for (const hit of target.previews || []) {
      map.set(hit.frame_index, {
        frameIndex: hit.frame_index,
        timeSeconds: hit.time_seconds ?? 0,
        locator: hit.locator,
        score: hit.score ?? undefined,
        isHit: true,
      });
    }
    return Array.from(map.values()).sort((a, b) => a.timeSeconds - b.timeSeconds);
  }, [frames, target]);

  const subtitleCues = useMemo(() => {
    const cues: { index: number; start: number; end: number; text: string }[] = [];
    for (const assoc of associations) {
      for (const c of assoc.cues || []) {
        cues.push({
          index: c.cue_index,
          start: c.start_seconds,
          end: c.end_seconds,
          text: c.text,
        });
      }
    }
    return cues.slice(0, 40);
  }, [associations]);

  const noOriginalStored = target.storeOriginal === false;
  const showVideo = target.kind === "video" && !noOriginalStored && !target.sourceMissing && !mediaError;
  const showFullImage = target.kind === "image" && !noOriginalStored && !target.sourceMissing && !mediaError;

  return (
    <Portal>
      <motion.div
        className="screening-backdrop"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.35, ease: EASE }}
        onClick={onClose}
      >
        <motion.div
          className="screening-shell"
          initial={{ opacity: 0, y: 18, scale: 0.985 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 12, scale: 0.985 }}
          transition={{ duration: 0.42, ease: EASE }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* 顶部放映厅控制栏 */}
          <header className="screening-topbar">
            <div className="screening-title-group">
              <span className="screening-badge">
                {target.kind === "video" ? <FilmStrip size={13} /> : <ImageSquare size={13} />}
                {target.kind === "video" ? "VIDEO REEL" : "STILL FRAME"}
              </span>
              <h2 className="screening-title" title={target.title}>
                {target.title}
              </h2>
              {activeTime != null ? (
                <span className="screening-tc">TC {fmtTime(activeTime)}</span>
              ) : null}
            </div>

            <div className="screening-actions">
              {onSearchSimilar ? (
                <button
                  type="button"
                  className="screening-action-btn primary"
                  disabled={similarBusy}
                  onClick={() => void handleSearchSimilar()}
                >
                  <MagnifyingGlass size={14} weight="bold" />
                  <span>{similarBusy ? t("Sampling...", "采样中...") : t("Find Similar", "搜相似画面")}</span>
                </button>
              ) : null}
              <Link className="screening-action-btn" to={`/library?id=${target.assetId}`} onClick={onClose}>
                <span>{t("Open in Library", "资料库详情")}</span>
                <ArrowUpRight size={14} />
              </Link>
              <button type="button" className="screening-close-btn" onClick={onClose} aria-label={t("Close Screening Room", "关闭放映室")}>
                <X size={16} weight="bold" />
              </button>
            </div>
          </header>

          {/* 主放映区 + 右侧导演审片台 */}
          <div className="screening-body">
            <div className="screening-stage">
              <div className="screening-viewport">
                {showVideo ? (
                  <video
                    ref={videoRef}
                    className="screening-media"
                    src={mediaUrl}
                    poster={coverUrl ?? undefined}
                    controls
                    autoPlay
                    playsInline
                    onLoadedMetadata={handleVideoLoaded}
                    onError={() => setMediaError(true)}
                  />
                ) : showFullImage ? (
                  <>
                    {!hdReady && coverUrl ? (
                      <img className="screening-media" src={coverUrl} alt={target.title} />
                    ) : null}
                    <img
                      className="screening-media"
                      style={!hdReady && coverUrl ? { display: "none" } : undefined}
                      src={mediaUrl}
                      alt={target.title}
                      onLoad={() => setHdReady(true)}
                      onError={() => setMediaError(true)}
                    />
                  </>
                ) : coverUrl ? (
                  <div className="screening-fallback-wrap">
                    <img className="screening-media" src={coverUrl} alt={target.title} />
                    <span className="screening-source-note">
                      {noOriginalStored
                        ? t("No original stored (external-link mode) · Showing preview frame", "未保存原文件（外链检索模式）· 正在展示预览帧")
                        : target.sourceMissing
                          ? t("Original file not stored locally · Showing preview frame", "源文件未保存在本地 · 正在放映预览帧")
                          : t("Original cannot be played directly · Switched to preview frame", "原文件无法直接播放 · 已切换预览帧")}
                    </span>
                    {target.sourceUrl ? (
                      <a className="screening-action-btn" href={target.sourceUrl} target="_blank" rel="noreferrer">
                        <span>{t("Open source URL", "打开原网页")}</span>
                        <ArrowUpRight size={14} />
                      </a>
                    ) : null}
                  </div>
                ) : (
                  <div className="screening-empty-media">
                    {target.kind === "video" ? <FilmStrip size={42} /> : <ImageSquare size={42} />}
                    <p>{t("No preview frame available", "暂无可用画面预览")}</p>
                  </div>
                )}
              </div>

              {/* 胶片关键帧时间轴 */}
              {timelineItems.length > 0 ? (
                <div className="screening-filmstrip">
                  <div className="screening-strip-head">
                    <span>{t(`KEYFRAME TIMELINE · ${timelineItems.length} frames`, `KEYFRAME TIMELINE · ${timelineItems.length} 帧`)}</span>
                    {target.kind === "video" ? <span className="strip-hint">{t("Click any frame to jump", "点击任意帧跳转时间点")}</span> : null}
                  </div>
                  <div className="screening-strip-track">
                    {timelineItems.map((frame) => {
                      const active =
                        activeTime != null && Math.abs(frame.timeSeconds - activeTime) < 0.2;
                      return (
                        <button
                          type="button"
                          key={frame.frameIndex}
                          className={`screening-thumb${active ? " active" : ""}${frame.isHit ? " matched" : ""}`}
                          onClick={() => jumpToTime(frame.timeSeconds, frame.locator)}
                          title={`#${frame.frameIndex} · ${fmtTime(frame.timeSeconds)}`}
                        >
                          <img src={authorizedMediaUrl(frame.locator)} alt="" loading="lazy" />
                          <span className="thumb-tc">{fmtTime(frame.timeSeconds)}</span>
                          {frame.isHit ? (
                            <span className="thumb-hit-dot" title="命中关键帧">
                              <Sparkle size={9} weight="fill" />
                            </span>
                          ) : null}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ) : null}
            </div>

            {/* 右侧检视信息面板 */}
            <aside className="screening-inspector">
              <div className="inspector-block">
                <div className="inspector-label">
                  {target.kind === "video" ? t("Video Cover", "视频封面") : t("Asset Cover", "资产封面")}
                </div>
                {videoCoverUrl ? (
                  <div className="inspector-cover-wrap">
                    <img className="inspector-cover-img" src={videoCoverUrl} alt={`${target.title} cover`} />
                  </div>
                ) : (
                  <span className="inspector-empty">{t("No cover image", "暂无封面图")}</span>
                )}
                <label className="inspector-upload-btn">
                  <input
                    type="file"
                    accept="image/*"
                    hidden
                    disabled={coverBusy}
                    onChange={(e) => void handleUploadCover(e)}
                  />
                  <Plus size={12} />
                  {coverBusy ? t("Uploading…", "上传中…") : t("Replace Cover", "更换封面")}
                </label>
              </div>

              <div className="inspector-block">
                <div className="inspector-label">{t("Description & Source", "描述与来源")}</div>
                <textarea
                  className="inspector-textarea"
                  rows={4}
                  value={description}
                  placeholder={t("Add image or video description (searchable via metadata search)", "补充图片或视频描述（支持元数据文本检索）")}
                  onChange={(e) => {
                    setDescription(e.target.value);
                    setDescNotice(null);
                  }}
                />
                <div className="inspector-desc-actions">
                  <button
                    type="button"
                    className="inspector-save-btn"
                    disabled={descBusy}
                    onClick={() => void handleSaveDescription()}
                  >
                    {descBusy ? t("Saving…", "保存中…") : t("Save Description", "保存描述")}
                  </button>
                  {descNotice ? <span className="inspector-saved">{descNotice}</span> : null}
                </div>
                {target.sourceUrl ? (
                  <a className="inspector-source-link" href={target.sourceUrl} target="_blank" rel="noreferrer">
                    <span>{t("Open source", "打开原网页")}: {target.sourceUrl}</span>
                    <ArrowUpRight size={12} />
                  </a>
                ) : null}
              </div>

              {target.score != null ? (
                <div className="inspector-block">
                  <div className="inspector-label">SEMANTIC RELEVANCE</div>
                  <div className="inspector-score-row">
                    <strong>{target.score.toFixed(4)}</strong>
                    <span>
                      {(hitMethodMap[target.hitSource || ""] &&
                        (lang === "zh" ? hitMethodMap[target.hitSource || ""][1] : hitMethodMap[target.hitSource || ""][0])) ||
                        target.hitSource ||
                        t("Vector Search", "向量检索")}
                    </span>
                  </div>
                  <div className="inspector-score-bar">
                    <div
                      className="inspector-score-fill"
                      style={{ width: `${Math.max(4, Math.min(100, target.score * 100))}%` }}
                    />
                  </div>
                </div>
              ) : null}

              {target.subtitleExcerpt ? (
                <div className="inspector-block">
                  <div className="inspector-label">
                    <Subtitles size={13} /> {t("Matched Subtitle Cue", "命中的台词 / 字幕片段")}
                  </div>
                  <blockquote className="inspector-quote">“{target.subtitleExcerpt}”</blockquote>
                </div>
              ) : null}

              <div className="inspector-block">
                <div className="inspector-label">PROVENANCE & VECTOR SPACE</div>
                <dl className="inspector-meta-list">
                  <div>
                    <dt>{t("Asset ID", "资产编号")}</dt>
                    <dd className="mono">{target.assetId.slice(0, 12)}</dd>
                  </div>
                  {target.objectKey ? (
                    <div>
                      <dt>{t("Object Key", "存储对象")}</dt>
                      <dd className="mono break">{target.objectKey}</dd>
                    </div>
                  ) : null}
                  {target.modelId ? (
                    <div>
                      <dt>{t("Vision Model", "视觉模型")}</dt>
                      <dd>{target.modelId}{target.dim ? ` (${target.dim}D)` : ""}</dd>
                    </div>
                  ) : null}
                  {target.collection ? (
                    <div>
                      <dt>{t("Milvus Collection", "Milvus 集合")}</dt>
                      <dd className="mono break">{target.collection}</dd>
                    </div>
                  ) : null}
                </dl>
              </div>

              {/* 标签读写 */}
              <div className="inspector-block">
                <div className="inspector-label">
                  <Tag size={13} /> {t("Tags", "标签档案")}
                </div>
                <div className="inspector-tags">
                  {tags.length ? (
                    tags.map((tag) => (
                      <span className="inspector-tag" key={tag}>
                        <span>{tag}</span>
                        <button
                          type="button"
                          onClick={() => void handleRemoveTag(tag)}
                          disabled={tagBusy}
                          aria-label={t(`Remove tag ${tag}`, `移除标签 ${tag}`)}
                        >
                          <X size={10} />
                        </button>
                      </span>
                    ))
                  ) : (
                    <span className="inspector-empty">{t("No tags", "暂无标签")}</span>
                  )}
                </div>
                <form className="inspector-tag-form" onSubmit={(e) => void handleAddTag(e)}>
                  <input
                    value={tagInput}
                    onChange={(e) => setTagInput(e.target.value)}
                    placeholder={t("Add note / tag...", "添加导演注释 / 标签...")}
                    disabled={tagBusy}
                  />
                  <button type="submit" disabled={tagBusy || !tagInput.trim()} aria-label={t("Add tag", "添加标签")}>
                    <Plus size={13} weight="bold" />
                  </button>
                </form>
              </div>

              {/* 关联字幕台词时间轴 */}
              {subtitleCues.length > 0 ? (
                <div className="inspector-block">
                  <div className="inspector-label">
                    <Play size={13} /> {t("Subtitle Timeline", "关联字幕时间轴")} ({subtitleCues.length})
                  </div>
                  <div className="inspector-cues">
                    {subtitleCues.map((cue) => (
                      <button
                        type="button"
                        key={`${cue.index}-${cue.start}`}
                        className="inspector-cue-item"
                        onClick={() => jumpToTime(cue.start)}
                      >
                        <span className="cue-time">{fmtTime(cue.start)}</span>
                        <span className="cue-text">{cue.text}</span>
                      </button>
                    ))}
                  </div>
                </div>
              ) : null}
            </aside>
          </div>
        </motion.div>
      </motion.div>
    </Portal>
  );
}
