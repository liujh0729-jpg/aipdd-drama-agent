// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「配置备份 / 恢复」交互回归：敏感横幅 / 导出触发下载 /
// 导入二次确认（预览 → 确认 POST）/ 非法文件拒绝。后端调用全 mock。
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfigBackupSection } from "@/components/settings/config-backup-section";
import { toast } from "sonner";

const mocks = vi.hoisted(() => ({
  download: vi.fn(),
  mutateAsync: vi.fn(),
  isPending: false,
}));

vi.mock("@/lib/queries/config-backup", () => ({
  downloadConfigBackup: () => mocks.download(),
  useImportConfig: () => ({ mutateAsync: mocks.mutateAsync, isPending: mocks.isPending }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

function bundleFile(name = "cfg.json", payload?: unknown): File {
  const content =
    payload ??
    {
      format: "dramaclaw-config-backup",
      schema_version: 1,
      app_version: "2.1.6",
      generated_at: "2026-01-01T00:00:00Z",
      settings: { "newapi.api_key": "sk-1", "newapi.base_url": "http://gw" },
    };
  const file = new File([JSON.stringify(content)], name, { type: "application/json" });
  // jsdom 的 File 不保证带 Blob.text()，测试里显式补一个即可。
  (file as File & { text(): Promise<string> }).text = async () => JSON.stringify(content);
  return file;
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.download.mockResolvedValue(undefined);
  mocks.mutateAsync.mockResolvedValue({ ok: true, data: { applied: 2, skipped: 0, warnings: [] } });
  mocks.isPending = false;
});

describe("配置备份 / 恢复分区", () => {
  it("顶部展示敏感信息横幅与导出 / 导入按钮", () => {
    render(<ConfigBackupSection />);
    expect(screen.getByText(/备份包含 API Key/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导出配置" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "导入配置" })).toBeInTheDocument();
  });

  it("点导出触发下载并提示成功", async () => {
    const user = userEvent.setup();
    render(<ConfigBackupSection />);
    await user.click(screen.getByRole("button", { name: "导出配置" }));
    await waitFor(() => expect(mocks.download).toHaveBeenCalledTimes(1));
    expect(toast.success).toHaveBeenCalledWith("已开始下载配置文件");
  });

  it("选合法 bundle 先出预览，确认后导入并提示结果", async () => {
    const user = userEvent.setup();
    render(<ConfigBackupSection />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, bundleFile());

    expect(await screen.findByText(/将导入 2 项配置/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "确认导入" }));
    await waitFor(() => expect(mocks.mutateAsync).toHaveBeenCalledTimes(1));
    expect(mocks.mutateAsync.mock.calls[0][0]).toMatchObject({
      format: "dramaclaw-config-backup",
      settings: { "newapi.api_key": "sk-1" },
    });
    expect(toast.success).toHaveBeenCalledWith("已恢复 2 项配置");
    // 成功后预览面板收起。
    expect(screen.queryByText(/将导入 2 项配置/)).not.toBeInTheDocument();
  });

  it("取消确认不清空之外的动作，仅关闭预览", async () => {
    const user = userEvent.setup();
    render(<ConfigBackupSection />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, bundleFile());
    await screen.findByText(/将导入 2 项配置/);
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByText(/将导入 2 项配置/)).not.toBeInTheDocument();
    expect(mocks.mutateAsync).not.toHaveBeenCalled();
  });

  it("非法文件（非本系统备份）拒绝并进确认面板之前", async () => {
    const user = userEvent.setup();
    render(<ConfigBackupSection />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(fileInput, bundleFile("bad.json", { format: "not-mine", settings: {} }));
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith("不是有效的 DramaClaw 配置文件"),
    );
    expect(screen.queryByText(/将导入/)).not.toBeInTheDocument();
  });
});
