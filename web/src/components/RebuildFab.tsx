import { motion } from "motion/react";
import { useEffect, useState } from "react";
import { ArrowsClockwise, Sparkle } from "@phosphor-icons/react";
import { api } from "../api";
import { useI18n } from "../i18n";
import { softSpring } from "../motion";

export default function RebuildFab() {
  const { t } = useI18n();
  const [mismatch, setMismatch] = useState(0);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      const [plan, jobs] = await Promise.all([api.rebuildPlan(), api.jobs()]);
      const active = jobs.items.filter(
        (job) => job.job_type === "reindex_profile" && ["waiting", "running", "paused"].includes(job.status),
      );
      setRunning(active.length > 0);
      setMismatch(plan.model_mismatch_count || 0);
      setError(null);
    } catch {
      setMismatch(0);
      setRunning(false);
    }
  }

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 4000);
    return () => window.clearInterval(timer);
  }, []);

  if (!running && mismatch <= 0) return null;

  async function enqueue() {
    if (running) return;
    setRunning(true);
    try {
      await api.rebuildQueue();
      await load();
    } catch (err) {
      setRunning(false);
      setError(err instanceof Error ? err.message : t("Failed to queue re-index", "没排上重算"));
    }
  }

  return (
    <motion.div
      className="rebuild-fab"
      initial={{ opacity: 0, y: 18, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={softSpring}
    >
      {error ? <span className="fab-error">{error}</span> : null}
      <button type="button" className="fab-btn" disabled={running} onClick={() => void enqueue()}>
        <span className={`fab-icon${running ? " spinning" : ""}`}>
          {running ? <ArrowsClockwise size={16} /> : <Sparkle size={16} weight="fill" />}
        </span>
        {running ? t("Re-indexing…", "正在重算") : t("Re-index with active model", "用新模型重新算")}
      </button>
    </motion.div>
  );
}
