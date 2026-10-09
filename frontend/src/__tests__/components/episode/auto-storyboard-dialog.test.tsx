// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「自动化生成分镜图」产物面板单测：钉住默认只勾草图（渲染图是
// 更贵的一步，不该由默认替用户点上）、多选时按「先草图后渲染」的顺序回传、一个都没勾
// 时不给开始、以及重新打开回到默认（上一轮的勾选不能悄悄留到这一轮）。
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { AutoStoryboardDialog } from "@/components/episode/auto-storyboard-dialog";

const onOpenChange = vi.hoisted(() => vi.fn());
const onConfirm = vi.hoisted(() => vi.fn());

const SKETCH = "episode.list.autoStoryboardDialog.targets.sketch.label";
const RENDER = "episode.list.autoStoryboardDialog.targets.render.label";
const CONFIRM = "episode.list.autoStoryboardDialog.confirm";
const CHAIN_HINT = "episode.list.autoStoryboardDialog.chainHint";

function renderDialog() {
  render(
    <AutoStoryboardDialog
      open
      episodeCount={3}
      onOpenChange={onOpenChange}
      onConfirm={onConfirm}
    />,
  );
}

function checkbox(name: string): HTMLElement {
  return screen.getByRole("checkbox", { name: new RegExp(name) });
}

function reopenedDialog(count = 1) {
  return (
    <AutoStoryboardDialog
      open
      episodeCount={count}
      onOpenChange={onOpenChange}
      onConfirm={onConfirm}
    />
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AutoStoryboardDialog", () => {
  it("defaults to storyboard sketches only", () => {
    renderDialog();
    expect(checkbox(SKETCH)).toBeChecked();
    expect(checkbox(RENDER)).not.toBeChecked();
    // 顺序提示只在两个都勾时出现，别在默认态就讲一条没发生的事。
    expect(screen.queryByText(CHAIN_HINT)).not.toBeInTheDocument();
  });

  it("reports sketch then render when both are selected", () => {
    renderDialog();
    fireEvent.click(checkbox(RENDER));
    expect(screen.getByText(CHAIN_HINT)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    // 回传顺序固定为先草图：调用方按这个顺序读「先草图、再据草图出渲染图」。
    expect(onConfirm).toHaveBeenCalledWith(["sketch", "render"]);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("keeps the single-target choice when only sketches are wanted", () => {
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: CONFIRM }));
    expect(onConfirm).toHaveBeenCalledWith(["sketch"]);
  });

  it("refuses to start with nothing selected", () => {
    renderDialog();
    fireEvent.click(checkbox(SKETCH));
    expect(checkbox(SKETCH)).not.toBeChecked();
    expect(screen.getByRole("button", { name: CONFIRM })).toBeDisabled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it("says which episodes are about to run", () => {
    renderDialog();
    expect(
      screen.getByText("episode.list.autoStoryboardDialog.description"),
    ).toBeInTheDocument();
  });

  it("drops last round's picks when the panel is reopened", () => {
    // 面板常驻在页面里（只是开关），上一轮勾的渲染图不能隔一轮还在。
    const { rerender } = render(reopenedDialog());
    fireEvent.click(checkbox(RENDER));
    expect(checkbox(RENDER)).toBeChecked();
    rerender(
      <AutoStoryboardDialog
        open={false}
        episodeCount={1}
        onOpenChange={onOpenChange}
        onConfirm={onConfirm}
      />,
    );
    rerender(reopenedDialog());
    expect(checkbox(RENDER)).not.toBeChecked();
    expect(checkbox(SKETCH)).toBeChecked();
  });
});
