// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
//
// 资产库「剪映草稿」：VectCut 产物单独成类——不走上传、不参与选材/发画布，
// 只提供详情说明与草稿包（zip）下载。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";

const fetchFreezoneVideoCharacterLibrary = vi.fn();
const syncFreezoneAssetLibraryFromMainline = vi.fn();
const fetchFreezoneAssetLibraryFolders = vi.fn();
const uploadFreezoneImage = vi.fn();
const submitFreezoneAddVideoCharacterLibraryItem = vi.fn();
const downloadUrlAsFile = vi.fn();

vi.mock("@/api/ops", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/ops")>()),
  fetchFreezoneVideoCharacterLibrary: (...args: unknown[]) =>
    fetchFreezoneVideoCharacterLibrary(...args),
  syncFreezoneAssetLibraryFromMainline: (...args: unknown[]) =>
    syncFreezoneAssetLibraryFromMainline(...args),
  fetchFreezoneAssetLibraryFolders: (...args: unknown[]) =>
    fetchFreezoneAssetLibraryFolders(...args),
  uploadFreezoneImage: (...args: unknown[]) => uploadFreezoneImage(...args),
  submitFreezoneAddVideoCharacterLibraryItem: (...args: unknown[]) =>
    submitFreezoneAddVideoCharacterLibraryItem(...args),
}));

vi.mock("@/features/canvas/hooks/useFreezoneStyleTemplates", () => ({
  useFreezoneStyleTemplates: () => ({
    templates: [],
    assetBase: "",
    isLoading: false,
    error: null,
    retry: () => {},
  }),
  bustStyleTemplateCover: vi.fn(),
}));

vi.mock("@/lib/queries/style-gallery", () => ({
  uploadStyleGalleryCover: vi.fn(),
}));

vi.mock("@/lib/browserDownload", () => ({
  downloadUrlAsFile: (...args: unknown[]) => downloadUrlAsFile(...args),
}));

import { AssetLibraryModal } from "@/features/canvas/ui/AssetLibraryModal";
import { ConfirmDialogHost } from "@/components/confirm-dialog-host";

const DRAFT = {
  item_id: "vectcut-draft:abc123",
  name: "EP01 精剪",
  media: "draft",
  source: "upload",
  category: "draft",
  folder: "draft",
  draft_id: "abc123",
};

const LIBRARY = [
  {
    id: "up-1",
    name: "参考图A",
    media: "image",
    source: "upload",
    image_urls: ["/static/a.png"],
  },
  DRAFT,
];

function renderModal(
  props: Partial<React.ComponentProps<typeof AssetLibraryModal>> = {},
) {
  return render(
    <>
      <ConfirmDialogHost />
      <AssetLibraryModal mode="manage" open project="demo" onClose={() => {}} {...props} />
    </>,
  );
}

describe("AssetLibraryModal 剪映草稿", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchFreezoneVideoCharacterLibrary.mockResolvedValue({ items: LIBRARY });
    syncFreezoneAssetLibraryFromMainline.mockResolvedValue({ items: LIBRARY });
    fetchFreezoneAssetLibraryFolders.mockResolvedValue([]);
    uploadFreezoneImage.mockResolvedValue({ url: "/static/uploaded.png?x=1" });
    submitFreezoneAddVideoCharacterLibraryItem.mockResolvedValue({ ok: true });
    downloadUrlAsFile.mockResolvedValue(undefined);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:preview"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
  });

  it("草稿独立成类目和文件夹，不混进其它资产", async () => {
    renderModal();

    // 类目筛选条与文件夹导航里都有独立的「剪映草稿」入口。
    expect(
      await screen.findByRole("button", { name: "分类 剪映草稿" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "文件夹 剪映草稿" }));

    expect(await screen.findByText("EP01 精剪")).toBeInTheDocument();
    expect(screen.queryByText("参考图A")).toBeNull();
  });

  it("详情里只提供草稿包下载，下载打到代理端点且文件名带 zip 后缀", async () => {
    renderModal({ onSendItemToCanvas: vi.fn() });

    fireEvent.click(
      await screen.findByRole("button", { name: "查看 EP01 精剪 详情" }),
    );
    const details = screen.getByRole("dialog", { name: "资产详情" });

    // 草稿的媒介标签就是「剪映草稿」；它不是可引用的素材，详情里不出现「发送到画布」。
    expect(within(details).getByText("剪映草稿")).toBeInTheDocument();
    expect(
      within(details).queryByRole("button", { name: "发送到画布" }),
    ).toBeNull();
    expect(within(details).getByText(/VectCut 生成的剪映草稿包/)).toBeInTheDocument();

    fireEvent.click(within(details).getByRole("button", { name: "下载草稿包" }));
    await waitFor(() =>
      expect(downloadUrlAsFile).toHaveBeenCalledWith(
        "/api/v1/projects/demo/freezone/video/drafts/abc123/download",
        "EP01 精剪.zip",
      ),
    );
  });

  it("选材态不给草稿勾选，点卡片只看详情", async () => {
    renderModal({ mode: "pick", onConfirm: vi.fn() });

    await screen.findByText("EP01 精剪");
    // 勾选入口只有图片那一条，草稿没有。
    expect(screen.getAllByRole("button", { name: "选择" })).toHaveLength(1);

    fireEvent.click(screen.getByText("EP01 精剪"));
    const details = screen.getByRole("dialog", { name: "资产详情" });
    expect(within(details).getByRole("button", { name: "下载草稿包" })).toBeInTheDocument();
    // 看了详情不等于选中了素材。
    expect(screen.getByRole("button", { name: "确定" })).toBeDisabled();
  });

  it("保存位置有「剪映草稿」且选中进入文件夹模式，标签候选里没有它", async () => {
    renderModal();
    await screen.findByRole("button", { name: "文件夹 主线" });

    fireEvent.click(screen.getByRole("button", { name: "上传资产" }));

    // [aipdd fork delta] req15：草稿进入保存位置候选（选中即切整文件夹上传），
    // 但标签候选仍过滤掉它——标签固定「剪映草稿」由后端兜底，不给用户选。
    fireEvent.click(screen.getByRole("button", { name: "选择标签" }));
    expect(screen.queryByRole("button", { name: "剪映草稿" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "选择保存位置" }));
    expect(screen.getByRole("button", { name: "剪映草稿" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "剪映草稿" }));

    expect(
      screen.getByText("保存位置是「剪映草稿」：整个文件夹将作为一条草稿上传"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传草稿" })).toBeDisabled();
  });

  it("按「剪映草稿」筛选时拖入文件被拒绝，切回全部资产后可上传", async () => {
    renderModal();
    await screen.findByRole("button", { name: "分类 剪映草稿" });

    fireEvent.click(screen.getByRole("button", { name: "分类 剪映草稿" }));
    const dialog = screen.getByRole("dialog", { name: "资产库" });
    const file = new File(["image"], "cover.png", { type: "image/png" });

    fireEvent.drop(dialog, { dataTransfer: { files: [file] } });
    expect(uploadFreezoneImage).not.toHaveBeenCalled();

    // 回到「全部资产」（默认落到「待分类资产」）后同样的拖入就正常入库。
    fireEvent.click(screen.getByRole("button", { name: "全部资产" }));
    fireEvent.drop(dialog, { dataTransfer: { files: [file] } });
    await waitFor(() =>
      expect(uploadFreezoneImage).toHaveBeenCalledWith("demo", file, "cover.png"),
    );
  });
});