import { motion } from "motion/react";
import { FormEvent, useEffect, useState } from "react";
import { CloudArrowUp, X } from "@phosphor-icons/react";
import { api } from "../api";
import { translate, useI18n, type Lang } from "../i18n";
import { modal as modalVariants, overlay as overlayVariants } from "../motion";
import type { Source } from "../types";
import { Portal } from "./ui";

const emptyForm = {
  name: "",
  endpoint: "",
  bucket: "",
  region: "",
  role: "input",
  is_platform_storage: false,
  access_key: "",
  secret_key: "",
};

export function probeLabel(source: Source, lang: Lang = "en") {
  if (source.last_probe_status === "ok") return translate(lang, "Connected", "已连上");
  if (source.last_probe_status === "failed") return translate(lang, "Failed", "没连上");
  return translate(lang, "Not tested", "还没试过连接");
}

export default function ConnectDrive({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: (source: Source) => void;
}) {
  const { t } = useI18n();
  const [form, setForm] = useState(emptyForm);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createSource({
        ...form,
        access_key: form.access_key || null,
        secret_key: form.secret_key || null,
        is_platform_storage: form.is_platform_storage || form.role === "storage" || form.role === "both",
      });
      onSaved(created);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Save failed", "保存失败"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Portal>
      <motion.div
        className="overlay"
        variants={overlayVariants}
        initial="hidden"
        animate="show"
        exit="exit"
        onClick={onClose}
      >
        <motion.div
          className="modal connect-modal"
          variants={modalVariants}
          initial="hidden"
          animate="show"
          exit="exit"
          onClick={(event) => event.stopPropagation()}
        >
          <header className="modal-head">
            <div className="modal-title">
              <span className="panel-icon">
                <CloudArrowUp size={18} />
              </span>
              <div>
                <p className="eyebrow">CONNECT</p>
                <h3>{t("Connect Object Storage", "连接网盘")}</h3>
              </div>
            </div>
            <button type="button" className="icon-btn" onClick={onClose} aria-label={t("Close", "关闭")}>
              <X size={17} />
            </button>
          </header>

          <p className="hint">{t("Enter your S3 / MinIO storage endpoint. Saving does not test connectivity — click Test Connection next.", "填写家里的对象存储地址。保存不等于已经连上，下一步再点「试连接」。")}</p>
          {error ? <p className="form-error">{error}</p> : null}

          <form className="form" onSubmit={onSubmit}>
            <div className="form-grid">
              <label className="field span-2">
                <span>{t("Name", "名称")}</span>
                <input
                  className="input"
                  required
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                  placeholder={t("e.g. Home MinIO", "例如家里的 MinIO")}
                />
              </label>
              <label className="field span-2">
                <span>{t("Endpoint URL", "服务地址")}</span>
                <input
                  className="input"
                  placeholder="http://127.0.0.1:9000"
                  value={form.endpoint}
                  onChange={(event) => setForm({ ...form, endpoint: event.target.value })}
                />
              </label>
              <label className="field">
                <span>{t("Bucket", "存储桶")}</span>
                <input className="input" value={form.bucket} onChange={(event) => setForm({ ...form, bucket: event.target.value })} />
              </label>
              <label className="field">
                <span>{t("Region (optional)", "区域（可空）")}</span>
                <input className="input" value={form.region} onChange={(event) => setForm({ ...form, region: event.target.value })} />
              </label>
              <label className="field span-2">
                <span>{t("Role", "用途")}</span>
                <select className="input" value={form.role} onChange={(event) => setForm({ ...form, role: event.target.value })}>
                  <option value="input">{t("Pick existing files from this bucket", "从这里挑选已有文件")}</option>
                  <option value="storage">{t("Store newly uploaded original files", "用来存放上传的原文件")}</option>
                  <option value="both">{t("Use this bucket for both", "两边都用这个桶")}</option>
                </select>
              </label>
              <label className="check-row span-2">
                <input
                  type="checkbox"
                  checked={form.is_platform_storage}
                  onChange={(event) => setForm({ ...form, is_platform_storage: event.target.checked })}
                />
                {t("Set as default upload storage (existing files stay put)", "设为上传默认存放位置（旧文件不搬家）")}
              </label>
              <label className="field">
                <span>Access Key</span>
                <input
                  className="input"
                  value={form.access_key}
                  onChange={(event) => setForm({ ...form, access_key: event.target.value })}
                  autoComplete="off"
                />
              </label>
              <label className="field">
                <span>{t("Secret Key (stored locally, never echoed)", "Secret Key（只存在本机，界面不回显）")}</span>
                <input
                  className="input"
                  type="password"
                  value={form.secret_key}
                  onChange={(event) => setForm({ ...form, secret_key: event.target.value })}
                  autoComplete="new-password"
                />
              </label>
            </div>

            <footer className="modal-foot">
              <button type="button" className="btn ghost" onClick={onClose}>{t("Cancel", "取消")}</button>
              <button type="submit" className="btn primary" disabled={busy}>{busy ? t("Saving…", "保存中…") : t("Save", "保存")}</button>
            </footer>
          </form>
        </motion.div>
      </motion.div>
    </Portal>
  );
}
