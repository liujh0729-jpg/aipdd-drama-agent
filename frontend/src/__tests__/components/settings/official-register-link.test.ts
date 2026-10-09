// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 官方网关的注册入口不能写死域名：站点地址只能从后端下发的
// model-gateway/config（official.baseUrl，取数据库 / NEWAPI_BASE_URL）派生。
// 这里既钉住派生规则，也扫一遍生产源码，防止有人把域名重新硬编码回去。
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { describe, expect, it } from "vitest";

import { officialSiteUrl } from "@/components/settings/settings-dialog";

const root = process.cwd();

function sourceFiles(directory = "src"): string[] {
  const absolute = join(root, directory);
  return readdirSync(absolute, { withFileTypes: true }).flatMap((entry) => {
    const path = join(absolute, entry.name);
    const projectPath = relative(root, path).replace(/\\/g, "/");
    if (entry.isDirectory()) {
      return entry.name === "__tests__" ? [] : sourceFiles(projectPath);
    }
    return /\.[cm]?[jt]sx?$/.test(entry.name) ? [projectPath] : [];
  });
}

describe("officialSiteUrl", () => {
  it("剥掉网关路径后缀，只留站点首页", () => {
    expect(officialSiteUrl("https://gateway.example.com/v1")).toBe(
      "https://gateway.example.com",
    );
    expect(officialSiteUrl("https://gateway.example.com")).toBe(
      "https://gateway.example.com",
    );
    expect(officialSiteUrl("  https://gateway.example.com/v1/  ")).toBe(
      "https://gateway.example.com",
    );
  });

  it("地址缺失或非法时不产链接", () => {
    expect(officialSiteUrl(undefined)).toBe("");
    expect(officialSiteUrl("")).toBe("");
    expect(officialSiteUrl("   ")).toBe("");
    expect(officialSiteUrl("not-a-url")).toBe("");
    expect(officialSiteUrl("javascript:alert(1)")).toBe("");
  });
});

describe("官方网关域名不写死在前端源码里", () => {
  it.each(["susciyuan", "relayclaw"])("没有指向 %s 的硬编码 URL", (host) => {
    // 只钉「写死的地址」本身；注释里提一句品牌名（例如 fork delta 说明）不算。
    const pattern = new RegExp(`https?://[\\w.-]*${host}`);
    const hits = sourceFiles().filter((path) =>
      pattern.test(readFileSync(join(root, path), "utf8")),
    );
    expect(hits).toEqual([]);
  });
});
