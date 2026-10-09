// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useSyncExternalStore } from "react";

import { readUrl } from "@/lib/url-params";

/**
 * [aipdd fork delta] 目录 store 工厂 —— 画布各面板「按作用域缓存一份服务端
 * 目录」的统一内核。
 *
 * 此前 image / video / audio / text 四个模型目录 hooks（外加 camera /
 * style / video-camera 三个一次性目录）各自维护一份拷贝式的 module-level
 * store：Map + useSyncExternalStore + inFlight/pendingRefresh，七份实现共享
 * 同一组语义却处处微妙漂移（失败判据不一、起飞态保留谁不一、事件刷新范围
 * 不一）。工厂把这组语义收成一份，各 hook 退化为「取数 + 结果映射」的薄配置。
 *
 * 统一后的关键语义：
 * - 一次拉取全体共享：按 key（项目 / 全局单槽）缓存，`entries.has(key)` 是
 *   「已尝试加载」的唯一判据。失败后不自动重试 —— render 期判据一旦把失败
 *   态算作未加载，就变成「失败 → 重渲染 → 再失败」的雪崩；需要重试的目录
 *   （风格清单）显式暴露 `retry`。
 * - 起飞静默、落地广播：hook 在 render 期调用，此刻同步写状态但不 emit
 *   （否则 React 报 update during render）；网络落地后才广播。用户事件
 *   （retry）不在 render 里，起飞时可以直接广播。
 * - 显式刷新保留旧值：重拉期间旧列表原地留守，响应到了再替换，不闪回空。
 * - 事件限幅：media-model-catalog-updated 只刷新「当前项目 + 最近使用过的
 *   至多 3 个 key」，不再是「遍历全部已知项目 × 全部目录」的放大器。
 * - focus TTL：切回标签页的 focus 刷新有最小间隔（默认 5 分钟）；显式
 *   refresh / retry / 目录事件不受 TTL 限制。
 */

export interface CatalogCoreState<TValue> {
  /** 已加载值：真实目录，或尚未加载成功时的兜底值。 */
  value: TValue;
  isLoading: boolean;
  /** true = value 是兜底值（初始 / 加载失败）；false = 服务端权威目录。 */
  isFallback: boolean;
  error: Error | null;
}

export interface CatalogStoreConfig<TValue, TResult> {
  /** "project" = 按项目分桶（无 override 时取 URL 项目）；"global" = 全站单槽。 */
  scope: "project" | "global";
  /** 真实取数。同步抛错、返回 undefined 都按失败处理（回落兜底）。 */
  fetch: (key: string) => Promise<TValue>;
  /**
   * 兜底值。必须是函数（惰性求值）—— 避免与定义兜底常量的模块发生循环导入 TDZ，
   * 并允许兜底随调用时状态求值。
   * [aipdd fork delta] 各媒体目录的兜底统一为空（[]）：后台没配模型时不凭空造模型，
   * 默认值一律来自后台目录首项（默认模型设置）。
   */
  fallback: () => TValue;
  /** 内核状态 → 面板结果。`retry` 引用由内核保证按 key 稳定（可进依赖数组）。 */
  toResult: (
    core: CatalogCoreState<TValue>,
    extras: { retry: () => void },
  ) => TResult;
  /** 失败时 console.warn 的前缀文案（含结尾冒号）。 */
  warnLabel: string;
  /** 配了才在 hook 挂载期注册 focus 刷新；TTL 内的重复 focus 直接跳过。 */
  focusRefreshTtlMs?: number;
}

export interface CatalogStore<TValue, TResult> {
  /**
   * 读 hook。projectOverride === undefined 时取 URL 项目；null / 空串静默
   * （不触发拉取也不注册 focus），仅订阅既有状态（picker 域外调用约定）。
   */
  use(projectOverride?: string | null, options?: { enabled?: boolean }): TResult;
  /** React 之外的同步读口：不触发拉取，未加载时返回兜底快照。 */
  read(projectOverride?: string | null): TResult;
  /** 幂等预取：已尝试加载过（含失败）就不动。 */
  ensure(project?: string | null): void;
  /** 强制重拉：旧列表留守，响应到了原地替换；in-flight 时排队补跑一次。 */
  refresh(project?: string | null): void;
  /** 原地修补缓存值（cache-bust 类局部更新）；未加载时是空操作。 */
  patch(project: string, updater: (value: TValue) => TValue): void;
  /** media-model-catalog-updated 的刷新入口：当前项目 + 最近使用过的至多 3 个 key。 */
  refreshRecentlyUsed(): void;
}

const GLOBAL_KEY = "\u0000catalog-global";
const DEFAULT_FOCUS_REFRESH_TTL_MS = 5 * 60 * 1000;
/** 目录事件最多带刷最近使用过的几个 key（另有当前项目兜底）。 */
const EVENT_REFRESH_LIMIT = 3;
const NOOP_RETRY = () => {};

export function createCatalogStore<TValue, TResult>(
  config: CatalogStoreConfig<TValue, TResult>,
): CatalogStore<TValue, TResult> {
  interface Entry {
    core: CatalogCoreState<TValue>;
    /** toResult 缓存：useSyncExternalStore 的 getSnapshot 必须引用稳定。 */
    result: TResult;
  }

  const entries = new Map<string, Entry>();
  const listeners = new Map<string, Set<() => void>>();
  const inFlight = new Set<string>();
  const pendingRefresh = new Set<string>();
  const lastFetchStartedAt = new Map<string, number>();
  const retryFns = new Map<string, () => void>();
  /** 最近使用过的 key（touch 序号递增，事件刷新按序号取尾部）。 */
  const recent = new Map<string, number>();
  let touchSeq = 0;
  let emptyResultMemo: TResult | null = null;

  const focusTtlMs = config.focusRefreshTtlMs ?? DEFAULT_FOCUS_REFRESH_TTL_MS;
  const focusRefreshEnabled = config.focusRefreshTtlMs !== undefined;

  function resolveKey(projectOverride?: string | null): string | null {
    if (config.scope === "global") return GLOBAL_KEY;
    const project =
      projectOverride !== undefined ? projectOverride : readUrl().project;
    return project || null;
  }

  function touch(key: string) {
    recent.delete(key);
    touchSeq += 1;
    recent.set(key, touchSeq);
  }

  function emit(key: string) {
    listeners.get(key)?.forEach((fn) => fn());
  }

  /** 兜底快照：无项目 / 尚未尝试加载时读到的那一份（引用必须稳定）。 */
  function getEmptyResult(): TResult {
    if (!emptyResultMemo) {
      emptyResultMemo = config.toResult(
        {
          value: config.fallback(),
          isLoading: false,
          isFallback: true,
          error: null,
        },
        { retry: NOOP_RETRY },
      );
    }
    return emptyResultMemo;
  }

  function retryFor(key: string): () => void {
    let fn = retryFns.get(key);
    if (!fn) {
      fn = () => {
        const entry = entries.get(key);
        // 只在失败态生效：加载中重试没有意义，成功态重试是白拉一次。
        if (!entry || entry.core.isLoading || !entry.core.error) return;
        load(key, true);
        emit(key); // 用户点击触发，不在 render 期，起飞态可以直接广播。
      };
      retryFns.set(key, fn);
    }
    return fn;
  }

  function setEntry(
    key: string,
    core: CatalogCoreState<TValue>,
    options: { emit?: boolean } = {},
  ) {
    entries.set(key, {
      core,
      result: config.toResult(core, { retry: retryFor(key) }),
    });
    if (options.emit) emit(key);
  }

  function load(key: string, force: boolean) {
    touch(key);
    if (inFlight.has(key)) {
      if (force) pendingRefresh.add(key);
      return;
    }
    // entries.has 是「已尝试加载」的唯一判据：失败过的目录不自动重试。
    if (!force && entries.has(key)) return;
    inFlight.add(key);
    lastFetchStartedAt.set(key, Date.now());
    const current = entries.get(key);
    // 起飞态只写不播（render 期调用安全）；保留当前值，重拉期间旧列表留守。
    setEntry(key, {
      value: current?.core.value ?? config.fallback(),
      isLoading: true,
      isFallback: current?.core.isFallback ?? true,
      error: null,
    });
    Promise.resolve()
      .then(() => config.fetch(key))
      .then((value) => {
        // mock / 误配置下 fetch 可能同步炸或给出 undefined；同步炸会被上面的
        // then 转成 rejection，undefined 在这里显式转失败，两者都回落兜底，
        // 而不是把 undefined 灌进面板。
        if (value === undefined) {
          throw new Error("catalog fetch returned undefined");
        }
        setEntry(
          key,
          { value, isLoading: false, isFallback: false, error: null },
          { emit: true },
        );
      })
      .catch((error: unknown) => {
        const normalized =
          error instanceof Error ? error : new Error(String(error));
        console.warn(config.warnLabel, normalized.message);
        setEntry(
          key,
          {
            value: config.fallback(),
            isLoading: false,
            isFallback: true,
            error: normalized,
          },
          { emit: true },
        );
      })
      .finally(() => {
        inFlight.delete(key);
        if (pendingRefresh.delete(key)) load(key, true);
      });
  }

  function subscribe(key: string | null, callback: () => void) {
    if (!key) return () => {};
    let bucket = listeners.get(key);
    if (!bucket) {
      bucket = new Set();
      listeners.set(key, bucket);
    }
    bucket.add(callback);
    return () => {
      bucket!.delete(callback);
      if (bucket!.size === 0) listeners.delete(key);
    };
  }

  function use(
    projectOverride?: string | null,
    options?: { enabled?: boolean },
  ): TResult {
    const enabled = options?.enabled ?? true;
    const key = resolveKey(projectOverride);

    // render 期幂等预取：起飞态静默写，保证后续 render / read 能同步看到。
    if (key && enabled) load(key, false);

    useEffect(() => {
      if (!key || !enabled || !focusRefreshEnabled) return;
      const refresh = () => {
        const last = lastFetchStartedAt.get(key) ?? 0;
        if (Date.now() - last < focusTtlMs) return;
        load(key, true);
      };
      window.addEventListener("focus", refresh);
      return () => window.removeEventListener("focus", refresh);
    }, [key, enabled]);

    return useSyncExternalStore(
      (callback) => subscribe(key, callback),
      () => (key ? entries.get(key)?.result ?? getEmptyResult() : getEmptyResult()),
      () => getEmptyResult(),
    );
  }

  function read(projectOverride?: string | null): TResult {
    const key = resolveKey(projectOverride);
    return key ? entries.get(key)?.result ?? getEmptyResult() : getEmptyResult();
  }

  function ensure(project?: string | null): void {
    const key = resolveKey(project);
    if (key) load(key, false);
  }

  function refresh(project?: string | null): void {
    const key = resolveKey(project);
    if (key) load(key, true);
  }

  function patch(project: string, updater: (value: TValue) => TValue): void {
    const key = resolveKey(project);
    if (!key) return;
    const entry = entries.get(key);
    if (!entry) return;
    setEntry(
      key,
      { ...entry.core, value: updater(entry.core.value) },
      { emit: true },
    );
  }

  function refreshRecentlyUsed(): void {
    const targets = new Set<string>();
    [...recent.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, EVENT_REFRESH_LIMIT)
      .forEach(([key]) => targets.add(key));
    if (config.scope === "project") {
      const current = readUrl().project;
      if (current) targets.add(current);
    }
    targets.forEach((key) => load(key, true));
  }

  return { use, read, ensure, refresh, patch, refreshRecentlyUsed };
}