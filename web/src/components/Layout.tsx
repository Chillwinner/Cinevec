import { useEffect, useState } from "react";
import { NavLink, useLocation, useOutlet } from "react-router-dom";
import { AnimatePresence, motion } from "motion/react";
import { SignOut } from "@phosphor-icons/react";
import { useAuth } from "../AuthContext";
import { api } from "../api";
import { useI18n } from "../i18n";
import { EASE } from "../motion";
import AboutSystemDrawer from "../cinema/AboutSystemDrawer";
import Preloader from "../cinema/Preloader";
import OnboardingTour, { ONBOARDING_STORAGE_KEY } from "./OnboardingTour";
import RebuildFab from "./RebuildFab";
import type { SystemStatus } from "../types";

export default function Layout() {
  const outlet = useOutlet();
  const location = useLocation();
  const { auth, username, ready: authReady, logout } = useAuth();
  const { lang, toggleLang, t } = useI18n();

  const links = [
    { to: "/", label: t("Explore", "探索"), end: true, tour: "nav-explore" },
    { to: "/library", label: t("Library", "资料库"), end: false, tour: "nav-library" },
    { to: "/activity", label: t("Activity", "仪表盘"), end: false, tour: "nav-activity" },
    { to: "/settings", label: t("Settings", "设置"), end: false, tour: "nav-settings" },
  ];

  const [aboutOpen, setAboutOpen] = useState(false);
  const [tourOpen, setTourOpen] = useState(false);
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [assetTotal, setAssetTotal] = useState<number | null>(null);
  const [systemReady, setSystemReady] = useState(false);

  useEffect(() => {
    if (!authReady || !systemReady) return;
    try {
      const done = window.localStorage.getItem(ONBOARDING_STORAGE_KEY);
      if (!done) {
        setTourOpen(true);
      }
    } catch {
      // ignore storage errors
    }
  }, [authReady, systemReady]);

  const closeTour = (markDone: boolean) => {
    setTourOpen(false);
    if (markDone) {
      try {
        window.localStorage.setItem(ONBOARDING_STORAGE_KEY, "1");
      } catch {
        // ignore storage errors
      }
    }
  };

  const isCinemaHome = location.pathname === "/";

  useEffect(() => {
    let active = true;
    const loadMeta = async () => {
      try {
        const [st, list] = await Promise.all([
          api.status().catch(() => null),
          api.assets({ limit: 1 }).catch(() => null),
        ]);
        if (!active) return;
        if (st) setStatus(st);
        if (list) setAssetTotal(list.total);
      } finally {
        if (active) setSystemReady(true);
      }
    };
    void loadMeta();
    const timer = window.setInterval(() => {
      void api.status().then((st) => active && setStatus(st)).catch(() => undefined);
    }, 15000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const milvusReady = ["ok", "configured"].includes(status?.milvus.status || "");
  const modelReady =
    ["ok", "ready"].includes(status?.embedding_model.status || "") ||
    Boolean(status?.embedding_model.inference_ready);
  const engineOnline = milvusReady && modelReady;

  return (
    <div className={`cinema-shell${isCinemaHome ? " is-home" : " is-console"}`}>
      <Preloader ready={authReady && systemReady} />

      {/* 浮动极简影院顶部导航栏 */}
      <header className="cinema-hud-bar">
        <NavLink to="/" className="cinema-brand" end>
          <span className={`cinema-brand-dot${engineOnline ? " online" : ""}`} />
          <span className="cinema-brand-name">CINEVEC</span>
          <span className="cinema-brand-sub">CINEMA ARCHIVE</span>
        </NavLink>

        <nav className="cinema-nav" aria-label={t("Main navigation", "主导航")}>
          {links.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              data-tour={item.tour}
              className={({ isActive }) => `cinema-nav-link${isActive ? " active" : ""}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="cinema-hud-right">
          {auth?.enabled && username ? (
            <button
              type="button"
              className="cinema-hud-user"
              onClick={logout}
              title={t(`Signed in as: ${username} (click to sign out)`, `当前账户: ${username}（点击退出）`)}
            >
              <span>{username}</span>
              <SignOut size={13} />
            </button>
          ) : null}

          <button
            type="button"
            className="cinema-about-btn"
            onClick={toggleLang}
            title={t("Switch to Chinese", "切换到 English")}
            aria-label={t("Switch language", "切换语言")}
          >
            {lang === "en" ? "中文" : "EN"}
          </button>

          <button
            type="button"
            className={`cinema-about-btn${tourOpen ? " active" : ""}`}
            onClick={() => setTourOpen(true)}
            title={t("Open onboarding guide", "打开新手引导")}
          >
            {t("GUIDE", "引导")}
          </button>

          <button
            type="button"
            className={`cinema-about-btn${aboutOpen ? " active" : ""}`}
            onClick={() => setAboutOpen(true)}
          >
            ABOUT
          </button>
        </div>
      </header>

      <main className={isCinemaHome ? "cinema-main-home" : "cinema-main-console"}>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={location.pathname}
            className={isCinemaHome ? "cinema-page-home" : "page cinema-page-console"}
            initial={{ opacity: 0, y: isCinemaHome ? 0 : 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: isCinemaHome ? 0 : -8, transition: { duration: 0.16, ease: "easeIn" } }}
            transition={{ duration: 0.38, ease: EASE }}
          >
            {outlet}
          </motion.div>
        </AnimatePresence>
        <RebuildFab />
      </main>

      <AboutSystemDrawer
        open={aboutOpen}
        onClose={() => setAboutOpen(false)}
        status={status}
        assetTotal={assetTotal}
      />

      <OnboardingTour open={tourOpen} status={status} onClose={closeTour} />
    </div>
  );
}
