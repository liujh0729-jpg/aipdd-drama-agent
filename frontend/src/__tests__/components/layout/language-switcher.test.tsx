// [aipdd fork delta] 公共语言切换器：新增 5 语种（日/阿/法/西/俄）必须可选并驱动 i18n。
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LanguageSwitcher } from "@/components/layout/language-switcher";
import { SUPPORTED } from "@/i18n/languages";

const changeLanguage = vi.fn();
const setLanguage = vi.fn();
let currentLang = "en";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) =>
      ({
        "header.account.selectLanguage": "Select language",
        "header.account.languageChinese": "中文",
        "header.account.languageEnglish": "English",
        "header.account.languageVietnamese": "Tiếng Việt",
        "header.account.languageJapanese": "日本語",
        "header.account.languageArabic": "العربية",
        "header.account.languageFrench": "Français",
        "header.account.languageSpanish": "Español",
        "header.account.languageRussian": "Русский",
      })[key] ?? key,
    i18n: {
      get language() {
        return currentLang;
      },
      get resolvedLanguage() {
        return currentLang;
      },
      changeLanguage,
    },
  }),
}));

vi.mock("@/stores/app-store", () => ({
  useAppStore: (selector: (s: { setLanguage: typeof setLanguage }) => unknown) =>
    selector({ setLanguage }),
}));

beforeEach(() => {
  changeLanguage.mockReset();
  setLanguage.mockReset();
  currentLang = "en";
});

describe("LanguageSwitcher", () => {
  it("registers the five new UI languages", () => {
    expect(SUPPORTED).toEqual(
      expect.arrayContaining(["zh", "en", "vi", "ja", "ar", "fr", "es", "ru"]),
    );
  });

  it("shows the active endonym and lists every supported language", () => {
    render(<LanguageSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "Select language" }));
    for (const label of ["中文", "English", "日本語", "العربية", "Français", "Español", "Русский"]) {
      expect(screen.getByRole("menuitemradio", { name: label })).toBeInTheDocument();
    }
  });

  it("switches language and persists to the app store on pick", () => {
    render(<LanguageSwitcher />);
    fireEvent.click(screen.getByRole("button", { name: "Select language" }));
    fireEvent.click(screen.getByRole("menuitemradio", { name: "日本語" }));
    expect(changeLanguage).toHaveBeenCalledWith("ja");
    expect(setLanguage).toHaveBeenCalledWith("ja");
  });
});
