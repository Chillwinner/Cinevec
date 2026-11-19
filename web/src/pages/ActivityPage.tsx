import { AnimatePresence, motion } from "motion/react";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowsClockwise, CaretDown, CaretUp, Cpu, GraphicsCard, Memory, X } from "@phosphor-icons/react";
import { api } from "../api";
import { AnimatedNumber, Banner, PageHead, Pill } from "../components/ui";
import { translate, useI18n, type Lang } from "../i18n";
import { EASE, spring } from "../motion";
import type { DashboardResponse, DashboardStage, JobCard, ResourceBlock } from "../types";

const COLUMNS = [
  { id: "register", labelEn: "Ingest", labelZh: "登记" },
  { id: "preprocess", labelEn: "Preprocess", labelZh: "预处理" },
  { id: "encode", labelEn: "Embed", labelZh: "向量编码" },
  { id: "index_write", labelEn: "Index", labelZh: "写入索引" },
] as const;

type StageId = (typeof COLUMNS)[number]["id"];

function StageWireIcon({ id }: { id: StageId }) {
  if (id === "register") {
    return (
      <svg viewBox="0 0 64 64" className="topology-svg" aria-hidden="true">
        <circle cx="32" cy="32" r="22" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <ellipse cx="32" cy="32" rx="10" ry="22" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M11 26h42M11 38h42M32 10v44" fill="none" stroke="currentColor" strokeWidth="1.3" />
      </svg>
    );
  }
  if (id === "preprocess") {
    return (
      <svg viewBox="0 0 64 64" className="topology-svg" aria-hidden="true">
        <rect x="11" y="15" width="42" height="34" rx="4" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <path d="M18 15v34M46 15v34M11 24h7M11 32h7M11 40h7M46 24h7M46 32h7M46 40h7" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <polygon points="28,25 28,39 39,32" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      </svg>
    );
  }
  if (id === "encode") {
    return (
      <svg viewBox="0 0 64 64" className="topology-svg" aria-hidden="true">
        <rect x="21" y="10" width="22" height="44" rx="11" fill="none" stroke="currentColor" strokeWidth="1.5" />
        <ellipse cx="32" cy="18" rx="7" ry="2.5" fill="none" stroke="currentColor" strokeWidth="1.2" />
        <circle cx="32" cy="43" r="3" fill="none" stroke="currentColor" strokeWidth="1.3" />
        <path d="M27 29h10M29 34h6" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 64 64" className="topology-svg" aria-hidden="true">
      <ellipse cx="32" cy="17" rx="17" ry="6" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M15 17v15c0 3.3 7.6 6 17 6s17-2.7 17-6V17" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M15 32v14c0 3.3 7.6 6 17 6s17-2.7 17-6V32" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M26 25l4 4 8-8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function stageCounts(stage?: DashboardStage) {
  const counts = stage?.counts ?? {};
  const jobs = stage?.jobs ?? [];
  const countStatus = (key: JobCard["status"]) =>
    typeof counts[key] === "number" ? counts[key] : jobs.filter((job) => job.status === key).length;
  const running = countStatus("running");
  const waitingOnly = countStatus("waiting");
  const paused = countStatus("paused");
  const blocked = countStatus("blocked");
  const waiting = waitingOnly + paused + blocked;
  const completed = countStatus("completed");
  const failed = countStatus("failed");
  const total = stage?.total ?? jobs.length;
  return { running, waiting, waitingOnly, paused, blocked, completed, failed, total };
}

function stageTrafficLevel(counts: ReturnType<typeof stageCounts>): "clear" | "busy" | "congested" {
  const active = counts.running + counts.waiting;
  if (active >= 5 || counts.blocked > 0) return "congested";
  if (active >= 1) return "busy";
  return "clear";
}

function JobCardView({ job, onRetry }: { job: JobCard; onRetry: (id: string) => void }) {
  const { lang, t } = useI18n();
  return (
    <motion.article
      layout
      className={`queue-card status-${job.status}`}
      initial={{ opacity: 0, y: 12, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.16 } }}
      transition={{ duration: 0.3, ease: EASE, layout: spring }}
    >
      <div className="queue-card-head">
        <strong>{fileName(job, lang)}</strong>
        <Pill status={job.status === "failed" ? "warn" : job.status === "completed" ? "ok" : "info"}>
          {statusLabel(job, lang)}
        </Pill>
      </div>
      <p className="hint">
        {job.step || job.stage}
        {job.elapsed_seconds != null ? ` · ${t("Total", "合计")} ${job.elapsed_seconds.toFixed(2)}s` : ""}
      </p>
      {job.error_message ? <p className="hint topology-job-err">{job.error_message}</p> : null}
      {job.timings && Object.keys(job.timings).length ? (
        <p className="hint timing-line">{formatTimings(job.timings, lang)}</p>
      ) : null}
      {job.progress ? (
        <div className="progress">
          <motion.span
            initial={{ scaleX: 0 }}
            animate={{ scaleX: Math.max(0, Math.min(1, job.progress)) }}
            transition={{ duration: 0.6, ease: EASE }}
          />
        </div>
      ) : null}
      <div className="queue-actions">
        {job.asset_id ? <Link className="btn ghost" to={`/library?id=${job.asset_id}`}>{t("Asset", "资料")}</Link> : null}
        {job.can_retry ? <button type="button" className="btn" onClick={() => onRetry(job.id)}>{t("Retry", "重试")}</button> : null}
      </div>
    </motion.article>
  );
}

function statusLabel(job: JobCard, lang: Lang): string {
  if (job.job_type === "register_asset" && job.status === "completed") {
    return translate(lang, "Registered", "仅登记完成");
  }
  const labels: Record<JobCard["status"], [string, string]> = {
    waiting: ["Queued", "排队"],
    running: ["Running", "正在处理"],
    paused: ["Paused", "已暂停"],
    blocked: ["Pending Index", "还差检索这一步"],
    completed: ["Searchable", "可以搜了"],
    failed: ["Failed", "失败"],
  };
  const pair = labels[job.status];
  return translate(lang, pair[0], pair[1]);
}

function fileName(job: JobCard, lang: Lang) {
  const fallback = translate(lang, "File", "文件");
  return String(job.object_key || job.asset_id || fallback).split(/[\\/]/).pop() || fallback;
}

const TIMING_LABELS: Record<string, [string, string]> = {
  download: ["Download", "下载"],
  decode: ["Decode", "解码"],
  preview: ["Preview", "预览"],
  model_input: ["Model Input", "模型输入"],
  model_input_render: ["Model PNG", "模型PNG编码"],
  model_input_put: ["Write PNG", "模型PNG写入"],
  model_input_file: ["Model PNG", "模型PNG"],
  frame_meta: ["Frame Meta", "帧记录"],
  encode: ["Encode Total", "编码合计"],
  encode_fetch: ["Fetch Input", "取模型输入"],
  encode_http: ["Encoder HTTP", "编码器HTTP"],
  encode_remote: ["Encoder Internal", "编码器内部"],
  index_write: ["Vector Write", "写向量库"],
  associate: ["Associate", "关联"],
  activate: ["Activate", "激活"],
};

function formatTimings(timings: NonNullable<JobCard["timings"]>, lang: Lang) {
  const isZh = lang === "zh";
  return Object.entries(timings)
    .map(([key, value]) => {
      const labelPair = TIMING_LABELS[key];
      const bits = [labelPair ? (isZh ? labelPair[1] : labelPair[0]) : key];
      if (value.seconds != null) bits.push(`${value.seconds.toFixed(2)}s`);
      if (value.http_seconds != null) bits.push(`${isZh ? "往返" : "RTT"} ${value.http_seconds.toFixed(2)}s`);
      if (value.queue_seconds != null) bits.push(`${isZh ? "排队" : "Queue"} ${value.queue_seconds.toFixed(2)}s`);
      if (value.preprocess_seconds != null) bits.push(`${isZh ? "预处理" : "Pre"} ${value.preprocess_seconds.toFixed(2)}s`);
      if (value.inference_seconds != null) bits.push(`${isZh ? "推理" : "Infer"} ${value.inference_seconds.toFixed(2)}s`);
      if (value.processed_width && value.processed_height) bits.push(`${isZh ? "模型输入" : "Input"} ${value.processed_width}×${value.processed_height}`);
      if (value.device) bits.push(String(value.device));
      if (value.width && value.height) bits.push(`${value.width}×${value.height}`);
      if (value.bytes != null) bits.push(`${Math.round(value.bytes / 1024)}KB`);
      if (value.request_bytes != null && value.bytes == null) bits.push(`${Math.round(value.request_bytes / 1024)}KB`);
      if (value.remote_split === false) bits.push(isZh ? "服务端未拆分" : "unsplit");
      return bits.join(" ");
    })
    .join(" · ");
}

function cpuBusyPercent(cpu?: ResourceBlock): number | null {
  if (!cpu?.available) return null;
  if (cpu.cpu_percent != null && !Number.isNaN(cpu.cpu_percent)) {
    return Math.max(0, Math.min(100, cpu.cpu_percent));
  }
  const n = cpu.logical_cpus;
  const load = cpu.loadavg?.[0];
  if (!n || load == null || Number.isNaN(load)) return null;
  return Math.min(100, (load / n) * 100);
}

function ramBusyPercent(mem?: ResourceBlock): number | null {
  if (!mem?.available) return null;
  const total = mem.total_bytes;
  const used = mem.used_bytes;
  if (!total || used == null || Number.isNaN(used)) return null;
  return Math.min(100, (used / total) * 100);
}

function gpuBusyPercent(gpu?: ResourceBlock): number | null {
  if (!gpu?.available) return null;
  const value = gpu.devices?.[0]?.utilization_percent;
  if (value == null || Number.isNaN(value)) return null;
  return Math.min(100, value);
}

function Gauge({
  label,
  percent,
  sub,
  icon,
  delay,
}: {
  label: string;
  percent: number | null;
  sub?: string;
  icon: ReactNode;
  delay: number;
}) {
  const { t } = useI18n();
  const known = percent != null && !Number.isNaN(percent);
  const value = known ? Math.max(0, Math.min(100, percent)) : 0;
  const tone = value > 85 ? "var(--danger)" : value > 60 ? "var(--warn)" : "var(--accent)";
  return (
    <motion.div
      className="gauge-card"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: EASE, delay }}
    >
      <div className="gauge-top">
        <span className="gauge-label">
          {icon}
          {label}
        </span>
        <span className={`gauge-state${known ? " on" : ""}`}>{known ? t("Live", "实时") : t("N/A", "暂无")}</span>
      </div>

      <div className="gauge-value">
        {known ? (
          <>
            <AnimatedNumber value={value} format={(latest) => Math.round(latest).toString()} />
            <span className="gauge-unit">%</span>
          </>
        ) : (
          <span className="gauge-unknown">—</span>
        )}
      </div>

      <svg viewBox="0 0 120 64" className="gauge-arc" aria-hidden="true">
        {/* 圆心 (60,56) 半径 46：弧顶留出 stroke 空间，避免被卡片 overflow 裁切 */}
        <path d="M14 56 A46 46 0 0 1 106 56" fill="none" stroke="var(--line-2)" strokeWidth="6" strokeLinecap="round" />
        {known ? (
          <motion.path
            d="M14 56 A46 46 0 0 1 106 56"
            fill="none"
            stroke={tone}
            strokeWidth="6"
            strokeLinecap="round"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: value / 100 }}
            transition={{ duration: 1.1, ease: EASE }}
          />
        ) : null}
      </svg>

      <p className="gauge-sub">{known ? sub : sub || t("No telemetry available on this host", "这台机器暂时没有这项数据")}</p>
    </motion.div>
  );
}

function sortJobs(jobs: JobCard[]) {
  return [...jobs].sort((a, b) => {
    const ta = a.created_at || "";
    const tb = b.created_at || "";
    if (ta !== tb) return ta < tb ? -1 : 1;
    return (a.queue_position || 0) - (b.queue_position || 0);
  });
}

function ActivitySkeleton() {
  return (
    <div>
      <div className="gauges">
        {Array.from({ length: 3 }).map((_, index) => (
          <div className="gauge-card skeleton-card" key={index}>
            <div className="skeleton skeleton-line" style={{ width: "38%" }} />
            <div className="skeleton skeleton-big" style={{ marginTop: 18 }} />
            <div className="skeleton skeleton-line" style={{ width: "62%", marginTop: 22 }} />
          </div>
        ))}
      </div>
      <div className="stage-topology-card skeleton-card">
        <div className="skeleton skeleton-line" style={{ width: 220, marginBottom: 24 }} />
        <div className="topology-track">
          {COLUMNS.map((col) => (
            <div className="topology-segment" key={col.id}>
              <div className="topology-node">
                <div className="skeleton" style={{ width: 58, height: 58, borderRadius: 16 }} />
                <div className="skeleton skeleton-line" style={{ width: 64, marginTop: 10 }} />
              </div>
              <div className="topology-connector">
                <div className="skeleton skeleton-line" style={{ width: "80%" }} />
              </div>
            </div>
          ))}
          <div className="topology-node">
            <div className="skeleton" style={{ width: 76, height: 58, borderRadius: 12 }} />
            <div className="skeleton skeleton-line" style={{ width: 64, marginTop: 10 }} />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ActivityPage() {
  const { lang, t } = useI18n();
  const [error, setError] = useState<string | null>(null);
  const [dash, setDash] = useState<DashboardResponse | null>(null);
  const [selectedStageId, setSelectedStageId] = useState<StageId | null>(null);
  const [expandAll, setExpandAll] = useState(false);

  async function load() {
    try {
      setDash(await api.dashboard());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Failed to load dashboard", "进度读不出来"));
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 2000);
    return () => window.clearInterval(timer);
  }, []);

  async function retry(id: string) {
    try {
      await api.retryJob(id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  }

  function toggleStage(id: StageId) {
    setExpandAll(false);
    setSelectedStageId((prev) => (prev === id ? null : id));
  }

  const resources = dash?.resources;
  const cpu = resources?.host?.cpu;
  const mem = resources?.host?.memory;
  const gpu = resources?.gpu;
  const cpuPercent = cpuBusyPercent(cpu);
  const ramPercent = ramBusyPercent(mem);
  const gpuPercent = gpuBusyPercent(gpu);

  const picaRssMb = resources?.pica_process?.rss_bytes != null
    ? Math.round(resources.pica_process.rss_bytes / 1048576)
    : null;
  const cpuSub = cpuPercent != null && cpu?.loadavg
    ? t(`1m load ${cpu.loadavg[0].toFixed(2)}`, `1 分钟负载 ${cpu.loadavg[0].toFixed(2)}`)
    : cpuPercent != null && cpu?.logical_cpus
      ? `${cpu.logical_cpus} ${t("logical cores", "逻辑核心")}${picaRssMb != null ? ` · CineVec ${picaRssMb} MB` : ""}`
      : cpu?.message;
  const ramSub = ramPercent != null && mem?.used_bytes != null
    ? `${(mem.used_bytes / 1073741824).toFixed(1)}${mem.total_bytes ? ` / ${(mem.total_bytes / 1073741824).toFixed(1)}` : ""} GB ${t("used", "已用")}`
    : mem?.message;
  const primaryGpu = gpu?.devices?.[0];
  const gpuVram = primaryGpu?.memory_used_mib != null && primaryGpu?.memory_total_mib != null
    ? ` · ${(primaryGpu.memory_used_mib / 1024).toFixed(1)}/${(primaryGpu.memory_total_mib / 1024).toFixed(1)} GB`
    : "";
  const gpuSub = gpuPercent != null
    ? primaryGpu?.name ? `${primaryGpu.name}${gpuVram}` : undefined
    : gpu?.message;

  const extras = useMemo(() => {
    const scan = dash?.stages.find((s) => s.id === "scan");
    const cleanup = dash?.stages.find((s) => s.id === "cleanup");
    const bits = [];
    if (scan && scan.total) bits.push(`${t("Scan", "扫描")} ${scan.total}`);
    if (cleanup && cleanup.total) bits.push(`${t("Cleanup", "清理")} ${cleanup.total}`);
    return bits.join(" · ");
  }, [dash, t]);

  const stageById = useMemo(() => {
    const map: Record<string, DashboardStage | undefined> = {};
    for (const s of dash?.stages ?? []) map[s.id] = s;
    return map;
  }, [dash]);

  const registerCounts = stageCounts(stageById.register);
  const indexCounts = stageCounts(stageById.index_write);
  const readyCount = indexCounts.completed + registerCounts.completed;

  const selectedStageCol = selectedStageId ? COLUMNS.find((c) => c.id === selectedStageId) ?? null : null;
  const selectedStageIdx = selectedStageId ? COLUMNS.findIndex((c) => c.id === selectedStageId) : -1;
  const selectedStageData = selectedStageId ? stageById[selectedStageId] : undefined;
  const selectedCounts = stageCounts(selectedStageData);
  const selectedJobs = useMemo(
    () => sortJobs(selectedStageData?.jobs ?? []),
    [selectedStageData],
  );
  const colLabel = (col: (typeof COLUMNS)[number]) => translate(lang, col.labelEn, col.labelZh);

  return (
    <div>
      <PageHead
        eyebrow="ACTIVITY"
        title={t("Activity", "仪表盘")}
        desc={t(
          "Live host telemetry above; click any pipeline stage or line below to inspect queued and completed jobs.",
          "上面看这台机器忙不忙，下面节点与连线展示各阶段实时负载，点击任意阶段查看完整任务明细。",
        )}
        actions={
          <div className="live-actions">
            <span className="live-chip">
              <span className="live-dot" />
              {t("Auto-refresh 2s", "每 2 秒自动刷新")}
            </span>
            <button type="button" className="icon-btn" onClick={() => void load()} aria-label={t("Refresh now", "立即刷新")}>
              <ArrowsClockwise size={16} />
            </button>
          </div>
        }
      />

      <AnimatePresence>
        {error ? (
          <motion.div key="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <Banner kind="danger" title={t("Error", "没做成")}>{error}</Banner>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {!dash ? (
        <ActivitySkeleton />
      ) : (
        <>
          <div className="gauges">
            <Gauge
              label="CPU"
              icon={<Cpu size={15} />}
              percent={cpuPercent}
              delay={0}
              sub={cpuSub}
            />
            <Gauge
              label="RAM"
              icon={<Memory size={15} />}
              percent={ramPercent}
              delay={0.06}
              sub={ramSub}
            />
            <Gauge
              label="GPU"
              icon={<GraphicsCard size={15} />}
              percent={gpuPercent}
              delay={0.12}
              sub={gpuSub}
            />
          </div>

          <motion.section
            className="stage-topology-card"
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.55, ease: EASE, delay: 0.14 }}
          >
            <div className="topology-head">
              <div className="topology-head-left">
                <span className="topology-head-hint">
                  {t("Pipeline · Click any stage icon or line for details", "处理流水线 · 点击阶段图标或连线查看详情")}
                </span>
                <span className="traffic-legend" aria-label={t("Traffic legend", "路线拥堵图例")}>
                  <span className="traffic-legend-item"><i className="traffic-dot clear" />{t("Clear", "畅通")}</span>
                  <span className="traffic-legend-item"><i className="traffic-dot busy" />{t("Active (1–4)", "进行中 (1–4)")}</span>
                  <span className="traffic-legend-item"><i className="traffic-dot congested" />{t("Congested (≥5)", "拥堵 (≥5)")}</span>
                </span>
              </div>
              <div className="topology-head-actions">
                {selectedStageId && !expandAll ? (
                  <button
                    type="button"
                    className="btn ghost"
                    onClick={() => setSelectedStageId(null)}
                  >
                    {t("Collapse", "收起详情")}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => {
                    setSelectedStageId(null);
                    setExpandAll((prev) => !prev);
                  }}
                >
                  {expandAll ? (
                    <>
                      {t("Collapse all", "收起全部")} <CaretUp size={13} />
                    </>
                  ) : (
                    <>
                      {t("Expand all", "展开全部")} <CaretDown size={13} />
                    </>
                  )}
                </button>
              </div>
            </div>

            <div className="topology-track" role="region" aria-label={t("Four-stage pipeline topology", "四阶段处理流水线拓扑图")}>
              {COLUMNS.map((col, idx) => {
                const label = colLabel(col);
                const counts = stageCounts(stageById[col.id]);
                const isSelected = !expandAll && selectedStageId === col.id;
                const traffic = stageTrafficLevel(counts);
                return (
                  <div className="topology-segment" key={col.id}>
                    <button
                      type="button"
                      className={`topology-node traffic-${traffic}${isSelected ? " is-selected" : ""}`}
                      onClick={() => toggleStage(col.id)}
                      aria-expanded={isSelected || expandAll}
                      title={t(`${label} (click for full details)`, `${label}（点击查看完整信息）`)}
                    >
                      <span className="topology-icon-wrap">
                        <StageWireIcon id={col.id} />
                      </span>
                      <span className="topology-node-label">
                        <span className="topology-index">{String(idx + 1).padStart(2, "0")}</span>
                        {label}
                      </span>
                      <span className="topology-node-sub">
                        {counts.failed > 0
                          ? t(`Failed ${counts.failed} · Done ${counts.completed}`, `失败 ${counts.failed} · 完成 ${counts.completed}`)
                          : t(`Done ${counts.completed}`, `完成 ${counts.completed}`)}
                      </span>
                    </button>

                    <button
                      type="button"
                      className={`topology-connector traffic-${traffic}${isSelected ? " is-selected" : ""}${counts.running > 0 ? " has-running" : ""}${counts.waiting > 0 ? " has-waiting" : ""}`}
                      onClick={() => toggleStage(col.id)}
                      aria-label={t(`${label}: ${counts.running} running, ${counts.waiting} waiting`, `${label}阶段：${counts.running} 正在执行，${counts.waiting} 正在等待`)}
                      aria-expanded={isSelected || expandAll}
                    >
                      <span className="topology-metric up">
                        <span className="topology-arrow" aria-hidden="true">↑</span>
                        <span className="topology-num">{counts.running}</span>
                        <span className="topology-unit">{t("Running", "正在执行")}</span>
                      </span>

                      <span className="topology-rail" aria-hidden="true">
                        <span className="topology-dots" />
                        <span className="topology-line">
                          <span className="topology-stage-tag">{label}</span>
                        </span>
                        <span className="topology-dots" />
                      </span>

                      <span className="topology-metric down">
                        <span className="topology-arrow" aria-hidden="true">↓</span>
                        <span className="topology-num">{counts.waiting}</span>
                        <span className="topology-unit">{t("Waiting", "正在等待")}</span>
                      </span>
                    </button>
                  </div>
                );
              })}

              <button
                type="button"
                className={`topology-node topology-terminal${!expandAll && selectedStageId === "index_write" ? " is-selected" : ""}`}
                onClick={() => toggleStage("index_write")}
                aria-expanded={(!expandAll && selectedStageId === "index_write") || expandAll}
                title={t("Total ready items (click to inspect Index stage)", "检索就绪总数（点击查看写入索引明细）")}
              >
                <span className="topology-monitor-frame">
                  <svg viewBox="0 0 88 64" className="topology-monitor-svg" aria-hidden="true">
                    <rect x="6" y="7" width="76" height="44" rx="4" fill="none" stroke="currentColor" strokeWidth="1.5" />
                    <path d="M36 51v6M52 51v6M28 57h32" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  </svg>
                  <span className="topology-monitor-value">
                    <strong>{readyCount}</strong>
                    <small>{t("items", "项")}</small>
                  </span>
                </span>
                <span className="topology-node-label">{t("Ready", "检索就绪")}</span>
                <span className="topology-node-sub">{t("Searchable / Done", "可搜 / 已完成")}</span>
              </button>
            </div>
          </motion.section>

          <AnimatePresence initial={false}>
            {!expandAll && selectedStageCol ? (
              <motion.section
                key={`detail-${selectedStageCol.id}`}
                className="topology-detail-panel"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.24, ease: EASE }}
              >
                <header className="topology-detail-head">
                  <div className="topology-detail-title">
                    <span className="queue-index">{String(selectedStageIdx + 1).padStart(2, "0")}</span>
                    <h3>{colLabel(selectedStageCol)}</h3>
                    <span className="queue-count">{selectedCounts.total}</span>
                  </div>
                  <div className="topology-detail-badges">
                    <Pill status={selectedCounts.running > 0 ? "info" : "ok"}>
                      {t("Running", "正在执行")} {selectedCounts.running}
                    </Pill>
                    <Pill status={selectedCounts.waiting > 0 ? "warn" : "info"}>
                      {t("Waiting", "正在等待")} {selectedCounts.waiting}
                    </Pill>
                    {selectedCounts.paused > 0 ? <Pill status="info">{t("Paused", "已暂停")} {selectedCounts.paused}</Pill> : null}
                    {selectedCounts.blocked > 0 ? <Pill status="warn">{t("Blocked", "阻塞")} {selectedCounts.blocked}</Pill> : null}
                    <Pill status="ok">{t("Done", "已完成")} {selectedCounts.completed}</Pill>
                    {selectedCounts.failed > 0 ? <Pill status="warn">{t("Failed", "失败")} {selectedCounts.failed}</Pill> : null}
                  </div>
                  <div className="topology-detail-actions">
                    <button
                      type="button"
                      className="btn ghost"
                      onClick={() => {
                        setSelectedStageId(null);
                        setExpandAll(true);
                      }}
                    >
                      {t("Expand all 4 stages", "展开全部四列")}
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      onClick={() => setSelectedStageId(null)}
                      aria-label={t("Close", "收起")}
                    >
                      <X size={15} />
                    </button>
                  </div>
                </header>

                {selectedJobs.length === 0 ? (
                  <div className="queue-empty">
                    <span aria-hidden="true" />
                    {t("Idle · No jobs in this stage", "空闲 · 当前阶段暂无任务")}
                  </div>
                ) : (
                  <div className="topology-detail-grid">
                    <AnimatePresence mode="popLayout" initial={false}>
                      {selectedJobs.map((job) => (
                        <JobCardView key={job.id} job={job} onRetry={(id) => void retry(id)} />
                      ))}
                    </AnimatePresence>
                  </div>
                )}
              </motion.section>
            ) : null}

            {expandAll ? (
              <motion.div
                key="expand-all-board"
                className="queue-board topology-expanded-board"
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
                transition={{ duration: 0.24, ease: EASE }}
              >
                {COLUMNS.map((col, colIndex) => {
                  const jobs = sortJobs(stageById[col.id]?.jobs ?? []);
                  return (
                    <section className="queue-col" key={col.id}>
                      <header className="queue-head">
                        <div className="queue-title">
                          <span className="queue-index">{String(colIndex + 1).padStart(2, "0")}</span>
                          <h3>{colLabel(col)}</h3>
                        </div>
                        <span className="queue-count">{jobs.length}</span>
                      </header>
                      <div className="queue-list">
                        <AnimatePresence mode="popLayout" initial={false}>
                          {jobs.map((job) => (
                            <JobCardView key={job.id} job={job} onRetry={(id) => void retry(id)} />
                          ))}
                        </AnimatePresence>
                        {jobs.length === 0 ? (
                          <div className="queue-empty">
                            <span aria-hidden="true" />
                            {t("Idle", "空闲")}
                          </div>
                        ) : null}
                      </div>
                    </section>
                  );
                })}
              </motion.div>
            ) : null}
          </AnimatePresence>

          {extras ? <p className="hint queue-extras">{extras} ({t("not shown in the 4 stages", "不进上面四列")})</p> : null}
        </>
      )}
    </div>
  );
}
