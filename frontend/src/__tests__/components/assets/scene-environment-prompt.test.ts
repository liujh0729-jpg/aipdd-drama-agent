// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect } from "vitest";

import {
  parseEnvironmentPrompt,
  serializeEnvironmentPrompt,
} from "@/components/assets/scene-environment-prompt";

const FULL_PROMPT = [
  "正面：核心视觉是一张长条木质工作桌。",
  "左侧：有一扇通往厨房的狭窄门洞。",
  "右侧：空间摆放着一张简易木架。",
  "背面：一扇老旧的双开木窗。",
  "光源：室内主灯熄灭，冷蓝色强光主导。",
  "材质/风格：典型的90年代中式民居内饰。",
  "禁止元素：现代智能家居、极简主义装修。",
].join("\n");

describe("parseEnvironmentPrompt", () => {
  it("splits a full 7-heading prompt into its sections", () => {
    const s = parseEnvironmentPrompt(FULL_PROMPT);
    expect(s.front).toBe("核心视觉是一张长条木质工作桌。");
    expect(s.left).toBe("有一扇通往厨房的狭窄门洞。");
    expect(s.right).toBe("空间摆放着一张简易木架。");
    expect(s.back).toBe("一扇老旧的双开木窗。");
    expect(s.light).toBe("室内主灯熄灭，冷蓝色强光主导。");
    expect(s.material).toBe("典型的90年代中式民居内饰。");
    expect(s.forbidden).toBe("现代智能家居、极简主义装修。");
  });

  it("accepts both halfwidth and fullwidth colons", () => {
    const s = parseEnvironmentPrompt("正面: front\n背面：back");
    expect(s.front).toBe("front");
    expect(s.back).toBe("back");
  });

  it("keeps multi-line section content until the next heading", () => {
    const s = parseEnvironmentPrompt("正面：line1\nstill front\n左侧：l");
    expect(s.front).toBe("line1\nstill front");
    expect(s.left).toBe("l");
  });

  it("does not treat a keyword mid-sentence as a heading", () => {
    const s = parseEnvironmentPrompt("正面：这里提到背面也只是描述");
    expect(s.front).toBe("这里提到背面也只是描述");
    expect(s.back).toBe("");
  });

  it("keeps legacy / unlabeled prompts whole in the front field", () => {
    const s = parseEnvironmentPrompt("一段没有任何标题的自由文本");
    expect(s.front).toBe("一段没有任何标题的自由文本");
    expect(s.left).toBe("");
  });

  it("returns all-empty sections for blank input", () => {
    const s = parseEnvironmentPrompt("");
    expect(Object.values(s).every((v) => v === "")).toBe(true);
  });
});

describe("serializeEnvironmentPrompt", () => {
  it("round-trips a full prompt", () => {
    expect(serializeEnvironmentPrompt(parseEnvironmentPrompt(FULL_PROMPT))).toBe(
      FULL_PROMPT,
    );
  });

  it("omits empty sections", () => {
    const out = serializeEnvironmentPrompt({
      front: "f",
      left: "",
      right: "",
      back: "b",
      light: "",
      material: "",
      forbidden: "",
    });
    expect(out).toBe("正面：f\n背面：b");
  });

  it("trims section values", () => {
    const out = serializeEnvironmentPrompt({
      front: "  f  ",
      left: "",
      right: "",
      back: "",
      light: "",
      material: "",
      forbidden: "",
    });
    expect(out).toBe("正面：f");
  });
});
