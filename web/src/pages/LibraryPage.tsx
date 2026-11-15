import { AnimatePresence, motion } from "motion/react";
import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  ArrowUpRight,
  CaretRight,
  Check,
  FilmStrip,
  ImageSquare,
  MagnifyingGlass,
  SpinnerGap,
  X,
} from "@phosphor-icons/react";
import { api, authorizedMediaUrl } from "../api";
import PreviewLightbox, { type PreviewTarget } from "../components/PreviewLightbox";
import { AnimatedNumber, Banner, Empty, PageHead, Pill, Portal, Segmented } from "../components/ui";
import { EASE, softSpring, spring } from "../motion";
import type { Asset, Association, FrameRecord, Source } from "../types";

type Filter = "all" | "image" | "video" | "pending" | "excluded";

function titleOf(asset: Asset) {
  return asset.display_name || asset.object_key.split("/").pop() || asset.object_key;
}

function originLabel(asset: Asset) {
  if (asset.origin === "hosted") return "电脑上传托管";
  if (asset.source_url) return "外部网址引用";
  return "网盘引用";
}

const PAGE = 48;

export default function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const selectedId = params.get("id");
  const [assets, setAssets] = useState<Asset[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [sources, setSources] = useState<Source[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [tag, setTag] = useState("");
  const [descDraft, setDescDraft] = useState("");
  const [batchTag, setBatchTag] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [frames, setFrames] = useState<FrameRecord[]>([]);
  const [assocs, setAssocs] = useState<Association[]>([]);
  const [subtitleId, setSubtitleId] = useState("");
  const [subs, setSubs] = useState<Asset[]>([]);
  const [preview, setPreview] = useState<PreviewTarget | null>(null);

  function filterOpts(nextOffset = 0) {
    return {
      kind: filter === "image" || filter === "video" ? filter : undefined,
      pending: filter === "pending" ? true : undefined,
      excluded: filter === "excluded",
      q: query.trim() || undefined,
      limit: PAGE,
      offset: nextOffset,
    };
  }

  async function load(nextOffset = 0, append = false) {
    setLoading(true);
    try {
      const [assetData, sourceData] = await Promise.all([api.assets(filterOpts(nextOffset)), api.sources()]);
      setAssets((prev) => (append ? [...prev, ...assetData.items] : assetData.items));
      setTotal(assetData.total);
      setOffset(nextOffset);
      setSources(sourceData.items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "资料库读不出来");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setSelectedIds([]);
    void load(0, false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  const hasPurgingAssets = useMemo(
    () => assets.some((a) => a.excluded && (a.vector_purge_status === "pending" || a.artifact_purge_status === "pending")),
    [assets],
  );

  useEffect(() => {
    if (!hasPurgingAssets) return;
    const timer = window.setTimeout(() => {
      void load(offset, false);
    }, 1500);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasPurgingAssets, assets, offset]);

  const selected = useMemo(() => assets.find((a) => a.id === selectedId) ?? null, [assets, selectedId]);

  useEffect(() => {
    setDescDraft(selected?.description || "");
  }, [selected?.id, selected?.description]);

  useEffect(() => {
    if (!selected) {
      setFrames([]);
      setAssocs([]);
      return;
    }
    void api.frames(selected.id).then((data) => setFrames(data.items)).catch(() => setFrames([]));
    if (selected.kind === "video") {
      void api.associations(selected.id).then((data) => setAssocs(data.items)).catch(() => setAssocs([]));
      void api.assets({ kind: "subtitle", limit: 200 }).then((data) => setSubs(data.items)).catch(() => setSubs([]));
    } else {
      setAssocs([]);
    }
  }, [selected?.id]);

  function open(id: string) {
    setParams({ id });
    setTag("");
  }

  function toggleSelect(id: string) {
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  }

  async function act(kind: "exclude" | "restore" | "vectorize", id: string) {
    setBusy(true);
    setNotice(null);
    try {
      if (kind === "exclude") {
        await api.excludeAsset(id);
        setNotice("已排除。会先删向量，成功后再清预览和平台上的原片。网盘里的原文件不会动。");
      } else if (kind === "restore") {
        await api.restoreAsset(id);
        setNotice("已恢复。");
      } else {
        await api.enqueueVectorize(id);
        setNotice("已加入检索队列。进度在「仪表盘」。");
      }
      await load(0, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function runBatch(action: "tag" | "exclude" | "vectorize") {
    if (selectedIds.length === 0) return;
    if (action === "tag" && !batchTag.trim()) return;
    setBusy(true);
    try {
      await api.batchAssets(selectedIds, action, action === "tag" ? batchTag.trim() : undefined);
      setNotice(action === "tag" ? "已打上标签。" : action === "exclude" ? "已排除所选，正在删向量并清理平台文件。" : "已加入检索队列。");
      setSelectedIds([]);
      await load(0, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "批量操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function addTag(event: FormEvent) {
    event.preventDefault();
    if (!selected || !tag.trim()) return;
    setBusy(true);
    try {
      await api.addTag(selected.id, tag.trim());
      setTag("");
      await load(offset, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "标签没加上");
    } finally {
      setBusy(false);
    }
  }

  async function saveDescription() {
    if (!selected) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.updateAsset(selected.id, { description: descDraft });
      setNotice("描述已保存。");
      await load(offset, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "描述保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function uploadSelectedCover(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!selected || !file) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.uploadCover(selected.id, file);
      setNotice("封面已更新。");
      await load(offset, false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "封面更新失败");
    } finally {
      setBusy(false);
    }
  }

  async function bindSubtitle() {
    if (!selected || !subtitleId) return;
    setBusy(true);
    try {
      await api.associateSubtitle(selected.id, subtitleId);
      setNotice("已绑定字幕。解析进度在「仪表盘」。");
      const data = await api.associations(selected.id);
      setAssocs(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "字幕没绑上");
    } finally {
      setBusy(false);
    }
  }

  const sourceName = (id: string) => sources.find((s) => s.id === id)?.name;
  const coverOf = (asset: Asset) => (asset.cover_locator ? authorizedMediaUrl(asset.cover_locator) : "");
  const visibleTiles = assets.filter((asset) => asset.kind !== "subtitle");

  return (
    <div>
      <PageHead
        eyebrow="LIBRARY"
        title="资料库"
        desc="浏览已经登记的照片和视频。排除会先删向量，再清预览和平台托管原片；网盘原文件不会删。"
        actions={
          <div className="stat-block">
            <strong>{total > 0 ? <AnimatedNumber value={total} /> : "0"}</strong>
            <span>件已登记</span>
          </div>
        }
      />

      <AnimatePresence>
        {error ? (
          <motion.div key="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Banner kind="danger" title="没做成">{error}</Banner>
          </motion.div>
        ) : null}
        {notice ? (
          <motion.div key="notice" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Banner kind="info" title="好了">{notice}</Banner>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div className="lib-toolbar">
        <Segmented
          id="library-filter"
          ariaLabel="资料筛选"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "all", label: "全部" },
            { value: "image", label: "图片" },
            { value: "video", label: "视频" },
            { value: "pending", label: "未处理完" },
            { value: "excluded", label: "已排除" },
          ]}
        />
        <form className="lib-search" onSubmit={(event) => { event.preventDefault(); void load(0, false); }}>
          <MagnifyingGlass size={16} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="文件名或标签包含"
            aria-label="筛选文件名或标签"
          />
          <button type="submit" className="icon-btn" aria-label="筛选">
            <ArrowUpRight size={15} />
          </button>
        </form>
      </div>

      <Portal>
        <AnimatePresence>
          {selectedIds.length ? (
            <motion.div
              key="batch"
              className="batch-bar"
            initial={{ opacity: 0, y: 28, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 18, scale: 0.98 }}
            transition={softSpring}
          >
            <span className="batch-count">已选 {selectedIds.length}</span>
            <input
              className="input batch-input"
              value={batchTag}
              onChange={(event) => setBatchTag(event.target.value)}
              placeholder="统一标签"
            />
            <div className="batch-actions">
              <button type="button" className="btn" disabled={busy || !batchTag.trim()} onClick={() => void runBatch("tag")}>打标签</button>
              <button type="button" className="btn" disabled={busy} onClick={() => void runBatch("exclude")}>排除</button>
              <button type="button" className="btn primary" disabled={busy} onClick={() => void runBatch("vectorize")}>加入检索</button>
              <button type="button" className="icon-btn" onClick={() => setSelectedIds([])} aria-label="取消选择">
                <X size={16} />
              </button>
            </div>
          </motion.div>
          ) : null}
        </AnimatePresence>
      </Portal>

      {loading && visibleTiles.length === 0 ? (
        <div className="library-grid">
          {Array.from({ length: 10 }).map((_, index) => (
            <div className="lib-tile skeleton-card" key={index}>
              <div className="skeleton skeleton-tile" />
            </div>
          ))}
        </div>
      ) : visibleTiles.length === 0 ? (
        <Empty title="这里还是空的" action={<Link className="btn primary" to="/">去搜索页添加</Link>}>
          添加之后会出现缩略图。字幕文件不会单独占一格，会绑在视频上。
        </Empty>
      ) : (
        <>
          <motion.div className="library-grid" layout>
            <AnimatePresence mode="popLayout">
              {visibleTiles.map((asset, index) => {
                const checked = selectedIds.includes(asset.id);
                return (
                  <motion.article
                    key={asset.id}
                    layout
                    className={`lib-tile${selectedId === asset.id ? " is-open" : ""}${checked ? " is-checked" : ""}${asset.excluded ? " is-excluded" : ""}`}
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{
                      opacity: 1,
                      scale: 1,
                      transition: { delay: Math.min(index * 0.03, 0.36), duration: 0.5, ease: EASE },
                    }}
                    exit={{ opacity: 0, scale: 0.95, transition: { duration: 0.16 } }}
                    transition={{ layout: spring }}
                  >
                    <button type="button" className="lib-hit" onClick={() => open(asset.id)}>
                      <div className="lib-media">
                        {asset.cover_locator ? (
                          <img src={coverOf(asset)} alt="" loading="lazy" />
                        ) : (
                          <span className="lib-fallback">
                            {asset.kind === "video" ? <FilmStrip size={22} /> : <ImageSquare size={22} />}
                            {asset.excluded
                              ? asset.vector_purge_status === "pending" || asset.artifact_purge_status === "pending"
                                ? "清理中"
                                : "已清理"
                              : asset.cover_status === "pending"
                                ? "还在处理"
                                : asset.kind === "video"
                                  ? "视频"
                                  : "无预览"}
                          </span>
                        )}
                        <span className="lib-shade" />
                        <span className="lib-kind">
                          {asset.kind === "video" ? <FilmStrip size={12} /> : <ImageSquare size={12} />}
                          {asset.kind === "video" ? "视频" : "图片"}
                        </span>
                        {asset.excluded ? <span className="lib-badge">已排除</span> : null}
                        {asset.cover_status === "pending" && asset.cover_locator ? <span className="lib-badge">还在处理</span> : null}
                        <span className="lib-cap">
                          <span>{titleOf(asset)}</span>
                          <CaretRight size={14} />
                        </span>
                      </div>
                    </button>
                    <button
                      type="button"
                      className={`lib-check${checked ? " on" : ""}`}
                      aria-label={`选择 ${titleOf(asset)}`}
                      onClick={() => toggleSelect(asset.id)}
                    >
                      <AnimatePresence initial={false}>
                        {checked ? (
                          <motion.span
                            key="check"
                            initial={{ scale: 0.4, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.4, opacity: 0 }}
                            transition={spring}
                          >
                            <Check size={12} weight="bold" />
                          </motion.span>
                        ) : null}
                      </AnimatePresence>
                    </button>
                  </motion.article>
                );
              })}
            </AnimatePresence>
          </motion.div>

          {assets.length < total ? (
            <div className="load-more">
              <button type="button" className="btn" disabled={loading} onClick={() => void load(offset + PAGE, true)}>
                {loading ? <SpinnerGap size={15} className="spin" /> : null}
                加载更多
                <span className="load-count">{assets.length} / {total}</span>
              </button>
            </div>
          ) : null}
        </>
      )}

      <Portal>
        <AnimatePresence>
          {selected ? (
            <motion.div
              key="drawer"
              className="drawer-back"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }}
            onClick={() => setParams({})}
          >
            <motion.aside
              className="drawer"
              initial={{ x: "104%", opacity: 0.7 }}
              animate={{ x: 0, opacity: 1 }}
              exit={{ x: "104%", opacity: 0.7 }}
              transition={softSpring}
              onClick={(event) => event.stopPropagation()}
            >
              <header className="drawer-head">
                <div>
                  <p className="eyebrow">
                    {selected.kind === "video" ? "VIDEO" : "IMAGE"} · {originLabel(selected)}
                  </p>
                  <h3>{titleOf(selected)}</h3>
                </div>
                <button type="button" className="icon-btn" onClick={() => setParams({})} aria-label="关闭">
                  <X size={17} />
                </button>
              </header>

              <div className="drawer-status">
                <Pill status={selected.excluded ? "warn" : selected.index_state === "ready" ? "ok" : "info"}>
                  {selected.lifecycle_label || (selected.excluded ? "已排除" : selected.vectorize ? "检索处理中或已就绪" : "仅登记")}
                </Pill>
                <span className="hint">
                  {sourceName(selected.source_id) ? `${sourceName(selected.source_id)}` : "本机"}
                  {selected.source_deleted ? " · 原文件现在找不到" : ""}
                </span>
              </div>
              {selected.excluded ? (
                <p className="hint" style={{ marginTop: -8 }}>
                  {selected.vector_purge_status === "failed" || selected.artifact_purge_status === "failed"
                    ? selected.vector_purge_message || selected.artifact_purge_message || "清理失败"
                    : selected.vector_purge_status === "pending" || selected.artifact_purge_status === "pending"
                      ? "正在后台清理向量与预览…"
                      : selected.fully_deleted
                        ? "向量与预览已清理完成。"
                        : selected.vector_purge_message || "向量已清理。"}
                </p>
              ) : null}

              {frames.filter((frame) => frame.kept).length ? (
                <div className="frame-strip">
                  {frames.filter((frame) => frame.kept).slice(0, 8).map((frame) => (
                    <motion.img
                      key={frame.index}
                      src={authorizedMediaUrl(`/api/previews/${selected.id}/frames/${frame.index}`)}
                      alt=""
                      loading="lazy"
                      initial={{ opacity: 0, scale: 0.96 }}
                      animate={{ opacity: 1, scale: 1 }}
                      transition={{ duration: 0.35, ease: EASE }}
                    />
                  ))}
                </div>
              ) : null}

              <section className="drawer-section">
                <h4>封面与描述</h4>
                {selected.video_cover_locator || selected.cover_locator ? (
                  <img
                    src={authorizedMediaUrl(selected.video_cover_locator || selected.cover_locator || "")}
                    alt={`${titleOf(selected)} 封面`}
                    style={{ width: "100%", maxHeight: 180, objectFit: "cover", borderRadius: 10, marginBottom: 10 }}
                  />
                ) : null}
                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 10, flexWrap: "wrap" }}>
                  <label className="btn ghost" style={{ cursor: "pointer" }}>
                    <input type="file" accept="image/*" hidden disabled={busy} onChange={(e) => void uploadSelectedCover(e)} />
                    更换封面
                  </label>
                  {selected.source_url ? (
                    <a className="btn ghost" href={selected.source_url} target="_blank" rel="noreferrer">
                      打开原网页
                      <ArrowUpRight size={13} />
                    </a>
                  ) : null}
                </div>
                <textarea
                  className="input"
                  rows={4}
                  value={descDraft}
                  placeholder="补充图片或视频描述（支持元数据文本检索）"
                  onChange={(event) => setDescDraft(event.target.value)}
                  style={{
                    width: "100%",
                    height: "auto",
                    minHeight: 96,
                    padding: "10px 12px",
                    lineHeight: 1.6,
                    resize: "vertical",
                    marginBottom: 8,
                  }}
                />
                <button className="btn" type="button" disabled={busy} onClick={() => void saveDescription()}>保存描述</button>
              </section>

              <section className="drawer-section">
                <h4>标签</h4>
                <div className="chip-row">
                  {(selected.tags || []).map((item) => (
                    <button
                      key={item}
                      type="button"
                      className="chip"
                      disabled={busy}
                      onClick={() => void api.removeTag(selected.id, item).then(() => load(offset, false))}
                    >
                      {item}
                      <X size={11} />
                    </button>
                  ))}
                  {!selected.tags?.length ? <span className="hint">还没有标签。</span> : null}
                </div>
                <form className="chip-form" onSubmit={(event) => void addTag(event)}>
                  <input className="input" value={tag} onChange={(event) => setTag(event.target.value)} placeholder="新标签" />
                  <button className="btn" type="submit" disabled={busy}>加上</button>
                </form>
              </section>

              {selected.kind === "video" ? (
                <section className="drawer-section">
                  <h4>字幕</h4>
                  {assocs.length === 0 ? (
                    <p className="hint">同名字幕只作为建议，仍要你点选。不会自动绑 ASS。</p>
                  ) : (
                    assocs.map((item) => (
                      <div key={item.id} className="assoc-row">
                        <span>{item.subtitle_key?.split("/").pop()}</span>
                        <button
                          type="button"
                          className="btn ghost"
                          onClick={() => void api.deleteAssociation(item.id).then(() => api.associations(selected.id).then((data) => setAssocs(data.items)))}
                        >
                          解开
                        </button>
                      </div>
                    ))
                  )}
                  {subs.length > 0 ? (
                    <div className="chip-form">
                      <select className="input" value={subtitleId} onChange={(event) => setSubtitleId(event.target.value)}>
                        <option value="">从已有字幕里选</option>
                        {subs.map((sub) => (
                          <option key={sub.id} value={sub.id}>{titleOf(sub)}</option>
                        ))}
                      </select>
                      <button type="button" className="btn" disabled={busy || !subtitleId} onClick={() => void bindSubtitle()}>绑定</button>
                    </div>
                  ) : (
                    <p className="hint">还没有字幕文件。可到搜索页右上角添加，或稍后绑定。</p>
                  )}
                </section>
              ) : null}

              <footer className="drawer-foot">
                <button
                  type="button"
                  className="btn primary"
                  onClick={() => setPreview({
                    assetId: selected.id,
                    kind: selected.kind,
                    title: titleOf(selected),
                    coverLocator: selected.cover_locator,
                    sourceMissing: Boolean(selected.source_deleted),
                  })}
                >
                  预览
                </button>
                {selected.excluded ? (
                  selected.can_restore === false ? (
                    <p className="hint">平台原片已删，不能从资料库恢复，需要重新上传。</p>
                  ) : (
                    <button type="button" className="btn primary" disabled={busy} onClick={() => void act("restore", selected.id)}>恢复</button>
                  )
                ) : (
                  <button
                    type="button"
                    className="btn danger ghost"
                    disabled={busy}
                    onClick={() => {
                      const hosted = selected.origin === "hosted";
                      const ok = window.confirm(
                        hosted
                          ? "排除后会先删向量，再删预览和平台上的原片，不能再从资料库恢复。确定？"
                          : "排除后会先删向量，再删预览。网盘里的原文件还在，之后可以恢复。确定？",
                      );
                      if (ok) void act("exclude", selected.id);
                    }}
                  >
                    排除
                  </button>
                )}
                {!selected.vectorize && !selected.excluded ? (
                  <button type="button" className="btn primary" disabled={busy} onClick={() => void act("vectorize", selected.id)}>加入检索</button>
                ) : null}
              </footer>
            </motion.aside>
          </motion.div>
          ) : null}
        </AnimatePresence>
      </Portal>

      <AnimatePresence>
        {preview ? <PreviewLightbox key="preview" target={preview} onClose={() => setPreview(null)} /> : null}
      </AnimatePresence>
    </div>
  );
}
