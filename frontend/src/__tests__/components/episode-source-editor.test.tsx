// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EpisodeSourceEditor } from "@/components/episode/episode-source-editor";

const labels = {
  rawLabel: "原文",
  rawActionLabel: "点击查看原文",
  noRawText: "暂无原文",
  sourceLabel: "分镜源文本",
  sourceMeta: (count: number) => `编辑后的内容自动保存，当前共 ${count} 行`,
  sourcePlaceholder: "填写分镜源文本",
  linePreviewLabel: "行切分预览",
  lineCount: (count: number) => `${count} 行`,
  noLines: "暂无可生成行",
};

describe("EpisodeSourceEditor", () => {
  it("opens raw content in a dialog and saves edited beat source text on blur", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <EpisodeSourceEditor
        rawContent="第一章原文"
        sourceText="旧分镜源文本"
        labels={labels}
        onSave={onSave}
      />,
    );

    expect(screen.queryByText("第一章原文")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "点击查看原文" }));
    expect(screen.getByText("第一章原文")).toBeInTheDocument();

    const source = screen.getByLabelText("分镜源文本 1");
    expect(source).toHaveValue("旧分镜源文本");

    fireEvent.change(source, { target: { value: "新分镜源文本" } });
    fireEvent.blur(source);

    await waitFor(() => expect(onSave).toHaveBeenCalledWith("新分镜源文本"));
  });

  it("edits source text as one input per generated line", () => {
    render(
      <EpisodeSourceEditor
        rawContent=""
        sourceText={" 第一行\n\n第二行 \r\n 第三行 "}
        labels={labels}
        onSave={vi.fn()}
      />,
    );

    expect(
      screen.getByText("(编辑后的内容自动保存，当前共 3 行)"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("分镜源文本 1")).toHaveValue("第一行");
    expect(screen.getByLabelText("分镜源文本 2")).toHaveValue("第二行");
    expect(screen.getByLabelText("分镜源文本 3")).toHaveValue("第三行");
  });

  it("splits pasted multiline text into editable lines and saves normalized text", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <EpisodeSourceEditor
        rawContent=""
        sourceText="第一行"
        labels={labels}
        onSave={onSave}
      />,
    );

    const source = screen.getByLabelText("分镜源文本 1");
    fireEvent.change(source, { target: { value: "改写第一行\n改写第二行" } });
    expect(screen.getByLabelText("分镜源文本 1")).toHaveValue("改写第一行");
    expect(screen.getByLabelText("分镜源文本 2")).toHaveValue("改写第二行");

    fireEvent.blur(screen.getByLabelText("分镜源文本 2"));

    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith("改写第一行\n改写第二行"),
    );
  });
});
