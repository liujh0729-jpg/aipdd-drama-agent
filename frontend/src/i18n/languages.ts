// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

/**
 * The supported UI languages, kept in a side-effect-free module.
 *
 * `./index` initializes i18next as an import side effect, so anything that only
 * needs the language list (the account menu, for one) imports from here instead
 * — otherwise every consumer's test has to stand up the full i18next runtime.
 *
 * Adding a locale: add the tag here, add `locales/<tag>/translation.json`, and
 * add its label key to `LANGUAGE_LABEL_KEYS` in `components/layout/header.tsx`.
 */
// [aipdd fork delta] 新增 5 种界面语言：日 / 阿 / 法 / 西 / 俄（用户可选，缺译回落 zh）。
export const SUPPORTED = ["zh", "en", "vi", "ja", "ar", "fr", "es", "ru"] as const;
export type Supported = (typeof SUPPORTED)[number];

export function normalize(lng: string | undefined): Supported {
  const two = (lng ?? "").slice(0, 2).toLowerCase();
  return (SUPPORTED as readonly string[]).includes(two) ? (two as Supported) : "zh";
}

/**
 * [aipdd fork delta] One label key per supported language, kept here so the
 * account menu and the public `LanguageSwitcher` share a single source instead
 * of each maintaining their own copy. The values are endonyms — each language
 * shown in its own script — so they read the same regardless of the active UI
 * language; the i18n keys exist so the strings still live in the locale files.
 */
export const LANGUAGE_LABEL_KEYS: Record<Supported, string> = {
  zh: "header.account.languageChinese",
  en: "header.account.languageEnglish",
  vi: "header.account.languageVietnamese",
  ja: "header.account.languageJapanese",
  ar: "header.account.languageArabic",
  fr: "header.account.languageFrench",
  es: "header.account.languageSpanish",
  ru: "header.account.languageRussian",
};
