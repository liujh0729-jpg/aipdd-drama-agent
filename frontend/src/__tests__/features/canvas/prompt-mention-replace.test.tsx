// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import {
  PromptMentionEditor,
  type MentionCandidate,
} from "@/features/canvas/nodes/PromptMentionEditor";

// jsdom 未实现 scrollIntoView（弹层高亮行的 ref 回调会用到）。
beforeAll(() => {
  if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = vi.fn();
  }
});

const candidates: MentionCandidate[] = [
  { key: "A", name: "图片1", imageUrl: "https://example.com/a.png", index: 1 },
  { key: "B", name: "图片2", imageUrl: "https://example.com/b.png", index: 2 },
];

describe("PromptMentionEditor — 双击替换引用", () => {
  it("double-clicking a chip opens the candidate popover anchored at it", () => {
    const { container } = render(
      <PromptMentionEditor
        value="@图片1 "
        onChange={() => {}}
        candidates={candidates}
      />,
    );

    const chip = container.querySelector(".mention-chip");
    expect(chip).not.toBeNull();
    // 初始没有 popover。
    expect(screen.queryByText("@2")).toBeNull();

    fireEvent.doubleClick(chip as Element);

    // popover 出现,列出全部候选(用右侧 @N 提示定位)。
    expect(screen.getByText("@1")).toBeTruthy();
    expect(screen.getByText("@2")).toBeTruthy();
  });

  it("picking a different candidate replaces the chip in place (serialized to @图片2)", () => {
    const onChange = vi.fn();
    const { container } = render(
      <PromptMentionEditor
        value="@图片1 "
        onChange={onChange}
        candidates={candidates}
      />,
    );

    fireEvent.doubleClick(container.querySelector(".mention-chip") as Element);

    // 选「@2」那一行(候选 图片2)。
    const row = screen.getByText("@2").closest("button");
    expect(row).not.toBeNull();
    fireEvent.mouseDown(row as Element);

    // 序列化后引用从 图片1 换成 图片2(传后端的文本仍带编号)。
    expect(onChange).toHaveBeenCalledWith("@图片2 ");
    // 替换后 chip 仍只有一个,且指向新资源 B。
    const chips = container.querySelectorAll(".mention-chip");
    expect(chips.length).toBe(1);
    expect((chips[0] as HTMLElement).dataset.mention).toBe("B");
  });

  it("does not open the popover when there are no candidates", () => {
    const { container } = render(
      <PromptMentionEditor value="@图片1 " onChange={() => {}} candidates={[]} />,
    );
    // 无候选时 rebuildDOM 不会建 chip(名单为空),双击空内容不报错、不开 popover。
    const chip = container.querySelector(".mention-chip");
    // 没有 chip 可双击 —— 直接确认不存在 popover 文案。
    expect(chip).toBeNull();
    expect(screen.queryByText("@1")).toBeNull();
  });
});
