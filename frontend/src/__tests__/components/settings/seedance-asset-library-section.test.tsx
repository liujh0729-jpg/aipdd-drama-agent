// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta req47] 设置 → Seedance 素材库的能力门控契约。
//
// 这页唯一的存在理由就是用户口径：「素材库仅支持 LocalAPI 支持素材库的情况，并不是所有
// LocalAPI 都支持」。所以钉死三件退化起来最便宜的事：
// 1. 探测结论不是 supported（含未出结论的进行中态）时，整块操作区不渲染，读查询的
//    enabled 也必须是 false —— 不能让上游根本没挂的路由变成一排「未知错误」；
// 2. 能力不成立的原因与上游原文要照贴（「没有这组路由」和「Key 被拒」是两件事）；
// 3. 支持时才放开：读查询启用、登记与真人认证入口出现。
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SeedanceAssetLibrarySection } from "@/components/settings/seedance-asset-library-section";

const statusState = vi.hoisted<{ current: unknown }>(() => ({ current: null }));
const enabledCalls = vi.hoisted<{ groups: boolean[]; assets: boolean[] }>(() => ({
  groups: [],
  assets: [],
}));
const refreshMutate = vi.hoisted(() => vi.fn());
const groupItems = vi.hoisted<{ current: unknown[] }>(() => ({ current: [] }));
const assetItems = vi.hoisted<{ current: unknown[] }>(() => ({ current: [] }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));

vi.mock("@/lib/queries/seedance-assets", () => ({
  useSeedanceAssetLibraryStatus: () => ({
    data: statusState.current ? { data: statusState.current } : undefined,
    error: null,
    isLoading: statusState.current === null,
  }),
  useSeedanceAssetGroups: (_params: unknown, enabled: boolean) => {
    enabledCalls.groups.push(enabled);
    return { data: { data: { Items: groupItems.current } }, isLoading: false };
  },
  useSeedanceAssets: (_params: unknown, enabled: boolean) => {
    enabledCalls.assets.push(enabled);
    return { data: { data: { Items: assetItems.current } }, isLoading: false };
  },
  useCreateSeedanceAsset: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateSeedanceValidateSession: () => ({ mutate: vi.fn(), isPending: false }),
  useSeedanceValidateResult: () => ({ mutate: vi.fn(), isPending: false }),
  useRefreshSeedanceAssetProbe: () => ({ mutate: refreshMutate, isPending: false }),
}));

vi.mock("sonner", () => ({ toast }));

function setStatus(patch: Record<string, unknown> | null) {
  statusState.current = patch
    ? {
        supported: false,
        state: "unsupported",
        reason: "",
        baseUrl: "https://gw.example",
        credentialSource: "gateway[custom]",
        keyPreview: "sk-1***fe",
        httpStatus: 0,
        upstreamCode: "",
        upstreamMessage: "",
        warning: "",
        checkedAt: "2026-10-08T00:00:00+00:00",
        cached: false,
        ...patch,
      }
    : null;
}

beforeEach(() => {
  vi.clearAllMocks();
  enabledCalls.groups = [];
  enabledCalls.assets = [];
  groupItems.current = [];
  assetItems.current = [];
  setStatus(null);
});

describe("SeedanceAssetLibrarySection 能力门控", () => {
  it("探测还没出结论时按不支持处理：不发读请求、不给写入口", () => {
    render(<SeedanceAssetLibrarySection />);

    expect(enabledCalls.groups).toEqual([false]);
    expect(enabledCalls.assets).toEqual([false]);
    expect(screen.queryByRole("button", { name: "登记" })).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "创建认证会话" }),
    ).not.toBeInTheDocument();
    // 没结论也要有话可说，而不是让横幅空着让人以为页面坏了。
    expect(screen.getByText("还没有可用的 LocalAPI 网关")).toBeInTheDocument();
  });

  it("这台 LocalAPI 没有素材库路由时讲清成因，并整块收起操作区", () => {
    setStatus({
      state: "unsupported",
      reason: "这台 LocalAPI 没有暴露 v2 素材库接口，素材库与真人认证不可用",
    });
    render(<SeedanceAssetLibrarySection />);

    expect(screen.getByText("这台 LocalAPI 没有素材库")).toBeInTheDocument();
    expect(screen.getByText(/没有暴露 v2 素材库接口/)).toBeInTheDocument();
    // 口径本身要写在页面上：能力随网关而定，不是连了 LocalAPI 就有。
    expect(screen.getByText(/素材库只由支持该能力的 LocalAPI 网关提供/)).toBeInTheDocument();
    expect(enabledCalls.groups).toEqual([false]);
    expect(enabledCalls.assets).toEqual([false]);
    expect(screen.queryByText("素材组")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "登记" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /虚拟素材/ })).not.toBeInTheDocument();
  });

  it("Key 被拒不等于不支持：把上游原文贴出来，同样不放开写操作", () => {
    setStatus({
      state: "unauthorized",
      reason: "这台 LocalAPI 有素材库路由，但当前 Key 被它拒绝，能力尚未证实",
      httpStatus: 401,
      upstreamCode: "InvalidToken",
      upstreamMessage: "无效的令牌",
    });
    render(<SeedanceAssetLibrarySection />);

    expect(
      screen.getByText("素材库路由存在，但当前 Key 被它拒绝"),
    ).toBeInTheDocument();
    expect(screen.getByText(/上游返回：\[InvalidToken\] 无效的令牌/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建认证会话" })).not.toBeInTheDocument();
    expect(enabledCalls.groups).toEqual([false]);
  });

  it("确认支持后才启用读查询并放开登记与真人认证", () => {
    setStatus({ state: "supported", supported: true, reason: "素材库可用：读到了资产组列表。" });
    render(<SeedanceAssetLibrarySection />);

    expect(enabledCalls.groups).toEqual([true]);
    expect(enabledCalls.assets).toEqual([true]);
    expect(screen.getByRole("button", { name: "登记" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建认证会话" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "真人素材" })).toBeInTheDocument();
  });

  it("重新探测用后端结论措辞，不在前端预设「支持」", () => {
    setStatus({ state: "unsupported" });
    refreshMutate.mockImplementation((_v, opts) =>
      opts?.onSuccess?.({ supported: false, state: "unsupported" }),
    );
    render(<SeedanceAssetLibrarySection />);
    fireEvent.click(screen.getByRole("button", { name: "重新探测" }));

    expect(refreshMutate).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalledWith(
      "已重新探测：这台 LocalAPI 用不了素材库",
    );
  });

  it("上游的额外提醒（信封错误）在支持态下也要露出来", () => {
    setStatus({
      state: "supported",
      supported: true,
      reason: "素材库接口可达（上游返回了资产信封）",
      warning: "QuotaExceeded: 账户额度不足",
    });
    render(<SeedanceAssetLibrarySection />);

    expect(screen.getByText(/注意：QuotaExceeded: 账户额度不足/)).toBeInTheDocument();
  });

  it("只有已过审的素材给复制引用入口，复制出来的是 asset:// 引用而不是临时直链", async () => {
    // 列表里的 URL 是临时直链（约 12 小时过期），生成要的是库里那份的引用。
    setStatus({ state: "supported", supported: true });
    // 素材列表只在选中了素材组之后才渲染，所以先给一个组让 activeGroupId 落到真实值。
    groupItems.current = [{ Id: "group-20260318071000-abcde", Name: "真人组" }];
    assetItems.current = [
      { Id: "asset-20260318071009-abcde", Name: "真人正面", Status: "Active", AssetType: "Image", URL: "https://tmp.example/a.png" },
      { Id: "asset-20260318071010-fghij", Name: "还在审", Status: "Processing", AssetType: "Image", URL: "" },
    ];
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    render(<SeedanceAssetLibrarySection />);

    expect(screen.getAllByRole("button", { name: "复制引用" })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "复制引用" }));

    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText).toHaveBeenCalledWith("asset://asset-20260318071009-abcde");
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringContaining("asset://asset-20260318071009-abcde"),
    );
  });
});
