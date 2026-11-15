import { AnimatePresence, motion } from "motion/react";
import { CaretLeft, CaretRight, FilmStrip, ImageSquare } from "@phosphor-icons/react";
import { useI18n } from "../i18n";
import { EASE } from "../motion";

export type CinemaSlideMeta = {
  id: string;
  title: string;
  kind: string;
  score?: number | null;
  timeSeconds?: number | null;
  hitSource?: string | null;
  subtitleExcerpt?: string | null;
};

export default function ArcIndicator({
  items,
  activeIndex,
  scrollFloat,
  onPrev,
  onNext,
  onSelectActive,
}: {
  items: CinemaSlideMeta[];
  activeIndex: number;
  scrollFloat: number;
  onPrev: () => void;
  onNext: () => void;
  onSelectActive: () => void;
}) {
  const { t } = useI18n();
  if (!items.length) return null;
  const current = items[((activeIndex % items.length) + items.length) % items.length];

  // 计算弧线上的高亮滑块位置（与 webtemp 一致的顶部弧形轨道）
  const total = Math.max(items.length, 1);
  const normalized = (((scrollFloat % total) + total) % total) / total;
  const arcLength = 108;
  const segmentLength = Math.max(16, Math.min(34, arcLength / Math.min(total, 6)));
  const dashOffset = -(normalized * (arcLength - segmentLength));

  const formatTime = (sec: number | null | undefined) => {
    if (sec == null || Number.isNaN(sec)) return null;
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };

  const tc = formatTime(current.timeSeconds);

  return (
    <div className="arc-indicator-wrap">
      <div className="arc-svg-holder" aria-hidden="true">
        <svg viewBox="0 0 112 22" width="112" height="22" fill="none">
          <path
            d="M 4 19 Q 56 -6 108 19"
            stroke="rgba(247,247,247,0.22)"
            strokeWidth="1.4"
            strokeLinecap="round"
          />
          <path
            d="M 4 19 Q 56 -6 108 19"
            stroke="#f7f7f7"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeDasharray={`${segmentLength} ${arcLength * 2}`}
            strokeDashoffset={dashOffset}
          />
        </svg>
      </div>

      <div className="arc-title-bar">
        <button
          type="button"
          className="arc-step-btn"
          onClick={onPrev}
          aria-label={t("Previous", "上一张")}
          title={t("Previous (←)", "上一张 (←)")}
        >
          <CaretLeft size={14} weight="bold" />
        </button>

        <button
          type="button"
          className="arc-title-btn"
          onClick={onSelectActive}
          title={t("Open Screening Room to inspect original / details (Enter)", "点击进入放映室查看原片/详情 (Enter)")}
        >
          <span className="arc-index-num tabular-nums">
            {String(((activeIndex % items.length) + items.length) % items.length + 1).padStart(2, "0")}
            <span className="arc-index-sep">/</span>
            {String(items.length).padStart(2, "0")}
          </span>

          <span className="arc-title-roller">
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.span
                key={current.id}
                className="arc-title-text"
                initial={{ y: "100%", opacity: 0 }}
                animate={{ y: "0%", opacity: 1 }}
                exit={{ y: "-100%", opacity: 0 }}
                transition={{ duration: 0.36, ease: EASE }}
              >
                {current.kind === "video" ? (
                  <FilmStrip size={13} className="arc-kind-icon" />
                ) : (
                  <ImageSquare size={13} className="arc-kind-icon" />
                )}
                <span className="arc-name">{current.title}</span>
                {current.score != null ? (
                  <span className="arc-score tabular-nums">{current.score.toFixed(3)}</span>
                ) : null}
                {tc ? <span className="arc-tc tabular-nums">{tc}</span> : null}
              </motion.span>
            </AnimatePresence>
          </span>
        </button>

        <button
          type="button"
          className="arc-step-btn"
          onClick={onNext}
          aria-label={t("Next", "下一张")}
          title={t("Next (→)", "下一张 (→)")}
        >
          <CaretRight size={14} weight="bold" />
        </button>
      </div>
    </div>
  );
}
