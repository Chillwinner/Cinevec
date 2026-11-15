import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState, type ReactNode } from "react";
import {
  CaretDown,
  CheckCircle,
  Clock,
  CloudArrowUp,
  Key,
  Lock,
  Rows,
  SlidersHorizontal,
  Timer,
} from "@phosphor-icons/react";
import { api } from "../api";
import { useAuth } from "../AuthContext";
import ConnectDrive, { probeLabel } from "../components/ConnectDrive";
import { Banner, Empty, PageHead, Pill } from "../components/ui";
import { EASE, softSpring } from "../motion";
import type { AppSettings, SampleSettings, Source, SystemStatus } from "../types";

const modes: { value: SampleSettings["mode"]; label: string; hint: string; icon: ReactNode }[] = [
  { value: "interval_seconds", label: "按时间间隔", hint: "默认每 5 秒取 1 帧。", icon: <Clock size={17} /> },
  { value: "uniform_total", label: "按总帧数均匀", hint: "整段视频均匀取固定数量。", icon: <Rows size={17} /> },
  { value: "frames_per_minute", label: "每分钟帧数", hint: "例如每分钟 12 帧。", icon: <Timer size={17} /> },
];

const emptySearch = {
  top_n: 20,
  max_previews_per_video: 5,
  counting_mode: "asset" as const,
  fusion: "visual_first" as const,
  subtitle_agg: "max" as const,
  rrf_k: 60,
  score_threshold: null as number | null,
  oversample_factor: 4,
  max_scan: 400,
  metric: "cosine",
};

function Panel({
  icon,
  title,
  hint,
  children,
  delay = 0,
}: {
  icon: ReactNode;
  title: string;
  hint?: string;
  children: ReactNode;
  delay?: number;
}) {
  return (
    <motion.section
      className="panel"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, ease: EASE, delay }}
    >
      <header className="panel-head">
        <span className="panel-icon">{icon}</span>
        <div>
          <h3>{title}</h3>
          {hint ? <p className="hint">{hint}</p> : null}
        </div>
      </header>
      <div className="panel-body">{children}</div>
    </motion.section>
  );
}

export default function SettingsPage() {
  const { auth, refresh: refreshAuth, rememberToken, logout } = useAuth();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [draft, setDraft] = useState<SampleSettings | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [connectOpen, setConnectOpen] = useState(false);
  const [authUser, setAuthUser] = useState("");
  const [authPass, setAuthPass] = useState("");
  const [authPass2, setAuthPass2] = useState("");
  const [keyMode, setKeyMode] = useState<"preset" | "custom">("custom");
  const [customKey, setCustomKey] = useState("");
  const [confirmChoice, setConfirmChoice] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [authNotice, setAuthNotice] = useState<string | null>(null);
  const [apiTokens, setApiTokens] = useState<{ id: string; name: string; token_prefix: string; created_at: string }[]>([]);
  const [tokenName, setTokenName] = useState("plugin");
  const [freshToken, setFreshToken] = useState<string | null>(null);

  async function load() {
    try {
      const [st, se, so, tokens] = await Promise.all([api.status(), api.getSettings(), api.sources(), api.apiTokens()]);
      setStatus(st);
      setSettings(se);
      setDraft(se.sample);
      setSources(so.items);
      setApiTokens(tokens.items);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "设置读不出来");
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function saveSample() {
    if (!draft || !settings) return;
    setBusy(true);
    setNotice(null);
    try {
      const next = await api.putSettings({
        sample: draft,
        subtitle_merge: settings.subtitle_merge,
        refresh: settings.refresh,
        lifecycle: settings.lifecycle,
        upload: settings.upload,
      });
      setSettings(next);
      setDraft(next.sample);
      setNotice("视频取样已保存。已经抽完的片子不会自动重抽。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  async function enableAuth() {
    setAuthError(null);
    setBusy(true);
    try {
      const result = await api.enableAuth({
        username: authUser,
        password: authPass,
        password_confirm: authPass2,
        signing_key_mode: keyMode,
        custom_signing_key: keyMode === "custom" ? customKey : undefined,
        confirm_customization: confirmChoice,
      });
      if (result.token) rememberToken({ token: result.token, username: result.username, expires_in: result.expires_in });
      setAuthPass("");
      setAuthPass2("");
      setCustomKey("");
      setConfirmChoice(false);
      setAuthNotice("登录已开启。");
      await refreshAuth();
      await load();
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "无法开启登录");
    } finally {
      setBusy(false);
    }
  }

  async function disableAuth() {
    setAuthError(null);
    setBusy(true);
    try {
      await api.disableAuth();
      setAuthNotice("登录已关闭。");
      await refreshAuth();
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "无法关闭登录");
    } finally {
      setBusy(false);
    }
  }

  async function rotateKey() {
    setAuthError(null);
    setBusy(true);
    try {
      await api.updateSigningKey({
        signing_key_mode: keyMode,
        custom_signing_key: keyMode === "custom" ? customKey : undefined,
        confirm_customization: confirmChoice,
      });
      setCustomKey("");
      setConfirmChoice(false);
      setAuthNotice("密钥已更新，请重新登录。");
      logout();
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "无法更新密钥");
    } finally {
      setBusy(false);
    }
  }

  function field<K extends keyof SampleSettings>(key: K, value: SampleSettings[K]) {
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));
  }

  const cards = status
    ? (
        [
          ["本机数据库", status.sqlite],
          ["抽帧工具", status.ffmpeg ?? settings?.ffmpeg],
          ["预览存放", status.file_store ?? settings?.file_store],
          ["网盘", status.minio_s3],
          ["检索库", status.milvus],
          ["看图模型", status.embedding_model],
          ["字幕模型", status.subtitle_encoder],
          ["登录", status.auth],
        ] as const
      ).filter((item) => item[1])
    : [];

  return (
    <div>
      <PageHead
        eyebrow="SETTINGS"
        title="设置"
        desc="日常只改取样和网盘。模型和检索公式收在「高级」里，其中不少还没拍板。"
      />

      <AnimatePresence>
        {error ? (
          <motion.div key="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Banner kind="danger" title="没做成">{error}</Banner>
          </motion.div>
        ) : null}
        {notice ? (
          <motion.div key="notice" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Banner kind="info" title="已保存">{notice}</Banner>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {draft && settings ? (
        <Panel
          icon={<Timer size={17} />}
          title="视频怎么取样"
          hint="只抽帧，不剪片子。间隔太大可能漏掉短镜头。"
          delay={0.04}
        >
          <div className="mode-grid">
            {modes.map((mode) => {
              const active = draft.mode === mode.value;
              return (
                <button
                  key={mode.value}
                  type="button"
                  className={`mode-card${active ? " active" : ""}`}
                  onClick={() => field("mode", mode.value)}
                >
                  <span className="mode-icon">{mode.icon}</span>
                  <strong>{mode.label}</strong>
                  <span className="mode-hint">{mode.hint}</span>
                  <AnimatePresence>
                    {active ? (
                      <motion.span
                        className="mode-check"
                        initial={{ scale: 0, opacity: 0 }}
                        animate={{ scale: 1, opacity: 1 }}
                        exit={{ scale: 0, opacity: 0 }}
                        transition={softSpring}
                      >
                        <CheckCircle size={18} weight="fill" />
                      </motion.span>
                    ) : null}
                  </AnimatePresence>
                </button>
              );
            })}
          </div>

          <div className="sample-row">
            <AnimatePresence mode="wait" initial={false}>
              {draft.mode === "interval_seconds" ? (
                <motion.label
                  key="interval"
                  className="field"
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.2 }}
                >
                  <span>间隔（秒）</span>
                  <input className="input" type="number" min={0.1} step={0.1} value={draft.interval_seconds} onChange={(event) => field("interval_seconds", Number(event.target.value))} />
                </motion.label>
              ) : null}
              {draft.mode === "uniform_total" ? (
                <motion.label
                  key="uniform"
                  className="field"
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.2 }}
                >
                  <span>总共抽几帧</span>
                  <input className="input" type="number" min={1} step={1} value={draft.uniform_total} onChange={(event) => field("uniform_total", Number(event.target.value))} />
                </motion.label>
              ) : null}
              {draft.mode === "frames_per_minute" ? (
                <motion.label
                  key="per-minute"
                  className="field"
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -6 }}
                  transition={{ duration: 0.2 }}
                >
                  <span>每分钟帧数</span>
                  <input className="input" type="number" min={0.1} step={0.1} value={draft.frames_per_minute} onChange={(event) => field("frames_per_minute", Number(event.target.value))} />
                </motion.label>
              ) : null}
            </AnimatePresence>
            <button type="button" className="btn primary" disabled={busy} onClick={() => void saveSample()}>保存取样</button>
          </div>
        </Panel>
      ) : null}

      {settings ? (
        <Panel
          icon={<CloudArrowUp size={17} />}
          title="传输队列限制"
          hint="防止网页端或外部插件一次性上传过多文件占满本地临时磁盘。可在 1 到 500 之间调整。"
          delay={0.07}
        >
          <div className="sample-row">
            <label className="field">
              <span>单次最多上传文件数（max_upload_files）</span>
              <input
                className="input"
                type="number"
                min={1}
                max={500}
                step={1}
                value={settings.upload?.max_upload_files ?? 20}
                onChange={(event) =>
                  setSettings({
                    ...settings,
                    upload: { max_upload_files: Math.max(1, Math.min(500, Number(event.target.value) || 20)) },
                  })
                }
              />
            </label>
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                setNotice(null);
                setError(null);
                void api
                  .putSettings({ upload: { max_upload_files: settings.upload?.max_upload_files ?? 20 } })
                  .then((next) => {
                    setSettings(next);
                    setNotice("传输队列限制已保存。");
                  })
                  .catch((err) => setError(err instanceof Error ? err.message : "保存失败"))
                  .finally(() => setBusy(false));
              }}
            >
              保存传输限制
            </button>
          </div>
        </Panel>
      ) : null}

      <Panel
        icon={<CloudArrowUp size={17} />}
        title="网盘与默认存放位置"
        hint="添加文件时也可以连接。这里管已有位置、试连接，以及哪一个桶用来放电脑上传的原文件。"
        delay={0.1}
      >
        {sources.length === 0 ? (
          <Empty title="还没有网盘" action={<button type="button" className="btn primary" onClick={() => setConnectOpen(true)}>连接网盘</button>}>
            保存不等于连上，需要再点「试连接」。
          </Empty>
        ) : (
          <div className="source-list">
            {sources.map((source, index) => (
              <motion.article
                className="source-row"
                key={source.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.45, ease: EASE, delay: index * 0.05 }}
              >
                <span className={`source-status${source.last_probe_status === "ok" ? " on" : ""}`} aria-hidden="true" />
                <div className="grow">
                  <div className="source-title">
                    <strong>{source.name}</strong>
                    <Pill status={source.last_probe_status === "ok" ? "ok" : "unconfigured"}>{probeLabel(source)}</Pill>
                    {source.is_platform_storage ? <span className="soft-tag">默认存放</span> : null}
                    {source.role === "input" ? <span className="soft-tag">只读挑选</span> : null}
                  </div>
                  <p className="hint source-meta">
                    {source.endpoint || "没填地址"} · {source.bucket || "没填桶"}
                  </p>
                  <p className="hint">
                    {source.last_probe_message || (source.secret_configured ? "密钥已保存，界面不显示" : "还没填密钥")}
                  </p>
                </div>
                <div className="source-actions">
                  <button type="button" className="btn" disabled={busy} onClick={() => void api.probeSource(source.id).then(load).catch((err: Error) => setError(err.message))}>试连接</button>
                  {!source.is_platform_storage ? (
                    <button type="button" className="btn ghost" disabled={busy} onClick={() => void api.updateSource(source.id, { is_platform_storage: true }).then(() => load())}>设为默认存放</button>
                  ) : null}
                  <button
                    type="button"
                    className="btn danger ghost"
                    onClick={() => {
                      if (!window.confirm("只删这条连接，不删网盘里的原文件。")) return;
                      void api.deleteSource(source.id).then(() => load());
                    }}
                  >
                    去掉
                  </button>
                </div>
              </motion.article>
            ))}
          </div>
        )}
        {sources.length > 0 ? (
          <div className="panel-actions">
            <button type="button" className="btn" onClick={() => setConnectOpen(true)}>再连一个</button>
          </div>
        ) : null}
      </Panel>

      <Panel
        icon={<Lock size={17} />}
        title="登录（可选）"
        hint="家里局域网可以不开。打开后网页和接口都要同一账号。"
        delay={0.16}
      >
        {authError ? <Banner kind="danger" title="登录设置失败">{authError}</Banner> : null}
        {authNotice ? <Banner kind="info" title="已更新">{authNotice}</Banner> : null}

        <div className="auth-status">
          <span className={`source-status${auth?.enabled ? " on" : ""}`} aria-hidden="true" />
          <div className="grow">
            <strong>{auth?.enabled ? "登录已开启" : "登录已关闭"}</strong>
            <p className="hint">账号：{auth?.user_configured ? (auth.username || "已设置") : "尚未设置"}</p>
          </div>
          <Pill status={auth?.enabled ? "ok" : "unconfigured"}>{auth?.enabled ? "需要登录" : "无需登录"}</Pill>
        </div>

        {auth?.enabled ? (
          <div className="form">
            <div className="mode-grid compact">
              <button type="button" className={`mode-card${keyMode === "preset" ? " active" : ""}`} onClick={() => setKeyMode("preset")} disabled={!auth.preset_available}>
                <strong>沿用本机预设</strong>
                <span className="mode-hint">{auth.preset_available ? "不会在此显示。" : "当前没有预设。"}</span>
              </button>
              <button type="button" className={`mode-card${keyMode === "custom" ? " active" : ""}`} onClick={() => setKeyMode("custom")}>
                <strong>自定义密钥</strong>
                <span className="mode-hint">至少 16 位，不写进仓库。</span>
              </button>
            </div>
            {keyMode === "custom" ? (
              <label className="field"><span>新密钥</span><input className="input" type="password" autoComplete="off" value={customKey} onChange={(event) => setCustomKey(event.target.value)} /></label>
            ) : null}
            <label className="check-row">
              <input type="checkbox" checked={confirmChoice} onChange={(event) => setConfirmChoice(event.target.checked)} />
              我确认这次密钥选择
            </label>
            <div className="panel-actions">
              <button type="button" className="btn" disabled={busy} onClick={() => void rotateKey()}>更新密钥</button>
              <button type="button" className="btn danger" disabled={busy} onClick={() => void disableAuth()}>关闭登录</button>
            </div>
          </div>
        ) : (
          <div className="form">
            <div className="form-grid three">
              <label className="field"><span>账号</span><input className="input" autoComplete="off" value={authUser} onChange={(event) => setAuthUser(event.target.value)} /></label>
              <label className="field"><span>密码（至少 8 位）</span><input className="input" type="password" autoComplete="new-password" value={authPass} onChange={(event) => setAuthPass(event.target.value)} /></label>
              <label className="field"><span>再输一次</span><input className="input" type="password" autoComplete="new-password" value={authPass2} onChange={(event) => setAuthPass2(event.target.value)} /></label>
            </div>
            <div className="mode-grid compact">
              <button type="button" className={`mode-card${keyMode === "preset" ? " active" : ""}`} onClick={() => setKeyMode("preset")} disabled={!auth?.preset_available}>
                <strong>沿用本机预设</strong>
                <span className="mode-hint">{auth?.preset_available ? "使用本地开发配置。" : "当前没有预设。"}</span>
              </button>
              <button type="button" className={`mode-card${keyMode === "custom" ? " active" : ""}`} onClick={() => setKeyMode("custom")}>
                <strong>自定义密钥</strong>
                <span className="mode-hint">自己设签名密钥。</span>
              </button>
            </div>
            {keyMode === "custom" ? (
              <label className="field"><span>自定义密钥（至少 16 位）</span><input className="input" type="password" autoComplete="off" value={customKey} onChange={(event) => setCustomKey(event.target.value)} /></label>
            ) : null}
            <label className="check-row">
              <input type="checkbox" checked={confirmChoice} onChange={(event) => setConfirmChoice(event.target.checked)} />
              我确认开启后接口需要登录
            </label>
            <div className="panel-actions">
              <button type="button" className="btn primary" disabled={busy || !confirmChoice} onClick={() => void enableAuth()}>开启登录</button>
            </div>
          </div>
        )}
      </Panel>

      <Panel
        icon={<Key size={17} />}
        title="插件 API Token"
        hint="给外挂用。完整 Token 只在生成时显示一次，之后只留前缀。插件用 Bearer 调 /api/v1。"
        delay={0.2}
      >
        {freshToken ? (
          <Banner kind="info" title="请立刻复制">
            <code className="token-once">{freshToken}</code>
          </Banner>
        ) : null}
        {apiTokens.length === 0 ? (
          <p className="hint">还没有 Token。</p>
        ) : (
          <div className="source-list">
            {apiTokens.map((item) => (
              <article className="source-row" key={item.id}>
                <div className="grow">
                  <strong>{item.name}</strong>
                  <p className="hint">{item.token_prefix}… · {item.created_at}</p>
                </div>
                <button
                  type="button"
                  className="btn danger ghost"
                  disabled={busy}
                  onClick={() => {
                    if (!window.confirm("作废后插件立刻不能再用这把钥匙。")) return;
                    void api.revokeApiToken(item.id).then(() => load());
                  }}
                >
                  作废
                </button>
              </article>
            ))}
          </div>
        )}
        <div className="form" style={{ marginTop: 12 }}>
          <label className="field">
            <span>名称</span>
            <input className="input" value={tokenName} onChange={(event) => setTokenName(event.target.value)} />
          </label>
          <div className="panel-actions">
            <button
              type="button"
              className="btn primary"
              disabled={busy}
              onClick={() => {
                setBusy(true);
                setFreshToken(null);
                void api
                  .createApiToken(tokenName)
                  .then((created) => {
                    setFreshToken(created.token);
                    return load();
                  })
                  .catch((err: Error) => setError(err.message))
                  .finally(() => setBusy(false));
              }}
            >
              生成 Token
            </button>
          </div>
        </div>
      </Panel>

      <button type="button" className="advanced-toggle" onClick={() => setShowAdvanced((value) => !value)}>
        <SlidersHorizontal size={16} />
        <span>{showAdvanced ? "收起高级设置" : "显示高级：模型、检索库、重建与阈值"}</span>
        <motion.span animate={{ rotate: showAdvanced ? 180 : 0 }} transition={softSpring}>
          <CaretDown size={14} />
        </motion.span>
      </button>

      <AnimatePresence initial={false}>
        {showAdvanced ? (
          <motion.div
            key="advanced"
            className="advanced"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.42, ease: EASE }}
          >
            <div className="status-grid">
              {cards.map(([name, item], index) => (
                <motion.article
                  className="status-card"
                  key={name}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4, ease: EASE, delay: index * 0.03 }}
                >
                  <div className="status-card-head">
                    <h3>{name}</h3>
                    <Pill status={item?.status}>{item?.status === "ok" ? "正常" : item?.status}</Pill>
                  </div>
                  <p className="hint">{item?.message}</p>
                </motion.article>
              ))}
            </div>

            {settings?.model ? (
              <div className="panel flat">
                <h3>模型和向量库</h3>
                <p className="hint">{settings.model.note} 保存取样不会当成模型已经健康，也不会自动换索引。</p>
                <div className="meta-list">
                  <span>当前：{settings.model.active_family || "未指定"}</span>
                  <span>地址：{settings.model.http_base_url_configured ? settings.model.http_base_url : "未配置"}</span>
                </div>
              </div>
            ) : null}

            {draft && settings ? (
              <div className="panel flat">
                <h3>过滤与预览阈值（未拍板）</h3>
                <p className="hint">{settings.provisional_note}</p>
                <div className="settings-grid">
                  <label className="field"><span>全黑判定</span><input className="input" type="number" value={draft.black_max_mean} onChange={(event) => field("black_max_mean", Number(event.target.value))} /></label>
                  <label className="field"><span>全白判定</span><input className="input" type="number" value={draft.white_min_mean} onChange={(event) => field("white_min_mean", Number(event.target.value))} /></label>
                  <label className="field"><span>严重模糊</span><input className="input" type="number" value={draft.blur_max_laplacian_var} onChange={(event) => field("blur_max_laplacian_var", Number(event.target.value))} /></label>
                  <label className="field"><span>连续相似</span><input className="input" type="number" value={draft.similar_max_mad} onChange={(event) => field("similar_max_mad", Number(event.target.value))} /></label>
                  <label className="field"><span>预览最长边</span><input className="input" type="number" value={draft.preview_max_edge} onChange={(event) => field("preview_max_edge", Number(event.target.value))} /></label>
                  <label className="field"><span>预览质量</span><input className="input" type="number" value={draft.preview_quality} onChange={(event) => field("preview_quality", Number(event.target.value))} /></label>
                </div>
                <div className="panel-actions">
                  <button type="button" className="btn" disabled={busy} onClick={() => void saveSample()}>一并保存</button>
                </div>
              </div>
            ) : null}

            {settings ? (
              <div className="panel flat">
                <h3>检索融合 / 阈值（未拍板）</h3>
                <p className="hint">默认画面优先。条数是图片一套、视频一套，互不占名额。RRF 留在这里可以试。相关阈值空着就是关。</p>
                <div className="settings-grid">
                  <label className="field"><span>返回条数</span>
                    <input
                      className="input"
                      type="number"
                      min={1}
                      value={settings.search?.top_n ?? 20}
                      onChange={(event) => setSettings({ ...settings, search: { ...(settings.search ?? emptySearch), top_n: Number(event.target.value) } })}
                    />
                  </label>
                  <label className="field"><span>融合</span>
                    <select
                      className="input"
                      value={settings.search?.fusion ?? "visual_first"}
                      onChange={(event) => setSettings({ ...settings, search: { ...(settings.search ?? emptySearch), fusion: event.target.value as "visual_first" | "rrf" } })}
                    >
                      <option value="visual_first">画面优先</option>
                      <option value="rrf">RRF（候选）</option>
                    </select>
                  </label>
                  <label className="field"><span>相关阈值（空为关闭）</span>
                    <input
                      className="input"
                      type="number"
                      step={0.01}
                      value={settings.search?.score_threshold ?? ""}
                      onChange={(event) => setSettings({ ...settings, search: { ...(settings.search ?? emptySearch), score_threshold: event.target.value === "" ? null : Number(event.target.value) } })}
                    />
                  </label>
                </div>
                <div className="panel-actions">
                  <button type="button" className="btn" disabled={busy} onClick={() => void api.putSettings({ search: settings.search }).then(setSettings)}>保存检索设置</button>
                </div>
              </div>
            ) : null}

            {settings ? (
              <div className="panel flat">
                <h3>来源刷新（未拍板）</h3>
                <label className="check-row" style={{ marginBottom: 12 }}>
                  <input
                    type="checkbox"
                    checked={Boolean(settings.refresh?.enabled)}
                    onChange={(event) => setSettings({ ...settings, refresh: { ...(settings.refresh ?? { enabled: false, interval_minutes: 60, last_enqueued_at: null, last_completed_at: null, last_result: null, next_due_at: null }), enabled: event.target.checked } })}
                  />
                  定时核对网盘（默认关）
                </label>
                <div className="panel-actions">
                  <button type="button" className="btn" disabled={busy} onClick={() => void api.putSettings({ refresh: { enabled: Boolean(settings.refresh?.enabled), interval_minutes: settings.refresh?.interval_minutes ?? 60 } }).then(setSettings)}>保存</button>
                </div>
              </div>
            ) : null}
          </motion.div>
        ) : null}
      </AnimatePresence>

      <AnimatePresence>
        {connectOpen ? (
          <ConnectDrive
            key="connect"
            onClose={() => setConnectOpen(false)}
            onSaved={() => { void load(); setNotice("已保存。请点「试连接」。"); }}
          />
        ) : null}
      </AnimatePresence>
    </div>
  );
}
