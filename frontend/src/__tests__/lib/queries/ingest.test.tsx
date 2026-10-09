// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { server } from "@/__mocks__/msw/server";
import ky from "ky";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const uploadPost = vi.hoisted(() => vi.fn());

// [aipdd fork delta] 上传客户端换成可注入 spy：本套件在 jsdom 下运行，jsdom 的
// File/FormData 与 Node(undici) 是两个 realm —— 真发请求时 multipart 会被序列化成
// filename="blob" 的空文件，handler 里 request.formData() 也会撞 undici 的 WebIDL 断言。
// 改为直接断言传给 uploadApi 的 FormData 入参（字段/文件名/大小/类型全保真），
// 响应侧用真实 Response 走同一条 jsonWithBackendError 错误契约。
vi.mock("@/lib/api", () => ({
  api: ky.create({ baseUrl: "http://localhost:3000/" }),
  uploadApi: { post: uploadPost },
}));

import { BillingRuleNotConfiguredError } from "@/lib/api-errors";
import {
  useChapters,
  useStartIngest,
  useUploadNovel,
} from "@/lib/queries/ingest";

afterEach(() => server.resetHandlers());

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("ingest query error contract", () => {
  it("requests and caches chapter previews by spine template", async () => {
    let requestedTemplate = "";
    server.use(
      http.get("http://localhost:3000/api/v1/projects/demo/chapters", ({ request }) => {
        requestedTemplate = new URL(request.url).searchParams.get("spine_template") ?? "";
        return HttpResponse.json({
          ok: true,
          data: { chapters: [], total_chars: 0, count: 0 },
        });
      }),
    );

    const { result } = renderHook(() => useChapters("demo", "drama"), {
      wrapper,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(requestedTemplate).toBe("drama");
  });

  it("rejects upload responses that return ok:false with a backend error", async () => {
    uploadPost.mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: false, error: "解析章节失败: 文件编码不支持" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );

    const { result } = renderHook(() => useUploadNovel("demo"), { wrapper });
    result.current.mutate({
      file: new File(["bad-file!"], "broken.txt", { type: "text/plain" }),
      spineTemplate: "drama",
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe("解析章节失败: 文件编码不支持");

    // 请求形状：URL 与 multipart 字段（在序列化前直接检查 FormData）。
    const [url, options] = uploadPost.mock.calls[0] as [string, { body: FormData }];
    expect(url).toBe("api/v1/projects/demo/ingest/upload");
    expect(options.body.get("spine_template")).toBe("drama");
    expect(options.body.get("file")).toMatchObject({
      name: "broken.txt",
      size: 9,
      type: "text/plain",
    });
  });

  it("rejects start responses that return ok:false with a backend error", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/ingest/start", () =>
        HttpResponse.json({ ok: false, error: "File 'missing.txt' not found in uploads/" }),
      ),
    );

    const { result } = renderHook(() => useStartIngest("demo"), { wrapper });
    result.current.mutate({ filename: "missing.txt", rebuild: true });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(Error);
    expect(result.current.error?.message).toBe("File 'missing.txt' not found in uploads/");
  });

  it("maps start 409 billing rule responses to the billing rule error", async () => {
    server.use(
      http.post("http://localhost:3000/api/v1/projects/demo/ingest/start", () =>
        HttpResponse.json(
          {
            ok: false,
            error: "计费规则未配置，请联系管理员设置积分规则",
            data: {
              error_code: "BILLING_RULE_NOT_CONFIGURED",
              billing_kind: "feature",
              billing_key: "mainline.ingest_fast",
            },
          },
          { status: 409 },
        ),
      ),
    );

    const { result } = renderHook(() => useStartIngest("demo"), { wrapper });
    result.current.mutate({ filename: "novel.txt", rebuild: true });

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toBeInstanceOf(BillingRuleNotConfiguredError);
    expect(result.current.error?.message).toBe("计费规则未配置，请联系管理员设置积分规则");
  });
});
