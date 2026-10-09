// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from "vitest";

import {
  getScriptReviewFeedback,
  mergeTaskLogs,
} from "@/lib/script-feedback";

describe("script feedback helpers", () => {
  it("reports review failure from script_writer task result", () => {
    expect(
      getScriptReviewFeedback({
        review_passed: false,
        review_summary: "第 3 行缺少画面描述",
      }),
    ).toEqual({
      type: "warning",
      key: "episode.script.scriptReviewFailed",
      values: { summary: "第 3 行缺少画面描述" },
    });
  });

  it("reports review success when script_writer task passes review", () => {
    expect(
      getScriptReviewFeedback({
        review_passed: true,
        review_summary: "ok",
      }),
    ).toEqual({
      type: "success",
      key: "episode.script.scriptReviewPassed",
    });
  });

  it("deduplicates task logs while preserving order", () => {
    expect(mergeTaskLogs(["启动", "生成第 1 行"], ["生成第 1 行", "完成"])).toEqual([
      "启动",
      "生成第 1 行",
      "完成",
    ]);
  });
});
