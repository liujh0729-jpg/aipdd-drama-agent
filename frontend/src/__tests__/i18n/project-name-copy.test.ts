// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta 需求第 2 条] 项目名校验与提示文案必须是同一个口径。
//
// 后端 `validate_project_name` 与前端 `PROJECT_NAME_PATTERN` 早就放行中文了，可语言包
// 里还留着一条「项目名称不能包含中文字符」——它没有任何调用点，是改名时漏删的死键。这类
// 残留比缺键更坏：界面不会显示它，但翻设置/翻文档的人会当真，而它说的那条规矩已经不成立。
// 所以这里钉两头的口径：不能有「禁止中文」的文案复活，校验正则必须真的收中文。
import { readdirSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const LOCALE_DIR = "public/locales";
const SOURCE = "src/routes/_app/index.tsx";

/** 各语言里「不允许中文」的说法（只扫 project.* 子树，避免误伤正常译文）。 */
const FORBIDS_CHINESE: RegExp[] = [
  /不[能可得要]{0,2}包含中文/,
  /不[能可得要]{0,2}含有中文/,
  /中文字符.{0,8}(不[能可]|禁止)/,
  /cannot\s+contain\s+chinese/i,
  /no\s+puede\s+contener\s+caracteres\s+chinos/i,
  /ne\s+peut\s+pas\s+contenir\s+de\s+caract[èe]res\s+chinois/i,
  /中国語(?:文字)?(?:を)?(?:含んでは|使って)い?けません/,
  /не\s+может\s+содержать\s+китайск/i,
  /لا\s+يمكن\s+أن\s+يحتوي\s+على\s+أحرف\s+صينية/,
  /không\s+được\s+chứa\s+xu[hk]\s+(hán|chinese)/i,
];

function localeLangs(): string[] {
  return readdirSync(LOCALE_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();
}

function flatten(value: unknown, prefix = ""): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return prefix ? { [prefix]: String(value ?? "") } : {};
  }
  return Object.entries(value as Record<string, unknown>).reduce<Record<string, string>>(
    (acc, [key, child]) => Object.assign(acc, flatten(child, prefix ? `${prefix}.${key}` : key)),
    {},
  );
}

describe("project name copy and validation agree", () => {
  it.each(localeLangs())("%s has no leftover rule forbidding Chinese project names", (lang) => {
    const flat = flatten(JSON.parse(readFileSync(`${LOCALE_DIR}/${lang}/translation.json`, "utf8")));
    const offenders = Object.entries(flat)
      .filter(([key]) => key.startsWith("project."))
      .filter(([, value]) => FORBIDS_CHINESE.some((pattern) => pattern.test(value)))
      .map(([key, value]) => `${key} = ${value}`);

    expect(offenders).toEqual([]);
  });

  it("keeps the live hint in every locale and says Chinese is allowed in zh", () => {
    for (const lang of localeLangs()) {
      const locale = JSON.parse(readFileSync(`${LOCALE_DIR}/${lang}/translation.json`, "utf8"));
      expect(locale.project?.nameInvalid).toEqual(expect.any(String));
    }
    const zh = JSON.parse(readFileSync(`${LOCALE_DIR}/zh/translation.json`, "utf8"));
    expect(zh.project.nameInvalid).toContain("可包含中文");
  });

  it("accepts Chinese project names in the pattern the form actually uses", () => {
    const source = readFileSync(SOURCE, "utf8");
    const match = source.match(/const PROJECT_NAME_PATTERN = (\/[^\n]+\/[a-z]*);/);
    expect(match, "PROJECT_NAME_PATTERN 换了写法，这里的口径要跟着核").toBeTruthy();

    const pattern = new RegExp(match![1].slice(1, match![1].lastIndexOf("/")));
    expect(pattern.test("雨巷故事")).toBe(true);
    expect(pattern.test("Episode_01")).toBe(true);
    // 路径分隔符仍然不收：名称会进目录名，放开就是把穿越漏洞请进来。
    expect(pattern.test("雨巷/故事")).toBe(false);
    expect(pattern.test("雨巷\\故事")).toBe(false);
  });
});
