// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from "react";
import { useAppStore, type Theme } from "@/stores/app-store";

const MEDIA_QUERY = "(prefers-color-scheme: dark)";

function resolveTheme(theme: Theme): "light" | "dark" {
  if (theme === "system") {
    return window.matchMedia(MEDIA_QUERY).matches ? "dark" : "light";
  }
  return theme;
}

function applyTheme(resolved: "light" | "dark") {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(resolved);
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const theme = useAppStore((s) => s.theme);

  useEffect(() => {
    applyTheme(resolveTheme(theme));
    if (theme !== "system") return;

    const mql = window.matchMedia(MEDIA_QUERY);
    const onChange = () => applyTheme(mql.matches ? "dark" : "light");
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [theme]);

  return children;
}

export function useResolvedTheme(): "light" | "dark" {
  const theme = useAppStore((s) => s.theme);
  if (typeof window === "undefined") return "dark";
  return resolveTheme(theme);
}
