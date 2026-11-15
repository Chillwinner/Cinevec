import { ChangeEvent, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import ConnectDrive, { probeLabel } from "../components/ConnectDrive";
import { Banner, Empty, PageHead, Pill } from "../components/ui";
import type { Asset, ObjectEntry, Source } from "../types";

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

export default function AddPage() {
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
  const [uploadSubtitle, setUploadSubtitle] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState<string | null>(null);
  const [knownKeys, setKnownKeys] = useState<Set<string>>(new Set());

  async function loadSources() {
    const data = await api.sources();
    setSources(data.items);
    if (!sourceId && data.items[0]) setSourceId(data.items[0].id);
  }

  useEffect(() => {
    void loadSources().catch((err: Error) => setError(err.message));
  }, []);

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
    if (step !== "drive" || !sourceId) return;
    setPrefix("");
    setChoices({});
    void browse("");
    void api.assets({ sourceId, kind: "subtitle", limit: 200 }).then((data) => setAssets(data.items)).catch(() => undefined);
    void api.objectKeys(sourceId).then((data) => setKnownKeys(new Set(data.items))).catch(() => setKnownKeys(new Set()));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceId, step]);

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

  async function onUpload(event: ChangeEvent<HTMLInputElement>) {
    const list = event.target.files;
    if (!list || list.length === 0) return;
    setBusy(true);
    setError(null);
    setUploadProgress("准备上传…");
    try {
      const files = Array.from(list);
      if (uploadSubtitle) files.push(uploadSubtitle);
      const meta = files.map((file) => ({
        relative_path: (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name,
        vectorize: uploadVectorize && !isSubtitle(file.name),
        subtitle_relative_path: uploadSubtitle && isVideo(file.name) ? uploadSubtitle.name : undefined,
      }));
      const result = await api.uploadFiles(files, meta, (loaded, total) => {
        setUploadProgress(`${files.length} 个文件 · ${(loaded / 1048576).toFixed(1)} / ${(total / 1048576).toFixed(1)} MB`);
      });
      setNotice(`${result.message || "已经拷进家里的存放位置。"} 要看进度去「仪表盘」。`);
      setUploadProgress(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setBusy(false);
      setUploadProgress(null);
      event.target.value = "";
    }
  }

  return (
    <div>
      <PageHead title="添加" desc="从这台电脑上传，或从已经连上的网盘里勾选。不想检索也可以先只登记。" />
      {error ? <Banner kind="warn" title="这次没做成">{error}</Banner> : null}
      {notice ? <Banner kind="info" title="好了">{notice} <Link to="/activity">去仪表盘</Link></Banner> : null}

      {step !== "pick" ? (
        <p><button className="btn ghost" onClick={() => setStep("pick")}>返回上一步</button></p>
      ) : null}

      {step === "pick" ? (
        <div className="mode-grid">
          <button type="button" className="mode-card" onClick={() => setStep("computer")}>
            <strong>从这台电脑上传</strong>
            <span>照片和视频会拷到默认存放位置，再决定要不要加入检索。</span>
          </button>
          <button type="button" className="mode-card" onClick={() => setStep("drive")}>
            <strong>从已有网盘里选</strong>
            <span>只登记位置，不搬原文件。没有网盘就先连接。</span>
          </button>
        </div>
      ) : null}

      {step === "computer" ? (
        <div className="card">
          <h3>从这台电脑</h3>
          <p className="hint">选文件或整个文件夹。勾上「加入检索」才会进搜索；视频字幕需要你自己指定，不会按文件名瞎配。</p>
          <label className="row">
            <input type="checkbox" checked={uploadVectorize} onChange={(e) => setUploadVectorize(e.target.checked)} />
            加入检索
          </label>
          <label className="field" style={{ maxWidth: 420 }}>
            <span>视频可选手动字幕（稍后也能在资料库绑定）</span>
            <input className="input" type="file" accept=".srt,.vtt,.ass" disabled={busy} onChange={(e) => setUploadSubtitle(e.target.files?.[0] ?? null)} />
          </label>
          {uploadProgress ? <p className="hint">{uploadProgress}</p> : null}
          <div className="row">
            <label className="btn primary">
              选择文件
              <input type="file" multiple hidden disabled={busy} onChange={(e) => void onUpload(e)} />
            </label>
            <label className="btn">
              选择文件夹
              <input type="file" multiple hidden disabled={busy} onChange={(e) => void onUpload(e)} {...({ webkitdirectory: "", directory: "" } as Record<string, string>)} />
            </label>
          </div>
        </div>
      ) : null}

      {step === "drive" ? (
        sources.length === 0 ? (
          <Empty title="还没有网盘" action={<button className="btn primary" onClick={() => setConnectOpen(true)}>连接网盘</button>}>
            连上之后才能浏览和勾选。保存配置不等于已经连上。
          </Empty>
        ) : (
          <>
            <div className="card" style={{ marginBottom: 16 }}>
              <div className="row">
                <label className="field grow">
                  <span>已连接的位置</span>
                  <select className="input" value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                    {sources.map((source) => (
                      <option key={source.id} value={source.id}>
                        {source.name} · {probeLabel(source)}
                      </option>
                    ))}
                  </select>
                </label>
                <button className="btn" disabled={busy || !sourceId} onClick={() => void probe(sourceId)}>试连接</button>
                <button className="btn" disabled={busy} onClick={() => browse(prefix)}>重新列出</button>
                <button className="btn ghost" onClick={() => setConnectOpen(true)}>连接网盘</button>
              </div>
            </div>
            <div className="card" style={{ padding: 0 }}>
              <div className="row" style={{ padding: "12px 14px", justifyContent: "space-between" }}>
                <strong className="mono">{prefix || "/"}</strong>
                {prefix ? <button className="btn ghost" onClick={() => browse(parentPrefix(prefix))}>上级目录</button> : null}
              </div>
              {entries.length === 0 ? (
                <div style={{ padding: 16, color: "var(--muted)" }}>{busy ? "正在列出…" : "这里是空的，或还没连上。"}</div>
              ) : (
                entries.map((entry) => (
                  <div className="list-item" key={entry.key}>
                    {entry.is_dir ? (
                      <button className="btn ghost" onClick={() => browse(entry.key)}>打开</button>
                    ) : (
                      <input
                        type="checkbox"
                        checked={Boolean(choices[entry.key]?.selected)}
                        onChange={(e) =>
                          setChoices((prev) => ({
                            ...prev,
                            [entry.key]: { selected: e.target.checked, vectorize: prev[entry.key]?.vectorize ?? true },
                          }))
                        }
                      />
                    )}
                    <div className="grow mono">
                      {entry.key.split("/").pop()}
                      {!entry.is_dir && knownKeys.has(entry.key) ? <span className="hint"> · 已在资料库</span> : null}
                    </div>
                    {!entry.is_dir ? (
                      <div className="row">
                        <label className="row">
                          <input
                            type="checkbox"
                            checked={Boolean(choices[entry.key]?.vectorize)}
                            disabled={!choices[entry.key]?.selected}
                            onChange={(e) =>
                              setChoices((prev) => ({
                                ...prev,
                                [entry.key]: { ...(prev[entry.key] ?? { selected: false, vectorize: false }), vectorize: e.target.checked },
                              }))
                            }
                          />
                          加入检索
                        </label>
                        {isVideo(entry.key) ? (
                          <select
                            className="input"
                            disabled={!choices[entry.key]?.selected}
                            value={choices[entry.key]?.subtitle || ""}
                            onChange={(e) =>
                              setChoices((prev) => ({
                                ...prev,
                                [entry.key]: { ...(prev[entry.key] ?? { selected: true, vectorize: true }), subtitle: e.target.value },
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
                            {assets.filter((a) => a.kind === "subtitle").map((sub) => (
                              <option key={sub.id} value={sub.object_key}>{sub.display_name || sub.object_key.split("/").pop()}</option>
                            ))}
                          </select>
                        ) : null}
                      </div>
                    ) : <Pill>文件夹</Pill>}
                  </div>
                ))
              )}
              <div className="row" style={{ padding: 14, justifyContent: "flex-end" }}>
                <button className="btn" disabled={busy || selected.length === 0} onClick={() => {
                  setChoices((prev) => {
                    const next = { ...prev };
                    selected.forEach((item) => { next[item.key] = { ...(next[item.key] ?? { selected: true, vectorize: false }), vectorize: false }; });
                    return next;
                  });
                }}>改为仅登记</button>
                <button className="btn primary" disabled={busy || selected.length === 0} onClick={() => void submitDrive()}>
                  登记所选（{selected.length}）
                </button>
              </div>
            </div>
          </>
        )
      ) : null}

      {connectOpen ? (
        <ConnectDrive
          onClose={() => setConnectOpen(false)}
          onSaved={(source) => {
            void loadSources().then(() => setSourceId(source.id));
            setNotice("已保存。点「试连接」确认能不能连上。");
          }}
        />
      ) : null}
    </div>
  );
}
