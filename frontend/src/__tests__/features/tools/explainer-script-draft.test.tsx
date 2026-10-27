// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 讲解类视频「台词草稿」面板单测。
//
// 守三件事：读（任务 result → 可编辑草稿，形状不对就当没有）、改（逐镜编辑只动这一镜）、
// 交（toLockedScript 只送可渲染的内容，空行/空镜在这里滤掉，后端不必猜空串算什么）。
// 组件断言的是用户看得见的中文，i18n 由 setup.ts 用线上 zh 那份初始化。
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ScriptDraftCard,
  estimateDraftSeconds,
  hasDraftContent,
  readScriptDraft,
  toLockedScript,
  type ExplainerDraft,
  type WhiteboardDraft,
} from "@/features/tools/components/ExplainerScriptDraft";

function explainerDraft(overrides: Partial<ExplainerDraft> = {}): ExplainerDraft {
  return {
    draft_version: 1,
    kind: "explainer",
    title: { big: "RAG", rest: "检索增强生成", en: "", tagline: "" },
    duration_s: 180,
    estimated_seconds: 99,
    shots: [
      {
        chapter: "什么是 RAG",
        sentences: ["第一句。", "第二句。"],
        primitive: "flow",
        headline: "四步走",
        items: ["切块", "入库"],
        numbers: [],
        emphasis: 0,
        motion: "travel",
      },
      {
        chapter: "为什么有效",
        sentences: ["第三句。"],
        primitive: "list",
        headline: "两个好处",
        items: ["可溯源"],
        numbers: ["12"],
        emphasis: -1,
        motion: "lightup",
      },
    ],
    ...overrides,
  };
}

function whiteboardDraft(overrides: Partial<WhiteboardDraft> = {}): WhiteboardDraft {
  return {
    draft_version: 1,
    kind: "whiteboard",
    estimated_seconds: 40,
    scenes: [
      { text: "第一幕旁白。", keyText: "第一幕", scenePrompt: "画一条时间线" },
      { text: "第二幕旁白。", keyText: "", scenePrompt: "画两个方块" },
    ],
    ...overrides,
  };
}

describe("readScriptDraft", () => {
  it("rejects drafts of the other kind instead of half-rendering them", () => {
    // 白板面板读到讲解草稿只能当「还没有草稿」：两族字段完全不同，硬读会渲染空幕。
    expect(readScriptDraft(explainerDraft(), "whiteboard")).toBeNull();
    expect(readScriptDraft(whiteboardDraft(), "explainer")).toBeNull();
    expect(readScriptDraft(null, "explainer")).toBeNull();
    expect(readScriptDraft("草稿", "explainer")).toBeNull();
    expect(readScriptDraft({ kind: "explainer", shots: [] }, "explainer")).toBeNull();
  });

  it("fills in safe defaults for missing fields", () => {
    const draft = readScriptDraft(
      {
        kind: "explainer",
        shots: [{ sentences: ["一句话。"] }],
      },
      "explainer",
    ) as ExplainerDraft;

    expect(draft.draft_version).toBe(1);
    expect(draft.duration_s).toBe(180);
    expect(draft.estimated_seconds).toBe(0);
    expect(draft.title).toEqual({ big: "", rest: "", en: "", tagline: "" });
    // 缺图元/动效时给渲染器画得出来的默认值，而不是空串（空串会被后端归一化掉）。
    expect(draft.shots[0].primitive).toBe("term");
    expect(draft.shots[0].motion).toBe("softin");
    expect(draft.shots[0].emphasis).toBe(-1);
    expect(draft.shots[0].items).toEqual([]);
  });
});

describe("toLockedScript", () => {
  it("drops blank lines, blank entries and empty shots before rendering", () => {
    const locked = toLockedScript(
      explainerDraft({
        shots: [
          {
            chapter: " 第一章 ",
            sentences: [" 第一句。 ", "   ", ""],
            primitive: "term",
            headline: " 标题 ",
            items: ["甲", " "],
            numbers: [],
            emphasis: -1,
            motion: "softin",
          },
          {
            chapter: "第二章",
            sentences: ["  ", ""],
            primitive: "list",
            headline: "",
            items: [],
            numbers: [],
            emphasis: -1,
            motion: "softin",
          },
        ],
      }),
    ) as Record<string, unknown>;

    expect(locked.kind).toBe("explainer");
    expect(locked.draft_version).toBe(1);
    const shots = locked.shots as Array<Record<string, unknown>>;
    expect(shots).toHaveLength(1);
    expect(shots[0].sentences).toEqual(["第一句。"]);
    expect(shots[0].chapter).toBe("第一章");
    expect(shots[0].headline).toBe("标题");
    expect(shots[0].items).toEqual(["甲"]);
  });

  it("keeps only scenes that still have narration", () => {
    const locked = toLockedScript(
      whiteboardDraft({
        scenes: [
          { text: " 第一幕。 ", keyText: " 重点 ", scenePrompt: " 画线 " },
          { text: "   ", keyText: "", scenePrompt: "空幕" },
        ],
      }),
    ) as Record<string, unknown>;

    expect(locked.kind).toBe("whiteboard");
    const scenes = locked.scenes as Array<Record<string, unknown>>;
    expect(scenes).toEqual([
      { text: "第一幕。", keyText: "重点", scenePrompt: "画线" },
    ]);
    expect(hasDraftContent(whiteboardDraft())).toBe(true);
    expect(
      hasDraftContent(
        whiteboardDraft({ scenes: [{ text: "", keyText: "", scenePrompt: "只有画面" }] }),
      ),
    ).toBe(false);
  });

  it("reports an empty draft when every shot was cleared", () => {
    const cleared = explainerDraft({
      shots: explainerDraft().shots.map((shot) => ({ ...shot, sentences: ["  "] })),
    });
    expect(hasDraftContent(cleared)).toBe(false);
    expect(hasDraftContent(explainerDraft())).toBe(true);
  });
});

describe("estimateDraftSeconds", () => {
  it("recomputes from the edited lines instead of the generation-time estimate", () => {
    const draft = explainerDraft({
      estimated_seconds: 99,
      shots: [
        {
          chapter: "",
          sentences: ["中文五个字", "hello"],
          primitive: "term",
          headline: "",
          items: [],
          numbers: [],
          emphasis: -1,
          motion: "softin",
        },
      ],
    });

    // 5 个 CJK 字 + 5 个拉丁字符（0.55 权重）/ 4.2 字每秒 ≈ 1.8 秒。
    expect(estimateDraftSeconds(draft)).toBe(1.8);
    // 白板两幕共 12 个 CJK 字 / 4.2 ≈ 2.9 秒，与卡片上显示的摘要同口径。
    expect(estimateDraftSeconds(whiteboardDraft())).toBe(2.9);
  });
});

describe("ScriptDraftCard", () => {
  it("shows the empty state and the generate action without a draft", () => {
    const onGenerate = vi.fn();
    render(
      <ScriptDraftCard draft={null} onChange={vi.fn()} onGenerate={onGenerate} />,
    );

    expect(screen.getByText("台词草稿")).toBeInTheDocument();
    expect(screen.getByText(/还没有台词草稿/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成台词草稿" }));
    expect(onGenerate).toHaveBeenCalledTimes(1);
  });

  it("summarises shots and seconds and edits only the touched shot", () => {
    const onChange = vi.fn();
    render(
      <ScriptDraftCard draft={explainerDraft()} onChange={onChange} onGenerate={vi.fn()} />,
    );

    // 「约 N 秒」按当前台词现算，不沿用生成时的估算值。
    expect(screen.getByText(/共 2 镜 · 约 2\.9 秒/)).toBeInTheDocument();
    expect(screen.getByText("第 1 镜")).toBeInTheDocument();
    expect(screen.getByText("第 2 镜")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成草稿" })).toBeInTheDocument();

    const textareas = screen.getAllByPlaceholderText(
      "这一镜要念的句子，一行一句；删空就跳过这一镜",
    );
    fireEvent.change(textareas[0], { target: { value: "改过的第一句。\n新加的第二句。" } });

    const next = onChange.mock.calls[0][0] as ExplainerDraft;
    expect(next.shots[0].sentences).toEqual(["改过的第一句。", "新加的第二句。"]);
    // 只动这一镜：另一镜原样带着（草稿是纯数据，编辑不该顺手重排章边界）。
    expect(next.shots[1]).toEqual(explainerDraft().shots[1]);
  });

  it("removes the shot the user deleted and warns that the source changed", () => {
    const onChange = vi.fn();
    render(
      <ScriptDraftCard
        draft={explainerDraft()}
        onChange={onChange}
        onGenerate={vi.fn()}
        sourceChanged
      />,
    );

    expect(screen.getByText(/源文案已改/)).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole("button", { name: "删除这一镜" })[1]);
    const next = onChange.mock.calls[0][0] as ExplainerDraft;
    expect(next.shots).toHaveLength(1);
    expect(next.shots[0].chapter).toBe("什么是 RAG");
  });

  it("discards the draft and shows the waiting/error lines", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <ScriptDraftCard draft={explainerDraft()} onChange={onChange} onGenerate={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "清空草稿" }));
    expect(onChange).toHaveBeenCalledWith(null);

    rerender(
      <ScriptDraftCard
        draft={null}
        onChange={onChange}
        onGenerate={vi.fn()}
        waiting
        error="文本模型不可用"
      />,
    );
    expect(screen.getByText(/草稿生成中/)).toBeInTheDocument();
    expect(screen.getByText("文本模型不可用")).toBeInTheDocument();
    // 生成中禁用按钮，避免连点产生多条草稿任务。
    expect(screen.getByRole("button", { name: "生成台词草稿" })).toBeDisabled();
  });

  it("edits whiteboard scenes per scene", () => {
    const onChange = vi.fn();
    render(
      <ScriptDraftCard draft={whiteboardDraft()} onChange={onChange} onGenerate={vi.fn()} />,
    );

    expect(screen.getByText(/共 2 幕 · 约 2\.9 秒/)).toBeInTheDocument();
    expect(screen.getByText("第 1 幕")).toBeInTheDocument();

    const textareas = screen.getAllByPlaceholderText(
      "这一幕要念的句子（删空即删除这一幕）",
    );
    fireEvent.change(textareas[1], { target: { value: "改过的第二幕。" } });

    const next = onChange.mock.calls[0][0] as WhiteboardDraft;
    expect(next.scenes[1].text).toBe("改过的第二幕。");
    expect(next.scenes[0]).toEqual(whiteboardDraft().scenes[0]);
    // 出片将按这份台词配音，不再调文本模型。
    expect(screen.getAllByText(/出片将按上面这份台词配音/).length).toBeGreaterThan(0);
  });
});
