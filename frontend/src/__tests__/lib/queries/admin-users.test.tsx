// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] CE 多用户管理查询层契约：端点、请求体、失效哪些缓存。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { http, HttpResponse } from "msw";
import ky from "ky";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
  uploadApi: ky.create({ baseUrl: "http://localhost:3000/" }),
}));

import {
  useAdminUsers,
  useCreateAdminUser,
  useResetAdminUserPassword,
  useSetupStatus,
  useUpdateAdminUser,
} from "@/lib/queries/admin-users";
import { server } from "@/__mocks__/msw/server";

const BASE = "http://localhost:3000/api/v1";

const RECORD = {
  id: "u-2",
  username: "xiaowang",
  role: "member",
  status: "active",
  created_at: "2026-09-02T00:00:00+00:00",
  updated_at: "2026-09-02T00:00:00+00:00",
  is_self: false,
};

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

/** 记录被 invalidate 的 queryKey，比翻缓存内部状态直白。 */
function spyInvalidate(qc: QueryClient): unknown[] {
  const seen: unknown[] = [];
  const original = qc.invalidateQueries.bind(qc);
  qc.invalidateQueries = ((options?: { queryKey?: unknown }) => {
    if (options?.queryKey) seen.push(options.queryKey);
    return original(options as never);
  }) as typeof qc.invalidateQueries;
  return seen;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("admin-users queries", () => {
  it("useSetupStatus 探测 /auth/setup-status；enabled=false 时不打后端", async () => {
    let hits = 0;
    server.use(
      http.get(`${BASE}/auth/setup-status`, () => {
        hits += 1;
        return HttpResponse.json({
          ok: true,
          data: { needs_setup: true, multi_user: true },
        });
      }),
    );

    const off = makeWrapper();
    renderHook(() => useSetupStatus(false), { wrapper: off.wrapper });
    await waitFor(() => expect(off.qc.isFetching()).toBe(0));
    expect(hits).toBe(0);

    const on = makeWrapper();
    const { result } = renderHook(() => useSetupStatus(), { wrapper: on.wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(hits).toBe(1);
    expect(result.current.data?.data).toEqual({ needs_setup: true, multi_user: true });
  });

  it("useAdminUsers 拉取账户列表", async () => {
    server.use(
      http.get(`${BASE}/admin/users`, () => HttpResponse.json({ ok: true, data: [RECORD] })),
    );
    const { result } = renderHook(() => useAdminUsers(), { wrapper: makeWrapper().wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.data).toEqual([RECORD]);
  });

  it("新建账户走 POST /admin/users，成功后刷账户列表", async () => {
    let payload: unknown = null;
    server.use(
      http.post(`${BASE}/admin/users`, async ({ request }) => {
        payload = await request.json();
        return HttpResponse.json({ ok: true, data: RECORD });
      }),
    );
    const { qc, wrapper } = makeWrapper();
    const seen = spyInvalidate(qc);
    const { result } = renderHook(() => useCreateAdminUser(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({
        username: "xiaowang",
        password: "Aipdd@2026",
        role: "member",
      });
    });
    expect(payload).toEqual({ username: "xiaowang", password: "Aipdd@2026", role: "member" });
    expect(seen).toContainEqual(["admin", "users"]);
  });

  it("改角色走 PATCH：id 只做路径参数，账户/当前用户缓存一起失效", async () => {
    const calls: { path: string; body: unknown }[] = [];
    server.use(
      http.patch(`${BASE}/admin/users/:userId`, async ({ request }) => {
        calls.push({ path: new URL(request.url).pathname, body: await request.json() });
        return HttpResponse.json({ ok: true, data: { ...RECORD, role: "owner" } });
      }),
    );
    const { qc, wrapper } = makeWrapper();
    const seen = spyInvalidate(qc);
    const { result } = renderHook(() => useUpdateAdminUser(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ userId: "u/2", role: "owner" });
    });
    // id 里的斜杠被转义，不会被拼成多一层路径
    expect(calls).toEqual([{ path: "/api/v1/admin/users/u%2F2", body: { role: "owner" } }]);
    // 角色变化会反映到 /auth/me，两边一起失效才不会让 UI 停在旧角色上
    expect(seen).toContainEqual(["admin", "users"]);
    expect(seen).toContainEqual(["auth", "me"]);
  });

  it("禁用账户只发 status，不夹带 role", async () => {
    const calls: unknown[] = [];
    server.use(
      http.patch(`${BASE}/admin/users/:userId`, async ({ request }) => {
        calls.push(await request.json());
        return HttpResponse.json({ ok: true, data: { ...RECORD, status: "suspended" } });
      }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useUpdateAdminUser(), { wrapper });

    await act(async () => {
      await result.current.mutateAsync({ userId: "u-2", status: "suspended" });
    });
    expect(calls).toEqual([{ status: "suspended" }]);
  });

  it("重置口令打在 /password 子资源上", async () => {
    let body: unknown = null;
    server.use(
      http.post(`${BASE}/admin/users/:userId/password`, async ({ request, params }) => {
        expect(params.userId).toBe("u-2");
        body = await request.json();
        return HttpResponse.json({
          ok: true,
          data: { revoked_sessions: 3, is_self: false },
        });
      }),
    );
    const { wrapper } = makeWrapper();
    const { result } = renderHook(() => useResetAdminUserPassword(), { wrapper });

    await act(async () => {
      const res = await result.current.mutateAsync({ userId: "u-2", password: "Aipdd@2027" });
      if (res.ok === false) throw new Error(res.error ?? "reset failed");
      expect(res.data).toEqual({ revoked_sessions: 3, is_self: false });
    });
    expect(body).toEqual({ password: "Aipdd@2027" });
  });

  it("后端回 403 时把 detail 变成可读错误，而不是让 UI 吞掉", async () => {
    server.use(
      http.get(`${BASE}/admin/users`, () =>
        HttpResponse.json({ detail: "administrator role required" }, { status: 403 }),
      ),
    );
    const { result } = renderHook(() => useAdminUsers(), { wrapper: makeWrapper().wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error?.message).toContain("administrator role required");
  });
});
