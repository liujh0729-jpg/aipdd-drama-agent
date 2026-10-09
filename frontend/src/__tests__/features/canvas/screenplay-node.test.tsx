// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] ScreenplayNode 行为：三模式编排复用项目管线 hooks。
//   1. 注册/菜单：screenplay 进 CANVAS_ADD_NODE_TYPES、有 registry 定义与类型守卫；
//   2. 默认落在「故事→剧本」，未同步前「同步到剧本」禁用；点生成调用 generate hooks；
//   3. 派生 tab 触发 build_characters；
//   4. 无项目时只显示提示、不渲染操作区。
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  CANVAS_NODE_TYPES,
  isScreenplayNode,
} from "@/features/canvas/domain/canvasNodes";
import {
  canvasNodeDefinitions,
  getNodeDefinition,
} from "@/features/canvas/domain/nodeRegistry";
import { ScreenplayNode } from "@/features/canvas/nodes/ScreenplayNode";
import { CANVAS_ADD_NODE_TYPES } from "@/features/canvas/ui/canvas-node-menu-shared";
import { useCanvasStore } from "@/stores/canvasStore";

const urlState = vi.hoisted(() => ({ project: "demo", canvas: "default" }));
const navigateMock = vi.hoisted(() => vi.fn());
const hooks = vi.hoisted(() => ({
  importMutateAsync: vi.fn(),
  generateMutateAsync: vi.fn(),
  syncMutate: vi.fn(),
  buildMutateAsync: vi.fn(),
  planEpisodesMutateAsync: vi.fn(),
  planScenesMutateAsync: vi.fn(),
  planPropsMutateAsync: vi.fn(),
  genScriptMutateAsync: vi.fn(),
  uploadMutateAsync: vi.fn(),
  startMutateAsync: vi.fn(),
  storyStatus: null as string | null,
  storyText: "从前有座山",
}));

vi.mock("@/lib/url-params", () => ({
  readUrl: () => ({ ...urlState }),
}));
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigateMock,
}));
vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual<typeof import("@xyflow/react")>("@xyflow/react");
  return { ...actual, Handle: () => <div />, useUpdateNodeInternals: () => vi.fn() };
});
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("@/features/canvas/ui/NodeHeader", () => ({
  NODE_HEADER_FLOATING_POSITION_CLASS: "",
  NodeHeader: () => <div />,
}));
vi.mock("@/features/canvas/ui/NodeResizeHandle", () => ({
  NodeResizeHandle: () => <div />,
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/lib/queries/story-project", () => ({
  STORY_STYLE_VALUES: ["", "spectacle", "suspense"],
  useProjectStorySource: () => ({ data: { data: { text: hooks.storyText } } }),
  useImportProjectStory: () => ({ mutateAsync: hooks.importMutateAsync, isPending: false }),
  useGenerateProjectScript: () => ({ mutateAsync: hooks.generateMutateAsync, isPending: false }),
  useSyncGeneratedScript: () => ({ mutate: hooks.syncMutate, isPending: false }),
  useGeneratedScriptResult: () => ({ data: { data: { status: hooks.storyStatus } } }),
}));
vi.mock("@/lib/queries/characters", () => ({
  useBuildCharacters: () => ({ mutateAsync: hooks.buildMutateAsync, isPending: false }),
}));
vi.mock("@/lib/queries/episodes", () => ({
  usePlanEpisodes: () => ({ mutateAsync: hooks.planEpisodesMutateAsync, isPending: false }),
  usePlanEpisodeScenes: () => ({ mutateAsync: hooks.planScenesMutateAsync, isPending: false }),
  usePlanEpisodeProps: () => ({ mutateAsync: hooks.planPropsMutateAsync, isPending: false }),
}));
vi.mock("@/lib/queries/scripts", () => ({
  useGenerateScript: () => ({ mutateAsync: hooks.genScriptMutateAsync, isPending: false }),
}));
vi.mock("@/lib/queries/ingest", () => ({
  useUploadNovel: () => ({ mutateAsync: hooks.uploadMutateAsync, isPending: false }),
  useStartIngest: () => ({ mutateAsync: hooks.startMutateAsync, isPending: false }),
}));

function seedAndRender(data: Record<string, unknown> = {}) {
  useCanvasStore.getState().setCanvasData(
    [{ id: "sp-1", type: CANVAS_NODE_TYPES.screenplay, position: { x: 0, y: 0 }, data }],
    [],
  );
  const node = useCanvasStore.getState().nodes.find((n) => n.id === "sp-1");
  return render(
    <ScreenplayNode
      id="sp-1"
      type={CANVAS_NODE_TYPES.screenplay}
      data={node?.data as never}
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

describe("ScreenplayNode registry / menu", () => {
  it("registers screenplay in add-node menu and registry", () => {
    expect(CANVAS_ADD_NODE_TYPES).toContain(CANVAS_NODE_TYPES.screenplay);
    expect(getNodeDefinition(CANVAS_NODE_TYPES.screenplay).menuLabelKey).toBe("node.menu.screenplay");
    expect(canvasNodeDefinitions[CANVAS_NODE_TYPES.screenplay].menuIcon).toBe("screenplay");
    expect(isScreenplayNode({ type: CANVAS_NODE_TYPES.screenplay } as never)).toBe(true);
  });
});

describe("ScreenplayNode behaviour", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    urlState.project = "demo";
    hooks.storyStatus = null;
    hooks.generateMutateAsync.mockResolvedValue({ ok: true });
    hooks.buildMutateAsync.mockResolvedValue({ ok: true });
  });

  it("defaults to story tab with sync disabled until a script job completed", () => {
    seedAndRender();
    expect(screen.getByText("node.screenplay.story.generate")).toBeTruthy();
    const sync = screen.getByText("node.screenplay.story.sync").closest("button");
    expect(sync?.hasAttribute("disabled")).toBe(true);
  });

  it("generates screenplay through the project pipeline hook", () => {
    seedAndRender();
    fireEvent.click(screen.getByText("node.screenplay.story.generate"));
    expect(hooks.generateMutateAsync).toHaveBeenCalledWith({ episode_count: 10, story_style: "" });
  });

  it("enables sync and fires it once the job completed", () => {
    hooks.storyStatus = "completed";
    seedAndRender();
    const sync = screen.getByText("node.screenplay.story.sync").closest("button");
    expect(sync?.hasAttribute("disabled")).toBe(false);
    fireEvent.click(sync as Element);
    expect(hooks.syncMutate).toHaveBeenCalled();
  });

  it("derives characters from the derive tab", () => {
    seedAndRender({ mode: "derive" });
    fireEvent.click(screen.getByText("node.screenplay.derive.characters"));
    expect(hooks.buildMutateAsync).toHaveBeenCalled();
  });

  it("shows only a hint when no project is selected", () => {
    urlState.project = "";
    seedAndRender();
    expect(screen.getByText("node.screenplay.noProject")).toBeTruthy();
    expect(screen.queryByText("node.screenplay.story.generate")).toBeNull();
  });
});
