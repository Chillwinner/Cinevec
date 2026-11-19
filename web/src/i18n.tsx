import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { I18N_DICT } from "./i18nDict";

export type Lang = "en" | "zh" | "ja" | "ko" | "es";

export const SUPPORTED_LANGS: { code: Lang; label: string; htmlLang: string }[] = [
  { code: "en", label: "EN", htmlLang: "en" },
  { code: "zh", label: "中文", htmlLang: "zh-CN" },
  { code: "ja", label: "日本語", htmlLang: "ja" },
  { code: "ko", label: "한국어", htmlLang: "ko" },
  { code: "es", label: "ES", htmlLang: "es" },
];

const VALID_LANGS = new Set<string>(SUPPORTED_LANGS.map((l) => l.code));
const STORAGE_KEY = "cinevec_lang";

function readInitialLang(): Lang {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && VALID_LANGS.has(saved)) return saved as Lang;
  } catch {
    // ignore storage errors
  }
  return "en";
}

export function translate(lang: Lang, en: string, zh: string): string {
  if (lang === "en") return en;
  if (lang === "zh") return zh;
  return I18N_DICT[en]?.[lang] ?? en;
}

type I18nContextValue = {
  lang: Lang;
  setLang: (next: Lang) => void;
  toggleLang: () => void;
  t: (en: string, zh: string) => string;
};

const I18nContext = createContext<I18nContextValue>({
  lang: "en",
  setLang: () => undefined,
  toggleLang: () => undefined,
  t: (en) => en,
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readInitialLang);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore storage errors
    }
  }, []);

  const toggleLang = useCallback(() => {
    const idx = SUPPORTED_LANGS.findIndex((l) => l.code === lang);
    const next = SUPPORTED_LANGS[(idx + 1) % SUPPORTED_LANGS.length].code;
    setLang(next);
  }, [lang, setLang]);

  useEffect(() => {
    const meta = SUPPORTED_LANGS.find((l) => l.code === lang);
    document.documentElement.lang = meta?.htmlLang ?? "en";
  }, [lang]);

  const t = useCallback((en: string, zh: string) => translate(lang, en, zh), [lang]);

  const value = useMemo(() => ({ lang, setLang, toggleLang, t }), [lang, setLang, toggleLang, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
