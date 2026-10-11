// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// 360 全景 / 宫格动作两个面板：提交必须带上解析出来的那个模型与档位。
//
// [aipdd fork delta] WP-D: 额度体系拆除前，这两个面板的回归点是「提交与报价同源」；
// 报价没了之后仍然成立、也仍然要守的契约是「解析出的模型/档位确实进了 payload」。
//
// 单测 `resolveFixedFeatureModelRequest` 只能证明解析结果自洽；这两个面板真正
// 出过的问题是解析完了根本没往 payload 里放 —— 所以断言必须落在 ops helper 收到
// 的 payload 上。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Scene360Overlay } from "@/features/canvas/ui/Scene360Overlay";
import { GridActionConfirmOverlay } from "@/features/canvas/ui/GridActionConfirmOverlay";
import type { GridActionRequest } from "@/features/canvas/ui/GridActionConfirmOverlay";
import type { CanvasNode } from "@/features/canvas/domain/canvasNodes";

vi.mock("@xyflow/react", () => ({
  NodeToolbar: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Position: { Top: "top", Bottom: "bottom" },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// [aipdd fork delta] 360 不再锁死某款一体化模型，改跟「默认生成模型」的默认 image
// （目录首项）。两个用例分别验证：默认落在首项；管理员换默认（改目录顺序）后随动。
const CATALOG_MODEL = {
  catalogId: "cat-77",
  id: "studio-image-v1",
  providerId: "openrouter" as const,
  apiModel: "google/gemini-2.5-flash-image-preview",
  label: "Studio Image",
  resolutionOptions: ["1K", "4K"],
  qualityOptions: ["low", "high"],
};

const SCENE_360_MODEL = {
  catalogId: "cat-g2",
  id: "acme-img-1",
  providerId: "newapi" as const,
  apiModel: "Acme-IMG-1",
  label: "Acme-IMG-1",
  resolutionOptions: ["1K", "2K", "4K"],
  qualityOptions: ["low", "medium", "high"],
};

type CatalogModel = typeof CATALOG_MODEL | typeof SCENE_360_MODEL;

type ImageCatalogState = {
  models: CatalogModel[];
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
};

/** 默认给一条正常目录；单个用例可以改成加载中 / 拉取失败 / 权威空目录。 */
const LOADED_CATALOG: ImageCatalogState = {
  models: [CATALOG_MODEL, SCENE_360_MODEL],
  isLoading: false,
  isFallback: false,
  error: null,
};

let imageCatalogState: ImageCatalogState = LOADED_CATALOG;

// 只替换取数的 hook；`isAuthoritativeEmptyCatalog` 是纯函数判据，面板正是
// 靠它决定禁不禁用提交，必须留真实实现。
vi.mock("@/features/canvas/hooks/useFreezoneImageModels", async (importOriginal) => ({
  ...(await importOriginal<
    typeof import("@/features/canvas/hooks/useFreezoneImageModels")
  >()),
  useFreezoneImageModels: () => imageCatalogState,
  prefetchFreezoneImageModels: () => {},
}));

const submitFreezoneScene360 = vi.fn();
const submitFreezoneTemplateEdit = vi.fn();
const fetchFreezoneJobResult = vi.fn();
const awaitTaskCompletion = vi.fn();

vi.mock("@/api/ops", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/ops")>();
  return {
    ...actual,
    submitFreezoneScene360: (...args: unknown[]) => submitFreezoneScene360(...args),
    submitFreezoneTemplateEdit: (...args: unknown[]) => submitFreezoneTemplateEdit(...args),
    fetchFreezoneJobResult: (...args: unknown[]) => fetchFreezoneJobResult(...args),
  };
});

vi.mock("@/api/tasks", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/api/tasks")>();
  return {
    ...actual,
    awaitTaskCompletion: (...args: unknown[]) => awaitTaskCompletion(...args),
  };
});

vi.mock("@/lib/url-params", () => ({
  readUrl: () => ({ project: "demo", canvas: "default" }),
}));

const NODE: CanvasNode = {
  id: "src-node",
  type: "exportImage",
  position: { x: 0, y: 0 },
  data: { imageUrl: "https://x/src.png", aspectRatio: "1:1" },
} as unknown as CanvasNode;

const JOB_REF = {
  task_type: "freezone_scene_360",
  job_id: "j-1",
  task_key: "freezone_scene_360:j-1",
};

beforeEach(() => {
  vi.clearAllMocks();
  imageCatalogState = LOADED_CATALOG;
  submitFreezoneScene360.mockResolvedValue(JOB_REF);
  submitFreezoneTemplateEdit.mockResolvedValue({ ...JOB_REF, task_type: "freezone_edit" });
  awaitTaskCompletion.mockResolvedValue({ result: { output_url: "https://x/out.png" } });
});

describe("360 全景面板", () => {
  it("跟随目录默认模型（首项）", async () => {
    render(
      <Scene360Overlay node={NODE} imageSource="https://x/src.png?t=1" onClose={() => {}} />,
    );
    fireEvent.click(screen.getByTitle("scene360.submit"));

    await waitFor(() => expect(submitFreezoneScene360).toHaveBeenCalled());
    const payload = submitFreezoneScene360.mock.calls[0][1] as Record<string, unknown>;
    expect(payload.model).toBe(CATALOG_MODEL.apiModel);
    expect(payload.catalogId).toBe(CATALOG_MODEL.catalogId);
    expect(payload.imageSize).toBe("1K");
    expect(payload.quality).toBe("low");
    expect(payload).not.toHaveProperty("aspectRatio");
    expect(screen.queryByText("21:9")).not.toBeInTheDocument();
  });

  it("管理员把默认 image 换到目录首项后，360 随动改用它", async () => {
    imageCatalogState = {
      models: [SCENE_360_MODEL, CATALOG_MODEL],
      isLoading: false,
      isFallback: false,
      error: null,
    };
    render(
      <Scene360Overlay node={NODE} imageSource="https://x/src.png?t=1" onClose={() => {}} />,
    );
    fireEvent.click(screen.getByTitle("scene360.submit"));

    await waitFor(() => expect(submitFreezoneScene360).toHaveBeenCalled());
    const payload = submitFreezoneScene360.mock.calls[0][1] as Record<string, unknown>;
    expect(payload.model).toBe(SCENE_360_MODEL.apiModel);
    expect(payload.catalogId).toBe(SCENE_360_MODEL.catalogId);
    expect(payload.imageSize).toBe("2K");
    expect(payload.quality).toBe("medium");
  });

  it("后台一个图片模型都没配时禁用提交，点了也不发请求", () => {
    imageCatalogState = { models: [], isLoading: false, isFallback: false, error: null };
    render(
      <Scene360Overlay node={NODE} imageSource="https://x/src.png?t=1" onClose={() => {}} />,
    );
    // 这个面板没有模型选择器，提交下去后端 `_resolve_catalog_request` 只会回 409。
    const button = screen.getByTitle("modelParams.noModelsAvailable");
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(submitFreezoneScene360).not.toHaveBeenCalled();
  });

  it("目录还在路上时不算「没模型」，不许闪一下不可用", () => {
    imageCatalogState = {
      models: [SCENE_360_MODEL],
      isLoading: true,
      isFallback: true,
      error: null,
    };
    render(
      <Scene360Overlay node={NODE} imageSource="https://x/src.png?t=1" onClose={() => {}} />,
    );
    expect(screen.getByTitle("scene360.submit")).not.toBeDisabled();
  });
});

describe("宫格动作面板", () => {
  const REQUEST: GridActionRequest = {
    nodeId: NODE.id,
    key: "multiCameraGrid",
    label: "多机位九宫格",
    prompt: "生成九宫格多机位",
  };

  it("提交带上解析出的模型、目录身份、尺寸与画质", async () => {
    render(
      <GridActionConfirmOverlay
        node={NODE}
        imageSource="https://x/src.png?t=1"
        request={REQUEST}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByTitle("nodeToolbar.gridMenu.confirmBar.submit"));

    await waitFor(() => expect(submitFreezoneTemplateEdit).toHaveBeenCalled());
    const payload = submitFreezoneTemplateEdit.mock.calls[0][1] as Record<string, unknown>;
    expect(payload.model).toBe(CATALOG_MODEL.apiModel);
    expect(payload.catalogId).toBe(CATALOG_MODEL.catalogId);
    expect(payload.imageSize).toBe("1K");
    expect(payload.quality).toBe("low");
    expect(payload.mode).toBe("multi_camera_nine_grid");
  });

  it("后台一个图片模型都没配时禁用提交，点了也不发请求", () => {
    imageCatalogState = { models: [], isLoading: false, isFallback: false, error: null };
    render(
      <GridActionConfirmOverlay
        node={NODE}
        imageSource="https://x/src.png?t=1"
        request={REQUEST}
        onClose={() => {}}
      />,
    );
    const button = screen.getByTitle("modelParams.noModelsAvailable");
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(submitFreezoneTemplateEdit).not.toHaveBeenCalled();
  });

  it("拉取失败回落到兜底列表时照常可提交", async () => {
    imageCatalogState = {
      models: [CATALOG_MODEL],
      isLoading: false,
      isFallback: true,
      error: new Error("boom"),
    };
    render(
      <GridActionConfirmOverlay
        node={NODE}
        imageSource="https://x/src.png?t=1"
        request={REQUEST}
        onClose={() => {}}
      />,
    );
    fireEvent.click(screen.getByTitle("nodeToolbar.gridMenu.confirmBar.submit"));
    await waitFor(() => expect(submitFreezoneTemplateEdit).toHaveBeenCalled());
  });
});
