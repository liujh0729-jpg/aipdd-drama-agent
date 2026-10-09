// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { useReferenceMentionSync } from "@/features/canvas/nodes/useReferenceMentionSync";

type Props = {
  prompt: string;
  ids: string[];
  apply: (next: string) => void;
};

function renderSync(initial: Props) {
  return renderHook(
    ({ prompt, ids, apply }: Props) =>
      useReferenceMentionSync(prompt, [{ prefix: "图片", ids }], apply),
    { initialProps: initial },
  );
}

describe("useReferenceMentionSync", () => {
  it("only records a baseline on first render, leaving the prompt untouched", () => {
    const apply = vi.fn();
    renderSync({ prompt: "让 @图片2 动起来", ids: ["A", "B"], apply });
    expect(apply).not.toHaveBeenCalled();
  });

  it("renumbers mentions when an earlier reference is deleted", () => {
    const apply = vi.fn();
    const { rerender } = renderSync({
      prompt: "让 @图片2 动起来",
      ids: ["A", "B"],
      apply,
    });

    // 删掉 A 的连线 → 剩 B,B 从图片2 变图片1。
    rerender({ prompt: "让 @图片2 动起来", ids: ["B"], apply });

    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledWith("让 @图片1 动起来");
  });

  it("does not fire when only the prompt changes (references stable)", () => {
    const apply = vi.fn();
    const { rerender } = renderSync({
      prompt: "@图片1",
      ids: ["A", "B"],
      apply,
    });

    rerender({ prompt: "@图片1 加了点字", ids: ["A", "B"], apply });

    expect(apply).not.toHaveBeenCalled();
  });

  it("does not fire when the remap produces an identical prompt", () => {
    const apply = vi.fn();
    const { rerender } = renderSync({
      prompt: "没有任何引用",
      ids: ["A", "B"],
      apply,
    });

    // 顺序变了但 prompt 里没有 mention → 不写回。
    rerender({ prompt: "没有任何引用", ids: ["B", "A"], apply });

    expect(apply).not.toHaveBeenCalled();
  });
});
