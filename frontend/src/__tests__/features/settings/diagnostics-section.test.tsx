// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「系统整体自检」分区渲染回归：分组 / 状态徽章 / 修复文案 /
// 待处理横幅 / 未设默认模型「前往模型与渠道」跳转。useDiagnostics 全 mock，绝不发真实请求。
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DiagnosticsSection } from "@/components/settings/diagnostics-section";

const mocks = vi.hoisted(() => ({
  refetch: vi.fn(),
  report: null as unknown,
  loading: false,
  error: null as unknown,
  fetching: false,
}));

vi.mock("@/lib/queries/diagnostics", () => ({
  useDiagnostics: () => ({
    data: mocks.report ? { ok: true, data: mocks.report } : undefined,
    isLoading: mocks.loading,
    error: mocks.error,
    isFetching: mocks.fetching,
    refetch: mocks.refetch,
  }),
}));

const ATTENTION_REPORT = {
  sections: [
    {
      id: "environment",
      items: [
        { id: "ffmpeg", level: "pass", detail: "/usr/bin/ffmpeg" },
        { id: "node", level: "fail", detail: null },
      ],
    },
    {
      id: "model_gateway",
      items: [{ id: "gateway_configured", level: "pass", detail: "mode=custom" }],
    },
    {
      id: "default_models",
      items: [{ id: "image", level: "fail", detail: null }],
    },
    {
      id: "storage",
      items: [{ id: "disk_space", level: "warn", detail: "1024 MB" }],
    },
  ],
  summary: { total: 5, pass: 2, warn: 1, fail: 2, info: 0, all_ok: false },
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.report = ATTENTION_REPORT;
  mocks.loading = false;
  mocks.error = null;
  mocks.fetching = false;
});

describe("系统整体自检分区", () => {
  it("渲染四组标题、复用依赖/栏目词条与状态徽章", () => {
    render(<DiagnosticsSection />);
    expect(screen.getByText("系统整体自检")).toBeInTheDocument();
    expect(screen.getByText("运行环境")).toBeInTheDocument();
    expect(screen.getByText("模型渠道")).toBeInTheDocument();
    expect(screen.getByText("默认模型")).toBeInTheDocument();
    expect(screen.getByText("存储")).toBeInTheDocument();
    // 环境项复用「环境安装」依赖词条，默认模型项复用「模型与渠道」栏目词条。
    expect(screen.getByText("FFmpeg")).toBeInTheDocument();
    expect(screen.getByText("Node.js 运行环境")).toBeInTheDocument();
    expect(screen.getByText("图片")).toBeInTheDocument();
    // 汇总：2 就绪 + 1 未就绪（node）等徽章。
    expect(screen.getAllByText("就绪").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("未就绪").length).toBeGreaterThanOrEqual(1);
  });

  it("有未就绪项时展示琥珀「N 项待处理」横幅", () => {
    render(<DiagnosticsSection />);
    expect(screen.getByText("有 3 项待处理")).toBeInTheDocument();
  });

  it("全部就绪时展示绿色横幅", () => {
    mocks.report = {
      sections: [{ id: "storage", items: [{ id: "disk_space", level: "pass", detail: "99 GB" }] }],
      summary: { total: 1, pass: 1, warn: 0, fail: 0, info: 0, all_ok: true },
    };
    render(<DiagnosticsSection />);
    expect(screen.getByText("全部就绪")).toBeInTheDocument();
    expect(screen.queryByText(/待处理/)).not.toBeInTheDocument();
  });

  it("未设默认模型项给出修复文案并可跳转「模型与渠道」", async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(<DiagnosticsSection onNavigate={onNavigate} />);
    expect(screen.getByText(/为该分类选择默认模型/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "前往模型与渠道" }));
    expect(onNavigate).toHaveBeenCalledWith("models");
  });

  it("加载中展示 loading 文案", () => {
    mocks.loading = true;
    render(<DiagnosticsSection />);
    expect(screen.getByText(/检测中|加载中|正在/)).toBeInTheDocument();
  });
});
