import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  Compass,
  GearSix,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { useI18n } from "../i18n";
import { EASE } from "../motion";
import type { SystemStatus } from "../types";

export const ONBOARDING_STORAGE_KEY = "cinevec_onboarding_v1_done";

type TourStep = {
  id: string;
  kind: "modal" | "spotlight";
  selector?: string;
  placement?: "top" | "bottom" | "left" | "right";
  titleEn: string;
  titleZh: string;
  bodyEn: string;
  bodyZh: string;
  tipEn?: string;
  tipZh?: string;
};

type Rect = { top: number; left: number; width: number; height: number };

const TOUR_STEPS: TourStep[] = [
  {
    id: "welcome-config",
    kind: "modal",
    titleEn: "Welcome to CineVec · Quick Setup Guide",
    titleZh: "欢迎使用 CineVec · 新手配置与操作指南",
    bodyEn:
      "Before searching your media, check the 3 core services below. Docker Compose automatically starts MinIO, embedded Milvus Lite, and the EmbeddingGemma 2 encoder container (first boot downloads weights into hf_cache) — or point PICA_CLIP_HTTP_BASE_URL anytime to an external GPU or custom model.",
    bodyZh:
      "开始检索素材前，先确认下方 3 项核心服务状态。Docker Compose 已内置自动拉起 MinIO、Milvus Lite 单文件向量库与 EmbeddingGemma 2 编码容器（首次启动会自动下载权重到 hf_cache），也可随时修改 PICA_CLIP_HTTP_BASE_URL 接入外部显卡或自研模型。",
  },
  {
    id: "search-modes",
    kind: "spotlight",
    selector: '[data-tour="search-modes"]',
    placement: "top",
    titleEn: "1. Switch Search Modality",
    titleZh: "1. 切换三种检索模式",
    bodyEn:
      "Semantic searches scenes & actions in natural language; Image finds visually similar shots by uploading a reference frame; Lexical matches filenames, tags, and descriptions.",
    bodyZh:
      "「语义」用自然语言找画面和动作；「以图」上传样图找相似镜头；「词法」按文件名、人工标签和描述文案模糊匹配。",
  },
  {
    id: "search-input",
    kind: "spotlight",
    selector: '[data-tour="search-input"]',
    placement: "top",
    titleEn: "2. Director Search Dock",
    titleZh: "2. 导演搜索栏（支持拖图）",
    bodyEn:
      "Type any scene description (e.g. 'sunset by the sea') and press Enter, or drag & drop any reference image directly onto the stage to trigger image search.",
    bodyZh:
      "直接输入画面描述（例如「海边的黄昏」「跑步」）按回车即可检索；也可以把本地图片直接拖拽到页面任意位置触发以图搜图。",
  },
  {
    id: "import-btn",
    kind: "spotlight",
    selector: '[data-tour="import-btn"]',
    placement: "top",
    titleEn: "3. Import Media & Zero-Retention",
    titleZh: "3. 导入素材（支持零原片留存）",
    bodyEn:
      "Upload local photos/videos or scan external S3/MinIO buckets. Uncheck 'Store original in S3' to enable Zero-Retention mode — only lightweight previews and vectors are kept.",
    bodyZh:
      "点击这里上传本地图片/视频，或从外部 S3/MinIO 桶导入。取消勾选「保存原片到 S3」即可开启 Zero-Retention 模式，抽帧后自动删除大原片，只留预览与向量。",
  },
  {
    id: "view-toggle",
    kind: "spotlight",
    selector: '[data-tour="view-toggle"]',
    placement: "bottom",
    titleEn: "4. 3D Cinema & Film Grid",
    titleZh: "4. 3D 影院走廊 / 胶片网格切换",
    bodyEn:
      "Browse hits inside the curved Three.js 3D cylindrical corridor or switch to Film Grid. Click any hit to open the Screening Room and jump straight to the matched video timestamp.",
    bodyZh:
      "可在弧形 Three.js 3D 走廊与平铺网格之间自由切换；点击任意结果进入放映室，视频会自动跳转到命中的关键帧时间戳。",
  },
  {
    id: "nav-library",
    kind: "spotlight",
    selector: '[data-tour="nav-library"]',
    placement: "bottom",
    titleEn: "5. Library & Activity Dashboard",
    titleZh: "5. 资料库与流水线仪表盘",
    bodyEn:
      "Manage tags, custom covers, descriptions, and safe purge in Library; monitor live CPU/RAM/GPU gauges and the 4-stage pipeline (Ingest → Preprocess → Embed → Index) in Activity.",
    bodyZh:
      "在「资料库」里批量管理标签、封面、描述和安全删除；在「仪表盘」实时查看硬件占用以及 4 阶段预处理/向量化队列进度。",
  },
  {
    id: "nav-settings",
    kind: "spotlight",
    selector: '[data-tour="nav-settings"]',
    placement: "bottom",
    titleEn: "6. Settings & Plugin API Tokens",
    titleZh: "6. 设置与插件 Token（接入 CineGram）",
    bodyEn:
      "Configure S3/MinIO storage drives, sampling intervals, login authentication, and generate Bearer API Tokens for external plugins like CineGram (Telegram).",
    bodyZh:
      "在这里管理对象存储源、调整抽帧间隔、开启账号登录保护，或签发 Bearer API Token 供 CineGram（Telegram 插件）等外部工具接入。",
    tipEn: "You can reopen this guide anytime from the GUIDE button in the top bar.",
    tipZh: "随时可以点击右上角「引导」按钮重新打开本教程。",
  },
];

type Props = {
  open: boolean;
  status: SystemStatus | null;
  onClose: (markDone: boolean) => void;
};

export default function OnboardingTour({ open, status, onClose }: Props) {
  const { t } = useI18n();
  const location = useLocation();
  const navigate = useNavigate();
  const [stepIndex, setStepIndex] = useState(0);
  const [targetRect, setTargetRect] = useState<Rect | null>(null);

  const step = TOUR_STEPS[stepIndex] ?? TOUR_STEPS[0];

  useEffect(() => {
    if (open) {
      setStepIndex(0);
    }
  }, [open]);

  // Ensure spotlight steps are viewed on the Explore home page where dock/stage anchors exist
  useEffect(() => {
    if (!open || step.kind !== "spotlight") return;
    if (location.pathname !== "/") {
      navigate("/");
    }
  }, [open, step.kind, location.pathname, navigate]);

  const measure = useCallback(() => {
    if (!open || step.kind !== "spotlight" || !step.selector) {
      setTargetRect(null);
      return;
    }
    const el = document.querySelector(step.selector) as HTMLElement | null;
    if (!el) {
      setTargetRect(null);
      return;
    }
    const r = el.getBoundingClientRect();
    setTargetRect({
      top: r.top,
      left: r.left,
      width: r.width,
      height: r.height,
    });
  }, [open, step]);

  useLayoutEffect(() => {
    measure();
    if (!open || step.kind !== "spotlight") return;
    const t1 = window.setTimeout(measure, 120);
    const t2 = window.setTimeout(measure, 420);
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.clearTimeout(t1);
      window.clearTimeout(t2);
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, step, measure, location.pathname]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose(true);
      if (e.key === "ArrowRight") {
        if (stepIndex < TOUR_STEPS.length - 1) setStepIndex((i) => i + 1);
        else onClose(true);
      }
      if (e.key === "ArrowLeft" && stepIndex > 0) {
        setStepIndex((i) => i - 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, stepIndex, onClose]);

  const popoverStyle = useMemo(() => {
    const cardW = 340;
    const cardH = 220;
    const gap = 16;
    const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
    const vh = typeof window !== "undefined" ? window.innerHeight : 800;

    if (!targetRect) {
      return {
        top: Math.max(24, (vh - cardH) / 2),
        left: Math.max(16, (vw - cardW) / 2),
        width: Math.min(cardW, vw - 32),
      };
    }

    const pref = step.placement || "bottom";
    let top = targetRect.top + targetRect.height + gap;
    let left = targetRect.left + targetRect.width / 2 - cardW / 2;

    if (pref === "top") {
      top = targetRect.top - cardH - gap;
      if (top < 16) top = targetRect.top + targetRect.height + gap;
    } else if (pref === "bottom") {
      if (top + cardH > vh - 16) top = Math.max(16, targetRect.top - cardH - gap);
    }

    left = Math.max(16, Math.min(left, vw - cardW - 16));
    top = Math.max(16, Math.min(top, vh - cardH - 16));

    return { top, left, width: Math.min(cardW, vw - 32) };
  }, [targetRect, step.placement]);

  const storageReady = ["ok", "configured", "ready"].includes(
    status?.file_store?.status || status?.minio_s3?.status || ""
  );
  const milvusReady = ["ok", "configured", "ready"].includes(status?.milvus?.status || "");
  const modelReady =
    ["ok", "ready"].includes(status?.embedding_model?.status || "") ||
    Boolean(status?.embedding_model?.inference_ready);

  const isLast = stepIndex === TOUR_STEPS.length - 1;

  return (
    <AnimatePresence>
      {open ? (
        <div className="tour-root" role="dialog" aria-modal="true" aria-label={t(step.titleEn, step.titleZh)}>
          {/* Backdrop */}
          <motion.div
            className="tour-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={() => (step.kind === "modal" ? undefined : onClose(true))}
          />

          {/* Step 0: Big Configuration & Setup Modal */}
          {step.kind === "modal" ? (
            <motion.div
              key="tour-modal"
              className="tour-big-modal"
              initial={{ opacity: 0, scale: 0.95, y: 12 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 10 }}
              transition={{ duration: 0.28, ease: EASE }}
            >
              <div className="tour-modal-head">
                <div className="tour-modal-title-wrap">
                  <span className="tour-badge">
                    <Compass size={14} weight="fill" />
                    <span>{t("FIRST-RUN GUIDE", "新手上手指南")}</span>
                  </span>
                  <h2>{t(step.titleEn, step.titleZh)}</h2>
                </div>
                <button
                  type="button"
                  className="tour-close-btn"
                  onClick={() => onClose(true)}
                  aria-label={t("Close guide", "关闭引导")}
                >
                  <X size={16} />
                </button>
              </div>

              <p className="tour-modal-desc">{t(step.bodyEn, step.bodyZh)}</p>

              {/* Live System Readiness Checklist */}
              <div className="tour-checklist">
                <div className={`tour-check-row${storageReady ? " ok" : " warn"}`}>
                  <div className="tour-check-icon">
                    {storageReady ? <CheckCircle size={18} weight="fill" /> : <WarningCircle size={18} weight="fill" />}
                  </div>
                  <div className="tour-check-info">
                    <strong>{t("1. Object Storage (MinIO / S3)", "1. 对象存储（MinIO / S3）")}</strong>
                    <span>
                      {storageReady
                        ? t("Connected & ready for previews and uploads.", "已就绪，可用于保存预览图与上传素材。")
                        : t(
                            "Not configured yet. Go to Settings to connect your MinIO/S3 bucket as Platform Storage.",
                            "尚未配置：请前往「设置」登记一个 MinIO/S3 桶作为平台存储（Docker Compose 会自动完成）。"
                          )}
                    </span>
                  </div>
                </div>

                <div className={`tour-check-row${milvusReady ? " ok" : " warn"}`}>
                  <div className="tour-check-icon">
                    {milvusReady ? <CheckCircle size={18} weight="fill" /> : <WarningCircle size={18} weight="fill" />}
                  </div>
                  <div className="tour-check-info">
                    <strong>{t("2. Vector Index (Milvus Lite / Milvus)", "2. 向量数据库（Milvus Lite / Milvus）")}</strong>
                    <span>
                      {milvusReady
                        ? status?.milvus?.message || t("Vector index ready.", "向量库已就绪。")
                        : t(
                            "Set PICA_MILVUS_BACKEND=lite in .env to use zero-setup embedded Milvus Lite.",
                            "未配置：在 .env 中设置 PICA_MILVUS_BACKEND=lite 即可免运维使用内置 Milvus Lite。"
                          )}
                    </span>
                  </div>
                </div>

                <div className={`tour-check-row${modelReady ? " ok" : " warn"}`}>
                  <div className="tour-check-icon">
                    {modelReady ? <CheckCircle size={18} weight="fill" /> : <WarningCircle size={18} weight="fill" />}
                  </div>
                  <div className="tour-check-info">
                    <strong>{t("3. Multimodal Embedding Model", "3. 多模态编码模型（EmbeddingGemma 2 / CLIP）")}</strong>
                    <span>
                      {modelReady
                        ? t("Encoder ready for semantic text & image search.", "编码器已就绪，可进行向量化与语义搜索。")
                        : t(
                            "Set PICA_CLIP_HTTP_BASE_URL in .env to your embedding service URL to enable semantic search.",
                            "尚未就绪：在 .env 中配置 PICA_CLIP_HTTP_BASE_URL 指向你的编码服务地址即可开启语义搜图。"
                          )}
                    </span>
                  </div>
                </div>
              </div>

              {/* Key .env snippet */}
              <div className="tour-env-box">
                <div className="tour-env-head">
                  <GearSix size={14} />
                  <span>{t("Key .env configuration (Docker Compose)", "核心 .env 配置要点（Docker 一键部署）")}</span>
                </div>
                <pre className="tour-env-code">{`PICA_MILVUS_BACKEND=lite                 # 内置 Milvus Lite，免装集群
PICA_AUTO_INIT_PLATFORM_STORAGE=true     # 自动连接内置 MinIO 桶
PICA_CLIP_HTTP_BASE_URL=http://encoder:8001  # 默认内置 EmbeddingGemma 2（也可改外部 GPU 地址）`}</pre>
              </div>

              <div className="tour-modal-footer">
                <button type="button" className="tour-btn ghost" onClick={() => onClose(true)}>
                  {t("Skip for now", "暂不需要（跳过）")}
                </button>
                <div className="tour-footer-right">
                  {!storageReady ? (
                    <button
                      type="button"
                      className="tour-btn"
                      onClick={() => {
                        onClose(true);
                        navigate("/settings");
                      }}
                    >
                      {t("Go to Settings", "先去设置存储")}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="tour-btn primary"
                    onClick={() => setStepIndex(1)}
                  >
                    <span>{t("Start UI Walkthrough", "下一步：认识界面按钮")}</span>
                    <ArrowRight size={14} weight="bold" />
                  </button>
                </div>
              </div>
            </motion.div>
          ) : (
            <>
              {/* Spotlight highlight ring around target UI control */}
              {targetRect ? (
                <motion.div
                  key={`spot-${step.id}`}
                  className="tour-spotlight-ring"
                  initial={{ opacity: 0, scale: 0.96 }}
                  animate={{
                    opacity: 1,
                    scale: 1,
                    top: targetRect.top - 6,
                    left: targetRect.left - 6,
                    width: targetRect.width + 12,
                    height: targetRect.height + 12,
                  }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.24, ease: EASE }}
                />
              ) : null}

              {/* Small floating coachmark popover anchored next to target */}
              <motion.div
                key={`pop-${step.id}`}
                className="tour-popover"
                style={{
                  top: popoverStyle.top,
                  left: popoverStyle.left,
                  width: popoverStyle.width,
                }}
                initial={{ opacity: 0, y: 8, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: 6, scale: 0.97 }}
                transition={{ duration: 0.22, ease: EASE }}
              >
                <div className="tour-pop-head">
                  <span className="tour-step-pill">
                    {stepIndex} / {TOUR_STEPS.length - 1}
                  </span>
                  <button
                    type="button"
                    className="tour-close-btn sm"
                    onClick={() => onClose(true)}
                    aria-label={t("Skip tour", "跳过引导")}
                  >
                    <X size={14} />
                  </button>
                </div>
                <h3 className="tour-pop-title">{t(step.titleEn, step.titleZh)}</h3>
                <p className="tour-pop-body">{t(step.bodyEn, step.bodyZh)}</p>
                {step.tipEn ? <p className="tour-pop-tip">{t(step.tipEn, step.tipZh || step.tipEn)}</p> : null}
                <div className="tour-pop-footer">
                  <button type="button" className="tour-btn ghost sm" onClick={() => onClose(true)}>
                    {t("Skip", "跳过")}
                  </button>
                  <div className="tour-footer-right">
                    <button
                      type="button"
                      className="tour-btn sm"
                      onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
                    >
                      <ArrowLeft size={13} />
                      <span>{t("Back", "上一步")}</span>
                    </button>
                    {isLast ? (
                      <button type="button" className="tour-btn primary sm" onClick={() => onClose(true)}>
                        <span>{t("Done", "完成")}</span>
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="tour-btn primary sm"
                        onClick={() => setStepIndex((i) => i + 1)}
                      >
                        <span>{t("Next", "下一步")}</span>
                        <ArrowRight size={13} weight="bold" />
                      </button>
                    )}
                  </div>
                </div>
              </motion.div>
            </>
          )}
        </div>
      ) : null}
    </AnimatePresence>
  );
}
