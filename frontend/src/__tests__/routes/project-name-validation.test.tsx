// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  PROJECT_NAME_MAX_LENGTH,
  getProjectNameValidationKey,
} from "@/routes/_app/index";

describe("项目名称长度校验", () => {
  it("接受 64 字符名称", () => {
    expect(PROJECT_NAME_MAX_LENGTH).toBe(64);
    expect(getProjectNameValidationKey("a".repeat(64))).toBeNull();
  });

  it("拒绝 65 字符名称", () => {
    expect(getProjectNameValidationKey("a".repeat(65))).toBe("project.nameTooLong");
  });

  it("接受中文项目名（aipdd fork：放开中文）", () => {
    expect(getProjectNameValidationKey("我的短剧项目")).toBeNull();
    expect(getProjectNameValidationKey("短剧项目_01")).toBeNull();
  });

  it("仍拒绝空格 / 连字符 / 斜杠等非法字符", () => {
    expect(getProjectNameValidationKey("has space")).toBe("project.nameInvalid");
    expect(getProjectNameValidationKey("has-dash")).toBe("project.nameInvalid");
    expect(getProjectNameValidationKey("has/slash")).toBe("project.nameInvalid");
  });
});
