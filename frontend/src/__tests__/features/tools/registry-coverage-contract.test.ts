// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 工具栏目注册表契约：新增一个工具必须同时具备「TOOLS 条目 +
// ToolsPage 分发 + 菜单入口 + 8 份语言包词条」四件事，缺任何一件都在这里变红。
// 起因：动效大片链路（案例图册 / 逐帧渲染 / 剪映交付）早已接通，却没进 TOOLS，
// 用户在「工具」页一个入口都看不见——原先只硬编码菜单名册的用例拦不住漏注册。
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { TOOLS, TOOL_GROUPS, TOOL_NAV, type ToolKind } from "@/features/tools/core";

const root = process.cwd();
const read = (path: string) => readFileSync(resolve(root, path), "utf8");

const LOCALES = ["zh", "en", "ja", "es", "fr", "ru", "ar", "vi"];
const localeData: Record<string, Record<string, unknown>> = Object.fromEntries(
  LOCALES.map((lang) => [lang, JSON.parse(read(`public/locales/${lang}/translation.json`))]),
);

function lookup(locale: Record<string, unknown>, dottedKey: string): unknown {
  return dottedKey
    .split(".")
    .reduce<unknown>((acc, part) => (acc as Record<string, unknown> | undefined)?.[part], locale);
}

describe("tools registry coverage", () => {
  it("dispatches a panel in ToolsPage for every registered tool kind", () => {
    const page = read("src/features/tools/ToolsPage.tsx");
    for (const tool of TOOLS) {
      expect(page, `ToolsPage 没有分发 ${tool.kind} 的面板`).toContain(`active === "${tool.kind}"`);
    }
  });

  it("reaches every registered tool through the derived menu", () => {
    const reachable = new Set<ToolKind>();
    for (const entry of TOOL_NAV) {
      if (entry.type === "tool") reachable.add(entry.kind);
      else for (const member of entry.members) reachable.add(member);
    }
    for (const tool of TOOLS) {
      expect(reachable.has(tool.kind), `${tool.kind} 没有菜单入口`).toBe(true);
    }
  });

  it("resolves every tool title/desc key in all shipped locales", () => {
    for (const tool of TOOLS) {
      for (const key of [tool.titleKey, tool.descKey]) {
        for (const lang of LOCALES) {
          const value = lookup(localeData[lang], key);
          expect(typeof value, `${lang} 缺词条 ${key}`).toBe("string");
          expect((value as string).trim().length, `${lang} 词条 ${key} 为空`).toBeGreaterThan(0);
        }
      }
    }
  });

  it("resolves every group entry key in all shipped locales", () => {
    for (const group of Object.values(TOOL_GROUPS)) {
      for (const key of [group.titleKey, group.descKey, group.typeLabelKey]) {
        for (const lang of LOCALES) {
          const value = lookup(localeData[lang], key);
          expect(typeof value, `${lang} 缺词条 ${key}`).toBe("string");
          expect((value as string).trim().length, `${lang} 词条 ${key} 为空`).toBeGreaterThan(0);
        }
      }
    }
  });
});
