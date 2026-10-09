// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 公共 / 首页可见的语言切换器：与账号菜单同源于 `@/i18n/languages`
// 的 SUPPORTED + LANGUAGE_LABEL_KEYS，映射同一批语种，避免两处漂移。登录页（公开、无账号
// 菜单）与首页头部条都挂它，选中即 i18n.changeLanguage + 持久化到 app-store。
import { useEffect, useRef, useState } from "react";
import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAppStore } from "@/stores/app-store";
import {
  LANGUAGE_LABEL_KEYS,
  normalize,
  SUPPORTED,
  type Supported,
} from "@/i18n/languages";

export function LanguageSwitcher({ className }: { className?: string }) {
  const { t, i18n } = useTranslation();
  const setLanguage = useAppStore((s) => s.setLanguage);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const activeLanguage = normalize(i18n.resolvedLanguage ?? i18n.language);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const switchTo = (lang: Supported) => {
    setOpen(false);
    if (lang === activeLanguage) return;
    void i18n.changeLanguage(lang);
    setLanguage(lang);
  };

  return (
    <div ref={rootRef} className={`relative ${className ?? ""}`}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("header.account.selectLanguage")}
        className="inline-flex h-9 items-center gap-2 rounded-full border border-white/[0.12] bg-white/[0.06] px-3 text-sm text-white/85 backdrop-blur-xl transition-colors hover:border-white/25 hover:bg-white/[0.10] hover:text-white"
      >
        <Languages className="size-4" aria-hidden="true" />
        <span>{t(LANGUAGE_LABEL_KEYS[activeLanguage])}</span>
      </button>
      {open ? (
        <div
          role="menu"
          aria-label={t("header.account.selectLanguage")}
          className="absolute right-0 z-[90] mt-1.5 min-w-[168px] space-y-0.5 rounded-[14px] border border-white/[0.10] bg-[#202020]/88 p-1.5 text-slate-100 shadow-[0_18px_50px_rgba(0,0,0,0.36)] backdrop-blur-xl"
        >
          {SUPPORTED.map((language) => {
            const selected = language === activeLanguage;
            return (
              <button
                key={language}
                type="button"
                role="menuitemradio"
                aria-checked={selected}
                onClick={() => switchTo(language)}
                className={`flex w-full items-center justify-between gap-2 rounded-[9px] px-2.5 py-1.5 text-left text-sm transition-colors hover:bg-white/[0.08] ${
                  selected ? "text-white" : "text-white/72"
                }`}
              >
                <span>{t(LANGUAGE_LABEL_KEYS[language])}</span>
                {selected ? (
                  <span aria-hidden="true" className="text-cyan-300">
                    ✓
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
