"use client";

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { AR, type Dict } from "@/dictionaries/ar";
import { EN } from "@/dictionaries/en";

export type { Dict };
export type Locale = "ar" | "en";

type I18nCtx = {
  locale: Locale;
  t: (key: keyof Dict) => string;
  dir: "rtl" | "ltr";
  toggle: () => void;
};

const Ctx = createContext<I18nCtx>({
  locale: "ar",
  t: (k) => AR[k],
  dir: "rtl",
  toggle: () => {},
});

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState<Locale>("ar");

  useEffect(() => {
    const saved = (localStorage.getItem("locale") as Locale) || "ar";
    setLocale(saved);
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale;
    document.documentElement.dir = locale === "ar" ? "rtl" : "ltr";
  }, [locale]);

  const toggle = useCallback(() => {
    setLocale((prev) => {
      const next: Locale = prev === "ar" ? "en" : "ar";
      localStorage.setItem("locale", next);
      return next;
    });
  }, []);

  const t = useCallback(
    (key: keyof Dict) => (locale === "ar" ? AR[key] : EN[key]) ?? String(key),
    [locale],
  );

  return (
    <Ctx.Provider value={{ locale, t, dir: locale === "ar" ? "rtl" : "ltr", toggle }}>
      {children}
    </Ctx.Provider>
  );
}

export const useI18n = () => useContext(Ctx);
