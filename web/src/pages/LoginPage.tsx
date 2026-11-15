import { motion } from "motion/react";
import { FormEvent, useState } from "react";
import { ArrowRight, SpinnerGap } from "@phosphor-icons/react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext";
import { Banner } from "../components/ui";
import { useI18n } from "../i18n";
import { EASE } from "../motion";

export default function LoginPage() {
  const { login, auth } = useAuth();
  const { t } = useI18n();
  const [username, setUsername] = useState(auth?.username || "");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      const from = (location.state as { from?: string } | null)?.from;
      navigate(from && from !== "/login" ? from : "/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : t("Sign-in failed", "登录失败"));
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-scene">
      <div className="login-ambient" aria-hidden="true">
        <span className="login-orb one" />
        <span className="login-orb two" />
        <span className="login-grid" />
      </div>

      <motion.div
        className="login-panel"
        initial={{ opacity: 0, y: 22, scale: 0.985 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.7, ease: EASE }}
      >
        <div className="login-brand">
          <span className="login-mark" aria-hidden="true">
            <span />
          </span>
          <span className="eyebrow">{t("CINEVEC · MULTIMODAL ARCHIVE", "CINEVEC · 家庭图文检索")}</span>
        </div>

        <h1 className="login-title">{t("Back to your archive.", "回到家里的图。")}</h1>
        <p className="login-lede">
          {t(
            "Sign in with your instance credentials to obtain a local access token. Tokens stay in this browser only; secrets are never exposed.",
            "使用平台统一账号换取访问令牌。令牌只保存在本机浏览器，页面不会显示签名密钥或已存密码。",
          )}
        </p>

        {auth?.enabled ? null : (
          <Banner kind="info" title={t("Authentication disabled", "登录验证已关闭")}>
            {t("No sign-in required right now. You can enable it anytime in Settings.", "当前不必登录即可使用。可在设置中手动开启。")}
          </Banner>
        )}
        {error ? <Banner kind="danger" title={t("Unable to sign in", "无法登录")}>{error}</Banner> : null}

        <form className="form" onSubmit={onSubmit}>
          <label className="field">
            <span>{t("Username", "账号")}</span>
            <input className="input" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} />
          </label>
          <label className="field">
            <span>{t("Password", "密码")}</span>
            <input className="input" type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          <button type="submit" className="btn primary login-submit" disabled={busy || !username || !password}>
            {busy ? <SpinnerGap size={16} className="spin" /> : null}
            {busy ? t("Signing in…", "登录中…") : t("Sign in", "登录")}
            {busy ? null : <ArrowRight size={15} weight="bold" />}
          </button>
        </form>
      </motion.div>

      <motion.p
        className="login-foot"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.5, duration: 0.6 }}
      >
        {t("Self-hosted · Private instance", "本机部署 · 不对外公开")}
      </motion.p>
    </div>
  );
}
