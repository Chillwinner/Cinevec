import { useEffect, useState, type MouseEvent } from "react";
import { AnimatePresence, motion } from "motion/react";
import { useNavigate } from "react-router-dom";
import { Portal } from "../components/ui";
import type { SystemStatus } from "../types";

type Particle = { id: number; x: number; y: number; vx: number; vy: number };

function TargetIcon({ ok }: { ok: boolean }) {
  return (
    <svg viewBox="0 0 16 16" width="13" height="13" fill="none" aria-hidden="true" className="about-cap-icon">
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeOpacity={ok ? 0.85 : 0.35} strokeWidth="1.2" />
      <circle cx="8" cy="8" r="2.2" fill={ok ? "#16a34a" : "currentColor"} fillOpacity={ok ? 1 : 0.4} />
    </svg>
  );
}

export default function AboutSystemDrawer({
  open,
  onClose,
  status,
  assetTotal,
}: {
  open: boolean;
  onClose: () => void;
  status: SystemStatus | null;
  assetTotal: number | null;
}) {
  const navigate = useNavigate();
  const [copied, setCopied] = useState(false);
  const [particles, setParticles] = useState<Particle[]>([]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const modelSpace = status?.model?.active_space;
  const frameCol = status?.model?.collections?.frame || "未配置向量集合";
  const modelReady =
    ["ok", "ready"].includes(status?.embedding_model.status || "") ||
    Boolean(status?.embedding_model.inference_ready);
  const milvusReady = ["ok", "configured"].includes(status?.milvus.status || "");
  const ffmpegOk = status?.ffmpeg?.status === "ok";
  const storeOk = status?.file_store?.status === "ok";

  const capabilities = [
    { label: `视觉编码 · ${modelSpace?.model_id || "未配置"}`, ok: modelReady },
    { label: `向量空间 · ${modelSpace?.dim ? `${modelSpace.dim}D COSINE` : "未配置"}`, ok: milvusReady },
    { label: "Milvus REST v2 集合隔离", ok: milvusReady },
    { label: "MinIO / S3 源数据只读接入", ok: storeOk },
    { label: "FFmpeg 智能关键帧抽样", ok: ffmpegOk },
    { label: "以图搜图 & 跨模态语义检索", ok: modelReady && milvusReady },
    { label: "SRT / VTT 时空字幕融合检索", ok: status?.subtitle_encoder?.status !== "unconfigured" },
    { label: "3D 曲面柱面 WebGL 画廊", ok: true },
    { label: "单进程 SQLite 异步任务队列", ok: status?.sqlite?.status === "ok" },
    { label: "单视频 Max 聚合 · 多帧直达", ok: true },
  ];

  function copyCollection(e: MouseEvent<HTMLButtonElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const ox = e.clientX - rect.left;
    const oy = e.clientY - rect.top;
    navigator.clipboard?.writeText(frameCol).catch(() => undefined);
    setCopied(true);
    const burst = Array.from({ length: 14 }, (_, idx) => {
      const ang = (Math.PI * 2 * idx) / 14 + Math.random() * 0.3;
      const spd = 22 + Math.random() * 34;
      return {
        id: Date.now() + idx,
        x: ox,
        y: oy,
        vx: Math.cos(ang) * spd,
        vy: Math.sin(ang) * spd,
      };
    });
    setParticles(burst);
    window.setTimeout(() => setCopied(false), 1800);
    window.setTimeout(() => setParticles([]), 700);
  }

  return (
    <Portal>
      <AnimatePresence>
        {open ? (
          <div className="about-drawer-root" role="dialog" aria-modal="true" aria-label="关于 Pica 与系统契约">
            <motion.div
              className="about-backdrop"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
              onClick={onClose}
            />

            <motion.aside
              id="about-panel"
              className="about-panel"
              initial={{ clipPath: "inset(0 0 0 100%)", opacity: 0.6 }}
              animate={{ clipPath: "inset(0 0 0 0%)", opacity: 1 }}
              exit={{ clipPath: "inset(0 0 0 100%)", opacity: 0.6 }}
              transition={{ duration: 0.65, ease: [0.16, 1, 0.3, 1] }}
            >
              <header className="about-head">
                <span className="about-eyebrow">About · System Telemetry</span>
                <button type="button" className="about-close-btn" onClick={onClose}>
                  Close
                </button>
              </header>

              <div className="about-scroll">
                <section className="about-section">
                  <h2 className="about-h2">Pica · Private Cinema Archive</h2>
                  <p className="about-p">
                    面向家庭服务器打造的沉浸式自部署图文与视频检索平台。原始素材始终留存在你的私有 MinIO / S3 中保持只读，不上传任何第三方云端。
                  </p>
                  <p className="about-p">
                    结合多模态语义理解、时幕关联与 WebGL 曲面影院画廊，让每一次检索都像翻阅自己的私人胶片档案馆。
                  </p>
                </section>

                <section className="about-section">
                  <h3 className="about-h3">Capabilities</h3>
                  <div className="about-caps-grid">
                    {capabilities.map((cap) => (
                      <div key={cap.label} className="about-cap-item">
                        <TargetIcon ok={cap.ok} />
                        <span>{cap.label}</span>
                      </div>
                    ))}
                  </div>
                </section>

                <section className="about-section">
                  <h3 className="about-h3">Vector Space & Telemetry</h3>
                  <div className="about-metrics-list">
                    <div className="about-metric-row">
                      <span>模型家族</span>
                      <span className="tabular-nums">{modelSpace?.family || "未配置"}</span>
                    </div>
                    <div className="about-metric-row">
                      <span>模型版本 / 预处理</span>
                      <span className="tabular-nums">
                        {modelSpace ? `${modelSpace.model_version} · ${modelSpace.preprocess_version}` : "—"}
                      </span>
                    </div>
                    <div className="about-metric-row">
                      <span>已登记媒体资产</span>
                      <span className="tabular-nums">{assetTotal != null ? `${assetTotal} 项` : "—"}</span>
                    </div>
                    <div className="about-metric-row">
                      <span>活跃集合</span>
                      <span className="about-col-name" title={frameCol}>
                        {frameCol}
                      </span>
                    </div>
                  </div>
                </section>
              </div>

              <footer className="about-foot">
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    navigate("/library");
                  }}
                >
                  资料库
                </button>
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    navigate("/activity");
                  }}
                >
                  仪表盘
                </button>
                <button type="button" className="about-copy-btn" onClick={copyCollection}>
                  <span>{copied ? "Collection Copied" : "Copy Collection"}</span>
                  {particles.map((pt) => (
                    <span
                      key={pt.id}
                      className="particle-dot"
                      style={{
                        left: pt.x,
                        top: pt.y,
                        ["--vx" as string]: `${pt.vx}px`,
                        ["--vy" as string]: `${pt.vy}px`,
                      }}
                    />
                  ))}
                </button>
              </footer>
            </motion.aside>
          </div>
        ) : null}
      </AnimatePresence>
    </Portal>
  );
}
