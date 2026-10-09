// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] Composer 定向测试：回合载荷（类型 + 参考素材）与参考 chips 生命周期。
// 资产树拖拽（drop 收口）与命令式句柄（点击直引）同样在这里验证。
// 模型选择器与资产库弹窗均桩掉——数据链由 agent-page / canvas 各自测试覆盖。
import { act, createRef } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Composer, type ComposerHandle } from "@/features/agent/components/Composer";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      values ? `${key}:${JSON.stringify(values)}` : key,
  }),
}));

// 模型选择器桩：暴露类型切换的最小接线（当前值 + 两个切换按钮）。
vi.mock("@/features/agent/components/ComposerModelPicker", () => ({
  ComposerModelPicker: ({
    mode,
    onModeChange,
  }: {
    mode: string;
    onModeChange: (mode: string) => void;
  }) => (
    <div>
      <span data-testid="composer-mode">{mode}</span>
      <button type="button" onClick={() => onModeChange("image")}>
        to-image
      </button>
      <button type="button" onClick={() => onModeChange("video")}>
        to-video
      </button>
    </div>
  ),
}));

// 媒体参数选择器桩：受控 value + 两个模拟「用户显式选择」的按钮（真实档位解析
// 由 composer-media-params / media-params 测试覆盖）。
vi.mock("@/features/agent/components/ComposerMediaParams", () => ({
  ComposerMediaParams: ({
    mode,
    value,
    onChange,
  }: {
    mode: string;
    value: {
      imageSize?: string;
      videoResolution?: string;
      videoDuration?: number;
      videoHdResolution?: string;
    };
    onChange: (next: {
      imageSize?: string;
      videoResolution?: string;
      videoDuration?: number;
      videoHdResolution?: string;
    }) => void;
  }) => (
    <div data-testid="composer-media-params" data-mode={mode}>
      <button type="button" onClick={() => onChange({ ...value, imageSize: "4K" })}>
        set-size-4k
      </button>
      <button
        type="button"
        onClick={() => onChange({ ...value, videoResolution: "720p", videoDuration: 5 })}
      >
        set-video-params
      </button>
      <button type="button" onClick={() => onChange({ ...value, videoHdResolution: "2k" })}>
        set-video-hd
      </button>
    </div>
  ),
}));

// 资产库弹窗桩（懒加载模块同样被 vitest 拦截）：pick 一条图片素材后确认关闭。
vi.mock("@/features/canvas/ui/AssetLibraryModal", () => ({
  AssetLibraryModal: ({
    open,
    onClose,
    onConfirm,
  }: {
    open: boolean;
    onClose: () => void;
    onConfirm?: (selections: { media: string; url: string; name: string }[]) => void;
  }) =>
    open ? (
      <div data-testid="library-modal">
        <button
          type="button"
          onClick={() => {
            onConfirm?.([{ media: "image", url: "https://cdn.example/x.png", name: "x.png" }]);
            onClose();
          }}
        >
          pick-one
        </button>
      </div>
    ) : null,
}));

function renderComposer(onSend: (payload: unknown) => void) {
  return render(
    <Composer project="alpha" streaming={false} onSend={onSend} onStop={vi.fn()} />,
  );
}

describe("Composer", () => {
  it("sends the content with the default text mode and no references", async () => {
    const onSend = vi.fn();
    renderComposer(onSend);
    const user = userEvent.setup();

    await user.type(screen.getByRole("textbox"), "hello");
    await user.click(screen.getByRole("button", { name: "agent.chat.send" }));

    expect(onSend).toHaveBeenCalledWith({ content: "hello", mode: "text", references: [] });
  });

  it("carries the selected dialog type into the turn payload", async () => {
    const onSend = vi.fn();
    renderComposer(onSend);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "to-video" }));
    expect(screen.getByTestId("composer-mode").textContent).toBe("video");

    await user.type(screen.getByRole("textbox"), "make a clip");
    await user.click(screen.getByRole("button", { name: "agent.chat.send" }));

    expect(onSend).toHaveBeenCalledWith({
      content: "make a clip",
      mode: "video",
      references: [],
    });
  });

  it("carries an explicit image resolution pick into the turn payload", async () => {
    const onSend = vi.fn();
    renderComposer(onSend);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "to-image" }));
    await user.click(screen.getByRole("button", { name: "set-size-4k" }));
    await user.type(screen.getByRole("textbox"), "draw a poster");
    await user.click(screen.getByRole("button", { name: "agent.chat.send" }));

    expect(onSend).toHaveBeenCalledWith({
      content: "draw a poster",
      mode: "image",
      references: [],
      imageSize: "4K",
    });
  });

  it("carries explicit video resolution + duration picks into the turn payload", async () => {
    const onSend = vi.fn();
    renderComposer(onSend);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "to-video" }));
    await user.click(screen.getByRole("button", { name: "set-video-params" }));
    await user.type(screen.getByRole("textbox"), "make a 5s clip");
    await user.click(screen.getByRole("button", { name: "agent.chat.send" }));

    expect(onSend).toHaveBeenCalledWith({
      content: "make a 5s clip",
      mode: "video",
      references: [],
      videoResolution: "720p",
      videoDuration: 5,
    });
  });

  it("carries the HD tier pick into the turn payload", async () => {
    const onSend = vi.fn();
    renderComposer(onSend);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "to-video" }));
    await user.click(screen.getByRole("button", { name: "set-video-hd" }));
    await user.type(screen.getByRole("textbox"), "make it crisp");
    await user.click(screen.getByRole("button", { name: "agent.chat.send" }));

    expect(onSend).toHaveBeenCalledWith({
      content: "make it crisp",
      mode: "video",
      references: [],
      videoHdResolution: "2k",
    });
  });

  it("does not carry image params picked earlier when the round is in video mode", async () => {
    const onSend = vi.fn();
    renderComposer(onSend);
    const user = userEvent.setup();

    // 先在图片模式选了分辨率，再切到视频且不碰参数：本回合不应夹带图片参数。
    await user.click(screen.getByRole("button", { name: "to-image" }));
    await user.click(screen.getByRole("button", { name: "set-size-4k" }));
    await user.click(screen.getByRole("button", { name: "to-video" }));
    await user.type(screen.getByRole("textbox"), "clip please");
    await user.click(screen.getByRole("button", { name: "agent.chat.send" }));

    expect(onSend).toHaveBeenCalledWith({
      content: "clip please",
      mode: "video",
      references: [],
    });
  });

  it("attaches picked library references and clears them after sending", async () => {
    const onSend = vi.fn();
    renderComposer(onSend);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "agent.chat.reference.add" }));
    await user.click(await screen.findByRole("button", { name: "pick-one" }));

    // 选中即成为 chat 上方的参考 chip。
    expect(await screen.findByText("x.png")).toBeInTheDocument();

    await user.type(screen.getByRole("textbox"), "use the reference");
    await user.click(screen.getByRole("button", { name: "agent.chat.send" }));

    expect(onSend).toHaveBeenCalledWith({
      content: "use the reference",
      mode: "text",
      references: [{ media: "image", url: "https://cdn.example/x.png", name: "x.png" }],
    });
    // 参考素材随回合一次性附带：发送后清空。
    expect(screen.queryByText("x.png")).toBeNull();
  });

  it("removes a reference chip individually", async () => {
    renderComposer(vi.fn());
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "agent.chat.reference.add" }));
    await user.click(await screen.findByRole("button", { name: "pick-one" }));
    expect(await screen.findByText("x.png")).toBeInTheDocument();

    await user.click(screen.getByTitle("agent.chat.reference.remove"));

    expect(screen.queryByText("x.png")).toBeNull();
  });

  it("accepts an asset dragged from the library tree as a reference", () => {
    renderComposer(vi.fn());
    const payload = JSON.stringify({
      name: "hero.png",
      url: "https://cdn.example/hero.png",
      media: "image",
    });
    const dataTransfer = {
      types: ["application/x-supertale-agent-reference"],
      getData: (type: string) =>
        type === "application/x-supertale-agent-reference" ? payload : "",
      dropEffect: "none",
    };
    const textarea = screen.getByRole("textbox");

    // 拖入悬停：显示落地提示，不落 chip。
    fireEvent.dragOver(textarea, { dataTransfer });
    expect(screen.getByText("agent.chat.dropHint")).toBeInTheDocument();

    fireEvent.drop(textarea, { dataTransfer });

    // 拖入的素材成为参考 chip，提示条收起。
    expect(screen.getByText("hero.png")).toBeInTheDocument();
    expect(screen.queryByText("agent.chat.dropHint")).toBeNull();
  });

  it("ignores foreign drag payloads", () => {
    renderComposer(vi.fn());
    const dataTransfer = {
      types: ["text/plain"],
      getData: () => "just some text",
      dropEffect: "none",
    };

    fireEvent.dragOver(screen.getByRole("textbox"), { dataTransfer });
    expect(screen.queryByText("agent.chat.dropHint")).toBeNull();
    fireEvent.drop(screen.getByRole("textbox"), { dataTransfer });
    expect(screen.queryByTitle("agent.chat.reference.remove")).toBeNull();
  });

  it("adds a reference through the imperative handle (asset tree click path)", () => {
    const ref = createRef<ComposerHandle>();
    render(
      <Composer ref={ref} project="alpha" streaming={false} onSend={vi.fn()} onStop={vi.fn()} />,
    );

    act(() => {
      ref.current?.addReference({
        name: "bg.mp4",
        url: "https://cdn.example/bg.mp4",
        media: "video",
      });
    });

    expect(screen.getByText("bg.mp4")).toBeInTheDocument();
  });
});