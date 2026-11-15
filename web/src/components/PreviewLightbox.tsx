import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { MagnifyingGlass, X } from "@phosphor-icons/react";
import { authorizedMediaUrl } from "../api";
import { useI18n } from "../i18n";
import { softSpring } from "../motion";
import { Portal } from "./ui";

export type PreviewTarget = {
  assetId: string;
  kind: string;
  title: string;
  coverLocator?: string | null;
  startSeconds?: number | null;
  sourceMissing?: boolean;
};

export default function PreviewLightbox({
  target,
  onClose,
  onSearchSimilar,
}: {
  target: PreviewTarget;
  onClose: () => void;
  onSearchSimilar?: (file: File) => void;
}) {
  const { t } = useI18n();
  const [missing, setMissing] = useState(Boolean(target.sourceMissing));
  const cover = target.coverLocator ? authorizedMediaUrl(target.coverLocator) : "";
  const media = authorizedMediaUrl(`/api/assets/${target.assetId}/media`);

  useEffect(() => {
    setMissing(Boolean(target.sourceMissing));
  }, [target.assetId, target.sourceMissing]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previous;
    };
  }, [onClose]);

  async function searchSimilar() {
    if (!cover || !onSearchSimilar) return;
    const response = await fetch(cover);
    if (!response.ok) return;
    const blob = await response.blob();
    onSearchSimilar(new File([blob], `${target.title || "query"}.jpg`, { type: "image/jpeg" }));
  }

  return (
    <Portal>
      <motion.div
        className="lightbox-back"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.25 }}
        onClick={onClose}
      >
        <motion.div
          className="lightbox"
          initial={{ opacity: 0, scale: 0.955, y: 16 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.97, y: 10 }}
          transition={softSpring}
          onClick={(event) => event.stopPropagation()}
        >
          <header className="lightbox-head">
            <div className="lightbox-title">
              <p className="eyebrow">{target.kind === "video" ? "VIDEO" : "IMAGE"}</p>
              <strong>{target.title}</strong>
            </div>
            <button type="button" className="icon-btn lightbox-close" onClick={onClose} aria-label={t("Close", "关闭")}>
              <X size={18} />
            </button>
          </header>

          <div className="lightbox-media">
            {target.kind === "video" ? (
              missing ? (
                <div className="lightbox-missing">
                  {cover ? <img src={cover} alt="" /> : null}
                  <p>{t("Original file is unavailable", "原文件现在不在")}</p>
                </div>
              ) : (
                <video
                  controls
                  autoPlay
                  src={media}
                  poster={cover || undefined}
                  onError={() => setMissing(true)}
                  onLoadedMetadata={(event) => {
                    if (target.startSeconds != null) event.currentTarget.currentTime = target.startSeconds;
                  }}
                />
              )
            ) : cover ? (
              <img src={cover} alt="" />
            ) : (
              <p className="hint">{t("No preview image yet.", "还没有预览图。")}</p>
            )}
          </div>

          <footer className="lightbox-foot">
            <span className="hint">
              {target.sourceMissing
                ? t("Original file is unavailable; showing cached preview only.", "源文件当前不可用，只能看已有预览。")
                : t("Preview streamed directly from your CineVec server.", "预览由家里的服务器直接读取。")}
            </span>
            {onSearchSimilar && cover ? (
              <button type="button" className="btn" onClick={() => void searchSimilar()}>
                <MagnifyingGlass size={15} />
                {t("Find similar with this frame", "用这张图再搜相似")}
              </button>
            ) : null}
          </footer>
        </motion.div>
      </motion.div>
    </Portal>
  );
}
