import { AnimatePresence, motion } from "motion/react";
import { ChangeEvent, DragEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  CaretLeft,
  CaretRight,
  CloudArrowUp,
  FolderOpen,
  SpinnerGap,
  UploadSimple,
  X,
} from "@phosphor-icons/react";
import { api } from "../api";
import { EASE, modal as modalVariants, overlay as overlayVariants, softSpring } from "../motion";
import type { Asset, ObjectEntry, Source } from "../types";
import ConnectDrive, { probeLabel } from "./ConnectDrive";
import { Banner, Empty, Portal } from "./ui";

type Choice = { selected: boolean; vectorize: boolean; subtitle?: string };
type Step = "pick" | "computer" | "drive";

function parentPrefix(value: string) {
  const clean = value.replace(/\/$/, "");
  const idx = clean.lastIndexOf("/");
  return idx >= 0 ? clean.slice(0, idx + 1) : "";
}

function isVideo(key: string) {
  return /\.(mp4|mkv|mov|avi|webm|m4v)$/i.test(key);
}

function isSubtitle(key: string) {
  return /\.(srt|vtt|ass)$/i.test(key);
}

function stem(key: string) {
  return key.replace(/\.[^.]+$/, "").split("/").pop() || key;
}

export default function AddFlow({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [step, setStep] = useState<Step>("pick");
  const [sources, setSources] = useState<Source[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [prefix, setPrefix] = useState("");
  const [entries, setEntries] = useState<ObjectEntry[]>([]);
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [assets, setAssets] = useState<Asset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const [uploadVectorize, setUploadVectorize] = useState(true);
  const [storeOriginal, setStoreOriginal] = useState(true);
  const [uploadSourceUrl, setUploadSourceUrl] = useState("");
  const [uploadDescription, setUploadDescription] = useState("");
  const [uploadSubtitle, setUploadSubtitle] = useState<File | null>(null);
  const [uploadCover, setUploadCover] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [dropActive, setDropActive] = useState(false);
  const [knownKeys, setKnownKeys] = useState<Set<string>>(new Set());

  async function loadSources() {
    const data = await api.sources();
    setSources(data.items);
    if (!sourceId && data.items[0]) setSourceId(data.items[0].id);
  }

  useEffect(() => {
    if (!open) return;
    setStep("pick");
    setError(null);
    setNotice(null);
    setDropActive(false);
    setUploadProgress(null);
    void loadSources().catch((err: Error) => setError(err.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  async function browse(nextPrefix = prefix) {
    if (!sourceId) return;
    setBusy(true);
    setError(null);
    try {
      const data = await api.browse(sourceId, nextPrefix);
      setPrefix(data.prefix);
      setEntries(data.items);
    } catch (err) {
      setEntries([]);
      setError(err instanceof Error ? err.message : "列不出文件，多半是还没连上");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!open || step !== "drive" || !sourceId) return;
    setPrefix("");
    setChoices({});
    void browse("");
    void api.assets({ sourceId, kind: "subtitle", limit: 200 }).then((data) => setAssets(data.items)).catch(() => undefined);
    void api.objectKeys(sourceId).then((data) => setKnownKeys(new Set(data.items))).catch(() => setKnownKeys(new Set()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId, step, open]);

  const selected = useMemo(
    () => entries.filter((item) => !item.is_dir && choices[item.key]?.selected),
    [entries, choices],
  );

  async function probe(id: string) {
    setBusy(true);
    setError(null);
    try {
      const source = await api.probeSource(id);
      await loadSources();
      if (source.last_probe_status === "ok") setNotice("连上了。");
      else setError("地址或密钥不对，没连上。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "没连上");
    } finally {
      setBusy(false);
    }
  }

  async function submitDrive() {
    setBusy(true);
    setNotice(null);
    try {
      const result = await api.imports(
        sourceId,
        selected.map((item) => ({
          object_key: item.key,
          vectorize: Boolean(choices[item.key]?.vectorize),
          subtitle_object_key: choices[item.key]?.subtitle || undefined,
        })),
      );
      setNotice(`已加入 ${result.assets.length} 个文件。要看进度去「仪表盘」。不会改网盘里的原文件。`);
      const listed = await api.assets({ sourceId, kind: "subtitle", limit: 200 });
      setAssets(listed.items);
      const keys = await api.objectKeys(sourceId);
      setKnownKeys(new Set(keys.items));
    } catch (err) {
      setError(err instanceof Error ? err.message : "没加上");
    } finally {
      setBusy(false);
    }
  }

  async function uploadFileList(list: File[]) {
    if (list.length === 0) return;
    const trimmedSourceUrl = uploadSourceUrl.trim();
    if (!storeOriginal && !trimmedSourceUrl) {
      setError("不保存源文件到 S3 时，必须填写来源网址（source_url）。");
      return;
    }
    const trimmedDesc = uploadDescription.trim();
    setBusy(true);
    setError(null);
    setNotice(null);
    setUploadProgress("准备上传…");
    try {
      const files = [...list];
      if (uploadSubtitle && !files.some((file) => file.name === uploadSubtitle.name)) {
        files.push(uploadSubtitle);
      }
      const meta = files.map((file) => ({
        relative_path: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
        vectorize: uploadVectorize && !isSubtitle(file.name),
        subtitle_relative_path: uploadSubtitle && isVideo(file.name) ? uploadSubtitle.name : undefined,
        cover_relative_path: uploadCover && !isSubtitle(file.name) ? uploadCover.name : undefined,
        store_original: storeOriginal,
        source_url: trimmedSourceUrl || undefined,
        description: trimmedDesc || undefined,
      }));
      const result = await api.uploadFiles(
        files,
        meta,
        (loaded, total) => {
          setUploadProgress(`${files.length} 个文件 · ${(loaded / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB`);
        },
        uploadCover,
      );
      setNotice(`${result.message || "已经拷进家里的存放位置。"} 要看进度去「仪表盘」。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
      setUploadProgress(null);
    }
  }

  async function onUpload(event: ChangeEvent<HTMLInputElement>) {
    const list = event.target.files;
    if (!list || list.length === 0) return;
    try {
      await uploadFileList(Array.from(list));
    } finally {
      event.target.value = "";
    }
  }

  function onDropZoneDragOver(event: DragEvent<HTMLDivElement>) {
    if (!event.dataTransfer.types.includes("Files") || busy) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    setDropActive(true);
  }

  function onDropZoneDragLeave(event: DragEvent<HTMLDivElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDropActive(false);
  }

  function onDropZoneDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDropActive(false);
    if (busy) return;
    const files = Array.from(event.dataTransfer.files);
    if (files.length === 0) {
      setError("没有读到可上传的文件。拖文件夹时请尽量用「选择文件夹」，或直接拖文件进来。");
      return;
    }
    void uploadFileList(files);
  }

  const banners = (
    <>
      {error ? <Banner kind="warn" title="这次没做成">{error}</Banner> : null}
      {notice ? (
        <Banner kind="info" title="好了">
          {notice} <Link to="/activity" onClick={onClose}>去仪表盘</Link>
        </Banner>
      ) : null}
    </>
  );

  const computerStep = (
    <div className="add-step">
      <div className="step-heading">
        <span className="panel-icon"><UploadSimple size={18} /></span>
        <div>
          <h3>从这台电脑</h3>
          <p className="hint">选文件或整个文件夹。勾上「加入检索」才会进搜索；视频字幕需要你自己指定。</p>
        </div>
      </div>

      <label className="check-row">
        <input type="checkbox" checked={uploadVectorize} onChange={(event) => setUploadVectorize(event.target.checked)} />
        加入检索
      </label>

      <label className="check-row">
        <input type="checkbox" checked={storeOriginal} onChange={(event) => setStoreOriginal(event.target.checked)} />
        保存源文件到 S3（取消勾选则仅保留预览图、封面和向量，并在入库后删除临时源文件）
      </label>

      <label className="field">
        <span>{storeOriginal ? "来源网址（可选）" : "来源网址（不存源文件时必填）"}</span>
        <input
          className="input"
          type="url"
          placeholder="https://example.com/videos/..."
          disabled={busy}
          value={uploadSourceUrl}
          onChange={(event) => setUploadSourceUrl(event.target.value)}
        />
      </label>

      <label className="field">
        <span>视频或图片描述（可选，支持文本元数据检索）</span>
        <input
          className="input"
          type="text"
          placeholder="例如：海边日落航拍、老板娘专访第二段"
          disabled={busy}
          value={uploadDescription}
          onChange={(event) => setUploadDescription(event.target.value)}
        />
      </label>

      <div
        className={`upload-dropzone${dropActive ? " is-active" : ""}${busy ? " is-busy" : ""}`}
        onDragEnter={onDropZoneDragOver}
        onDragOver={onDropZoneDragOver}
        onDragLeave={onDropZoneDragLeave}
        onDrop={onDropZoneDrop}
      >
        <CloudArrowUp size={28} weight={dropActive ? "fill" : "regular"} />
        <strong>{dropActive ? "松开即可上传" : "把照片或视频拖到这里"}</strong>
        <p className="hint">支持 PNG / JPG / 视频等。也可以点下面按钮选择。</p>
        {uploadProgress ? (
          <motion.div
            className="upload-status"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, ease: EASE }}
          >
            <SpinnerGap size={15} className="spin" />
            {uploadProgress}
          </motion.div>
        ) : null}
        <div className="upload-actions">
          <label className={`btn primary btn-label${busy ? " is-busy" : ""}`}>
            <UploadSimple size={16} />
            选择文件
            <input type="file" multiple hidden disabled={busy} onChange={(event) => void onUpload(event)} />
          </label>
          <label className={`btn btn-label${busy ? " is-busy" : ""}`}>
            <FolderOpen size={16} />
            选择文件夹
            <input
              type="file"
              multiple
              hidden
              disabled={busy}
              onChange={(event) => void onUpload(event)}
              {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
            />
          </label>
        </div>
      </div>

      <label className="field">
        <span>视频封面图片（可选，不传则默认使用首个有效抽帧）</span>
        <input
          className="input"
          type="file"
          accept="image/*"
          disabled={busy}
          onChange={(event) => setUploadCover(event.target.files?.[0] ?? null)}
        />
        <span className="hint">上传后会显示在右侧预览信息面板中，搜索结果列表仍展示高相关命中帧。</span>
      </label>

      <label className="field">
        <span>仅视频字幕（可选，不是传照片）</span>
        <input
          className="input"
          type="file"
          accept=".srt,.vtt,.ass"
          disabled={busy}
          onChange={(event) => setUploadSubtitle(event.target.files?.[0] ?? null)}
        />
        <span className="hint">这里只能选 .srt / .vtt / .ass。PNG 照片请拖到上面或点「选择文件」。</span>
      </label>
    </div>
  );

  const driveStep = sources.length === 0 ? (
    <Empty title="还没有网盘" action={<button type="button" className="btn primary" onClick={() => setConnectOpen(true)}>连接网盘</button>}>
      连上之后才能浏览和勾选。保存配置不等于已经连上。
    </Empty>
  ) : (
    <div className="add-step">
      <div className="step-heading">
        <span className="panel-icon"><CloudArrowUp size={18} /></span>
        <div>
          <h3>从已有网盘里选</h3>
          <p className="hint">只登记位置，不搬原文件。勾选后可以逐个指定要不要加入检索。</p>
        </div>
      </div>

      <div className="browser-toolbar">
        <label className="field grow">
          <span>已连接的位置</span>
          <select className="input" value={sourceId} onChange={(event) => setSourceId(event.target.value)}>
            {sources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.name} · {probeLabel(source)}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="btn" disabled={busy || !sourceId} onClick={() => void probe(sourceId)}>试连接</button>
        <button type="button" className="btn ghost" disabled={busy} onClick={() => browse(prefix)}>重新列出</button>
        <button type="button" className="btn ghost" onClick={() => setConnectOpen(true)}>连接网盘</button>
      </div>

      <div className="file-browser">
        <header className="browser-path">
          <span className="mono">{prefix || "/"}</span>
          {prefix ? (
            <button type="button" className="btn ghost" onClick={() => browse(parentPrefix(prefix))}>
              <ArrowLeft size={14} />
              上级
            </button>
          ) : null}
        </header>

        {entries.length === 0 ? (
          <div className="browser-empty">
            {busy ? (
              <>
                <SpinnerGap size={16} className="spin" />
                正在列出…
              </>
            ) : (
              "这里是空的，或还没连上。"
            )}
          </div>
        ) : (
          <div className="browser-list">
            {entries.map((entry, index) => (
              <motion.div
                className={`browser-row${entry.is_dir ? " is-dir" : ""}`}
                key={entry.key}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, ease: EASE, delay: Math.min(index * 0.012, 0.24) }}
              >
                {entry.is_dir ? (
                  <>
                    <span className="browser-folder"><FolderOpen size={16} /></span>
                    <button type="button" className="browser-name as-button" onClick={() => browse(entry.key)}>
                      {entry.key.split("/").filter(Boolean).pop()}/
                    </button>
                    <CaretRight size={13} className="browser-caret" />
                  </>
                ) : (
                  <>
                    <input
                      type="checkbox"
                      checked={Boolean(choices[entry.key]?.selected)}
                      onChange={(event) =>
                        setChoices((prev) => ({
                          ...prev,
                          [entry.key]: {
                            selected: event.target.checked,
                            vectorize: prev[entry.key]?.vectorize ?? true,
                          },
                        }))
                      }
                    />
                    <div className="browser-name">
                      <span>{entry.key.split("/").pop()}</span>
                      {knownKeys.has(entry.key) ? <span className="soft-tag">已在资料库</span> : null}
                    </div>
                    <div className="browser-controls">
                      <label className="check-row small">
                        <input
                          type="checkbox"
                          checked={Boolean(choices[entry.key]?.vectorize)}
                          disabled={!choices[entry.key]?.selected}
                          onChange={(event) =>
                            setChoices((prev) => ({
                              ...prev,
                              [entry.key]: {
                                ...(prev[entry.key] ?? { selected: false, vectorize: false }),
                                vectorize: event.target.checked,
                              },
                            }))
                          }
                        />
                        加入检索
                      </label>
                      {isVideo(entry.key) ? (
                        <select
                          className="input select-sm"
                          disabled={!choices[entry.key]?.selected}
                          value={choices[entry.key]?.subtitle || ""}
                          onChange={(event) =>
                            setChoices((prev) => ({
                              ...prev,
                              [entry.key]: {
                                ...(prev[entry.key] ?? { selected: true, vectorize: true }),
                                subtitle: event.target.value,
                              },
                            }))
                          }
                        >
                          <option value="">稍后添加字幕</option>
                          {entries
                            .filter((item) => !item.is_dir && isSubtitle(item.key))
                            .sort((a, b) => Number(stem(b.key) === stem(entry.key)) - Number(stem(a.key) === stem(entry.key)))
                            .map((sub) => (
                              <option key={sub.key} value={sub.key}>
                                {stem(sub.key) === stem(entry.key) ? "建议：" : ""}
                                {sub.key.split("/").pop()}
                              </option>
                            ))}
                          {assets.filter((asset) => asset.kind === "subtitle").map((sub) => (
                            <option key={sub.id} value={sub.object_key}>{sub.display_name || sub.object_key.split("/").pop()}</option>
                          ))}
                        </select>
                      ) : null}
                    </div>
                  </>
                )}
              </motion.div>
            ))}
          </div>
        )}

        <footer className="browser-foot">
          <span className="hint">已选 {selected.length} 个文件</span>
          <div className="row">
            <button
              type="button"
              className="btn ghost"
              disabled={busy || selected.length === 0}
              onClick={() => {
                setChoices((prev) => {
                  const next = { ...prev };
                  selected.forEach((item) => {
                    next[item.key] = { ...(next[item.key] ?? { selected: true, vectorize: false }), vectorize: false };
                  });
                  return next;
                });
              }}
            >
              改为仅登记
            </button>
            <button type="button" className="btn primary" disabled={busy || selected.length === 0} onClick={() => void submitDrive()}>
              登记所选（{selected.length}）
            </button>
          </div>
        </footer>
      </div>
    </div>
  );

  return (
    <>
      <Portal>
        <AnimatePresence>
          {open ? (
            <motion.div
              key="add-overlay"
              className="overlay"
              variants={overlayVariants}
              initial="hidden"
              animate="show"
              exit="exit"
              onClick={onClose}
            >
              <AnimatePresence mode="wait" initial={false}>
                {step === "pick" ? (
                  <motion.div
                    key="pick"
                    className="add-menu"
                    variants={modalVariants}
                    initial="hidden"
                    animate="show"
                    exit="exit"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <header className="add-menu-head">
                      <p className="eyebrow">ADD · 添加文件</p>
                      <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
                        <X size={17} />
                      </button>
                    </header>

                    <div className="add-pick-grid">
                      <motion.button
                        type="button"
                        className="add-option"
                        onClick={() => setStep("computer")}
                        initial={{ opacity: 0, y: 14 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.5, ease: EASE, delay: 0.08 }}
                        whileHover={{ y: -4 }}
                        whileTap={{ scale: 0.985 }}
                      >
                        <span className="add-option-icon"><UploadSimple size={22} /></span>
                        <strong>从这台电脑上传</strong>
                        <span>拷到默认存放位置，再决定要不要加入检索。</span>
                        <span className="add-option-arrow"><ArrowRight size={16} /></span>
                      </motion.button>

                      <motion.button
                        type="button"
                        className="add-option"
                        onClick={() => setStep("drive")}
                        initial={{ opacity: 0, y: 14 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.5, ease: EASE, delay: 0.16 }}
                        whileHover={{ y: -4 }}
                        whileTap={{ scale: 0.985 }}
                      >
                        <span className="add-option-icon"><CloudArrowUp size={22} /></span>
                        <strong>从已有网盘里选</strong>
                        <span>只登记位置，不搬原文件。</span>
                        <span className="add-option-arrow"><ArrowRight size={16} /></span>
                      </motion.button>
                    </div>

                    <p className="add-menu-note">不想加入检索也可以先只登记。网盘里的原文件不会被搬动。</p>
                  </motion.div>
                ) : (
                  <motion.div
                    key="form"
                    className="modal add-modal"
                    variants={modalVariants}
                    initial="hidden"
                    animate="show"
                    exit="exit"
                    transition={softSpring}
                    onClick={(event) => event.stopPropagation()}
                  >
                    <header className="modal-head">
                      <button type="button" className="btn ghost" onClick={() => setStep("pick")}>
                        <CaretLeft size={14} />
                        返回
                      </button>
                      <p className="eyebrow">{step === "computer" ? "从这台电脑" : "从已有网盘"}</p>
                      <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
                        <X size={17} />
                      </button>
                    </header>

                    <AnimatePresence mode="wait" initial={false}>
                      <motion.div
                        key={step}
                        initial={{ opacity: 0, x: 16 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, x: -16 }}
                        transition={{ duration: 0.24, ease: EASE }}
                      >
                        {banners}
                        {step === "computer" ? computerStep : driveStep}
                      </motion.div>
                    </AnimatePresence>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </Portal>

      <AnimatePresence>
        {connectOpen ? (
          <ConnectDrive
            key="connect"
            onClose={() => setConnectOpen(false)}
            onSaved={(source) => {
              void loadSources().then(() => setSourceId(source.id));
              setNotice("已保存。点「试连接」确认能不能连上。");
            }}
          />
        ) : null}
      </AnimatePresence>
    </>
  );
}
