// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 目录 store 工厂的行为契约。七份拷贝式 store 收敛成一个
// 工厂后，这些语义就是全体目录 hook 的共同护栏：并发合并、失败后不自动重试
// （防失败→重渲染→再失败循环）、refresh 保留旧值并在 in-flight 时排队补跑、
// focus TTL 节流、目录事件限幅、undefined/同步抛错回落。
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { createCatalogStore } from "@/features/canvas/modelCatalog/createCatalogStore";

// readUrl 固定 null：本文件全部显式传 project；事件刷新因此只吃 recent 队列。
vi.mock("@/lib/url-params", () => ({
  readUrl: () => ({ project: null }),
}));

interface TestResult {
  value: number;
  isLoading: boolean;
  isFallback: boolean;
  error: Error | null;
  retry: () => void;
}

function makeStore(
  fetchImpl: (key: string) => Promise<number>,
  options?: { focusRefreshTtlMs?: number },
) {
  return createCatalogStore<number, TestResult>({
    scope: "project",
    fetch: fetchImpl,
    fallback: () => -1,
    toResult: ({ value, isLoading, isFallback, error }, { retry }) => ({
      value,
      isLoading,
      isFallback,
      error,
      retry,
    }),
    warnLabel: "[test] catalog fetch failed:",
    ...options,
  });
}

/** 等待 fetch / 状态结算链（Promise.resolve → fetch → then/catch/finally）落地。 */
async function settle(): Promise<void> {
  for (let i = 0; i < 30; i += 1) {
    await Promise.resolve();
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("createCatalogStore", () => {
  it("并发 ensure 只发一次请求（in-flight 合并）", async () => {
    let resolveFetch: (value: number) => void = () => {};
    const fetchImpl = vi.fn(
      () =>
        new Promise<number>((resolve) => {
          resolveFetch = resolve;
        }),
    );
    const store = makeStore(fetchImpl);
    store.ensure("p1");
    store.ensure("p1");
    store.read("p1");
    // fetch 起飞要走一个 microtask（Promise.resolve().then 防御包装）。
    await Promise.resolve();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    resolveFetch(1);
    await settle();
    expect(store.read("p1").value).toBe(1);
    expect(store.read("p1").isFallback).toBe(false);
  });

  it("失败后 ensure 不自动重拉（失败循环护栏），refresh 才重拉", async () => {
    const fetchImpl = vi
      .fn<(key: string) => Promise<number>>()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(2);
    const store = makeStore(fetchImpl);
    store.ensure("p2");
    await settle();
    expect(store.read("p2").error).not.toBeNull();
    expect(store.read("p2").value).toBe(-1);
    expect(store.read("p2").isFallback).toBe(true);

    store.ensure("p2"); // 幂等：失败过的目录不再自动重试
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    store.refresh("p2"); // 显式刷新才重拉
    await settle();
    expect(store.read("p2").value).toBe(2);
    expect(store.read("p2").error).toBeNull();
  });

  it("retry 引用按 key 稳定；只在失败态生效，成功态调用是空操作", async () => {
    const fetchImpl = vi
      .fn<(key: string) => Promise<number>>()
      .mockRejectedValueOnce(new Error("boom"))
      .mockResolvedValueOnce(3);
    const store = makeStore(fetchImpl);
    store.ensure("p3");
    await settle();

    const failed = store.read("p3");
    expect(store.read("p3")).toBe(failed); // 快照引用稳定（getSnapshot 约定）
    failed.retry();
    await settle();
    expect(store.read("p3").value).toBe(3);

    const ok = store.read("p3");
    expect(ok.retry).toBe(failed.retry); // retry 引用不随状态换新
    ok.retry(); // 成功态 no-op
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("refresh 起飞时旧值原地保留（isLoading 打开），落地后替换", async () => {
    let resolveSecond: (value: number) => void = () => {};
    const fetchImpl = vi
      .fn<(key: string) => Promise<number>>()
      .mockResolvedValueOnce(1)
      .mockImplementationOnce(
        () =>
          new Promise<number>((resolve) => {
            resolveSecond = resolve;
          }),
      );
    const store = makeStore(fetchImpl);
    store.ensure("p5");
    await settle();

    store.refresh("p5");
    const during = store.read("p5");
    expect(during.value).toBe(1);
    expect(during.isLoading).toBe(true);

    // 等第二次 fetch 起飞（拿到真正的 resolve 句柄）再兑现。
    await Promise.resolve();
    resolveSecond(5);
    await settle();
    expect(store.read("p5").value).toBe(5);
    expect(store.read("p5").isLoading).toBe(false);
  });

  it("in-flight 期间 refresh 排队补跑一次，不并发重拉", async () => {
    let resolveFirst: (value: number) => void = () => {};
    const fetchImpl = vi
      .fn<(key: string) => Promise<number>>()
      .mockImplementationOnce(
        () =>
          new Promise<number>((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockResolvedValueOnce(9);
    const store = makeStore(fetchImpl);
    store.ensure("p6");
    // 等第一次 fetch 真正起飞（in-flight）再 refresh，才会命中排队分支。
    await Promise.resolve();
    store.refresh("p6"); // in-flight → 排队
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    resolveFirst(6);
    await settle();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(store.read("p6").value).toBe(9);
  });

  it("focus 刷新受 TTL 节流；显式 refresh 不受 TTL 限制", async () => {
    const fetchImpl = vi.fn(async () => 1);
    const store = makeStore(fetchImpl, { focusRefreshTtlMs: 60_000 });
    renderHook(() => store.use("p7"));
    await settle();
    expect(fetchImpl).toHaveBeenCalledTimes(1);

    act(() => {
      window.dispatchEvent(new Event("focus"));
    });
    await settle();
    expect(fetchImpl).toHaveBeenCalledTimes(1); // TTL 内不重拉

    store.refresh("p7");
    await settle();
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("refreshRecentlyUsed 只带刷最近使用的 3 个 key", async () => {
    const fetchImpl = vi.fn(async (_key: string) => 1);
    const store = makeStore(fetchImpl);
    ["a", "b", "c", "d"].forEach((key) => store.ensure(key));
    await settle();
    expect(fetchImpl).toHaveBeenCalledTimes(4);

    store.refreshRecentlyUsed();
    await settle();
    // 最近使用序 d > c > b > a：只重拉 d/c/b 三个，a 保持 1 次。
    expect(fetchImpl).toHaveBeenCalledTimes(7);
    const calledKeys = fetchImpl.mock.calls.map(([key]) => key);
    expect(calledKeys.filter((key) => key === "a")).toHaveLength(1);
    expect(calledKeys.filter((key) => key === "b")).toHaveLength(2);
    expect(calledKeys.filter((key) => key === "c")).toHaveLength(2);
    expect(calledKeys.filter((key) => key === "d")).toHaveLength(2);
  });

  it("fetch 返回 undefined 按失败处理（防 mock/误配置灌进面板）", async () => {
    const fetchImpl = vi.fn(async () => undefined as unknown as number);
    const store = makeStore(fetchImpl);
    store.ensure("p8");
    await settle();
    const result = store.read("p8");
    expect(result.isFallback).toBe(true);
    expect(result.error).not.toBeNull();
    expect(result.value).toBe(-1);
  });

  it("fetch 同步抛错被转成失败回落", async () => {
    const fetchImpl = vi.fn(() => {
      throw new Error("sync boom");
    });
    const store = makeStore(fetchImpl);
    store.ensure("p9");
    await settle();
    expect(store.read("p9").error).not.toBeNull();
    expect(store.read("p9").value).toBe(-1);
  });

  it("use(null) 静默不拉取（picker 域外调用约定）", () => {
    const fetchImpl = vi.fn(async () => 1);
    const store = makeStore(fetchImpl);
    renderHook(() => store.use(null));
    expect(fetchImpl).toHaveBeenCalledTimes(0);
  });

  it("无项目时 read 返回稳定的兜底快照，且不触发拉取", () => {
    const fetchImpl = vi.fn(async () => 1);
    const store = makeStore(fetchImpl);
    const first = store.read();
    const second = store.read();
    expect(first).toBe(second);
    expect(first.isFallback).toBe(true);
    expect(first.value).toBe(-1);
    expect(fetchImpl).toHaveBeenCalledTimes(0);
  });

  it("patch 原地修补已加载值；未加载时是空操作", async () => {
    const fetchImpl = vi.fn(async () => 1);
    const store = makeStore(fetchImpl);
    store.patch("px", (value) => value + 1); // 未加载 → no-op
    store.ensure("px");
    await settle();
    store.patch("px", (value) => value + 10);
    expect(store.read("px").value).toBe(11);
    expect(store.read("px").error).toBeNull();
  });
});