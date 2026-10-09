// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";

import {
  MentionTextarea,
  findMentionTokenAtSelection,
} from "@/components/episode/beat-workbench/mention-textarea";

describe("findMentionTokenAtSelection", () => {
  const text = "参考 @图片1 收尾"; // @图片1 在 [3,7)
  const labels = ["图片1", "图片2"];

  it("finds the mention token overlapping the double-click selection", () => {
    const hit = findMentionTokenAtSelection(text, labels, 4, 6);
    expect(hit).toEqual({ start: 3, end: 7, label: "图片1" });
  });

  it("returns null on plain text", () => {
    expect(findMentionTokenAtSelection(text, labels, 0, 1)).toBeNull();
  });

  it("returns null when no labels are known", () => {
    expect(findMentionTokenAtSelection(text, [], 4, 6)).toBeNull();
  });
});

describe("MentionTextarea — 双击替换引用", () => {
  it("double-clicking a mention opens the picker and replaces it in place", () => {
    const onChange = vi.fn();
    const { container, getByRole } = render(
      <MentionTextarea
        value="参考 @图片1 收尾"
        mentionLabels={["图片1", "图片2"]}
        onChange={onChange}
      />,
    );

    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
    textarea.setSelectionRange(4, 6); // 选区落在 @图片1 上
    fireEvent.doubleClick(textarea);

    // 替换态 picker 出现，列出全部候选。
    const listbox = getByRole("listbox");
    expect(listbox).toBeTruthy();

    // 选「图片2」替换。
    const option = Array.from(listbox.querySelectorAll("button")).find(
      (b) => b.textContent === "图片2",
    );
    expect(option).toBeTruthy();
    fireEvent.mouseDown(option as Element);

    expect(onChange).toHaveBeenCalledTimes(1);
    const event = onChange.mock.calls[0][0] as { target: { value: string } };
    expect(event.target.value).toBe("参考 @图片2 收尾");
  });

  it("does not open the picker when double-clicking plain text", () => {
    const onChange = vi.fn();
    const { container, queryByRole } = render(
      <MentionTextarea
        value="参考 @图片1 收尾"
        mentionLabels={["图片1", "图片2"]}
        onChange={onChange}
      />,
    );
    const textarea = container.querySelector("textarea") as HTMLTextAreaElement;
    textarea.setSelectionRange(0, 1); // 落在「参」上
    fireEvent.doubleClick(textarea);
    expect(queryByRole("listbox")).toBeNull();
  });
});
