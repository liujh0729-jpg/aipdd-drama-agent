// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta B] 白模视频节点「从 Blender 拉取」：直连拉取成功写回节点；Blender
// 不可达时弹手动入库兜底，粘贴链接入库后写回节点。i18n 断言用真实 zh 词条（见 setup）。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CANVAS_NODE_TYPES,
  type WhiteModelVideoNodeData,
} from "@/features/canvas/domain/canvasNodes";
import { WhiteModelVideoNode } from "@/features/canvas/nodes/WhiteModelVideoNode";
import { useCanvasStore } from "@/stores/canvasStore";

const pullWhiteModelFromBlender = vi.fn();
const ingestWhiteModelVideo = vi.fn();
const uploadFreezoneVideo = vi.fn();

vi.mock("@/api/ops", () => ({
  pullWhiteModelFromBlender: (...args: unknown[]) =>
    pullWhiteModelFromBlender(...args),
  ingestWhiteModelVideo: (...args: unknown[]) => ingestWhiteModelVideo(...args),
  uploadFreezoneVideo: (...args: unknown[]) => uploadFreezoneVideo(...args),
}));

vi.mock("@/lib/url-params", () => ({
  readUrl: () => ({ project: "demo", canvas: "default" }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

vi.mock("@xyflow/react", async () => {
  const actual =
    await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return {
    ...actual,
    Handle: ({ id, type }: { id?: string; type?: string }) => (
      <div data-testid={`handle-${type ?? "unknown"}-${id ?? "default"}`} />
    ),
    useUpdateNodeInternals: () => () => {},
  };
});

vi.mock("@/features/canvas/ui/NodeHeader", () => ({
  NODE_HEADER_FLOATING_POSITION_CLASS: "",
  NodeHeader: ({ titleText }: { titleText: string }) => <div>{titleText}</div>,
}));

function renderNode(data: WhiteModelVideoNodeData = { videoUrl: null }) {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: "wm1",
        type: CANVAS_NODE_TYPES.whiteModelVideo,
        position: { x: 0, y: 0 },
        data,
      },
    ],
    [],
  );
  return render(
    <WhiteModelVideoNode
      id="wm1"
      type="whiteModelVideoNode"
      data={data}
      selected={false}
      dragging={false}
      draggable
      selectable
      deletable
      zIndex={0}
      isConnectable
      positionAbsoluteX={0}
      positionAbsoluteY={0}
    />,
  );
}

function nodeVideoUrl(): unknown {
  const node = useCanvasStore
    .getState()
    .nodes.find((item) => item.id === "wm1");
  return (node?.data as WhiteModelVideoNodeData | undefined)?.videoUrl;
}

beforeEach(() => {
  pullWhiteModelFromBlender.mockReset();
  ingestWhiteModelVideo.mockReset();
  uploadFreezoneVideo.mockReset();
  useCanvasStore.getState().setCanvasData([], []);
});

describe("WhiteModelVideoNode 从 Blender 拉取", () => {
  it("空态先试拉取；成功把视频直链写回节点", async () => {
    pullWhiteModelFromBlender.mockResolvedValue({
      status: "pulled",
      url: "/static/projects/demo/blender/x.mp4",
      filename: "x.mp4",
    });
    renderNode();

    fireEvent.click(screen.getByRole("button", { name: /从 Blender 拉取/ }));

    await waitFor(() => {
      expect(nodeVideoUrl()).toBe("/static/projects/demo/blender/x.mp4");
    });
    expect(pullWhiteModelFromBlender).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({ canvasId: "default", nodeId: "wm1" }),
    );
  });

  it("Blender 不可达时弹手动入库兜底，粘贴链接入库后写回节点", async () => {
    pullWhiteModelFromBlender.mockResolvedValue({
      status: "unreachable",
      message: "连不上本机 Blender",
    });
    ingestWhiteModelVideo.mockResolvedValue({
      status: "ingested",
      url: "/static/projects/demo/blender/y.mp4",
      filename: "y.mp4",
    });
    renderNode();

    fireEvent.click(screen.getByRole("button", { name: /从 Blender 拉取/ }));

    const input = await screen.findByPlaceholderText("粘贴可访问的视频链接");
    fireEvent.change(input, { target: { value: "https://cdn.example.com/v.mp4" } });
    fireEvent.click(screen.getByRole("button", { name: "入库" }));

    await waitFor(() => {
      expect(nodeVideoUrl()).toBe("/static/projects/demo/blender/y.mp4");
    });
    expect(ingestWhiteModelVideo).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({
        canvasId: "default",
        nodeId: "wm1",
        url: "https://cdn.example.com/v.mp4",
      }),
    );
  });

  it("已有视频时仍提供替换（上传）与再次拉取入口", () => {
    renderNode({ videoUrl: "/static/projects/demo/blender/keep.mp4" });
    expect(
      screen.getByRole("button", { name: /从 Blender 拉取/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "替换" })).toBeInTheDocument();
  });
});
