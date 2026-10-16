// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「环境安装 · 换脸直播」子块：状态卡片 / 启用开关 / 按需部署 /
// 边车启停 / 下载源编辑的渲染与交互回归。仿 env-setup-section.test.tsx 的钩子存根模式，
// 全部 react-query 钩子以可变状态替换——不发真实请求、不起真实边车。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { FaceSwapSection } from "@/components/settings/faceswap-section";
import type { FaceSwapStatus } from "@/lib/queries/faceswap";

const { statusState, enabledMut, sourcesMut, installMut, uninstallMut, startEngineMut, stopEngineMut } =
  vi.hoisted(() => ({
    statusState: { value: {} as Record<string, unknown> },
    enabledMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
    sourcesMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
    installMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
    uninstallMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
    startEngineMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
    stopEngineMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
  }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/lib/queries/faceswap", () => ({
  useFaceSwapStatus: () => statusState.value,
  useSetFaceSwapEnabled: () => enabledMut.value,
  useUpdateFaceSwapSources: () => sourcesMut.value,
  useStartFaceSwapInstall: () => installMut.value,
  useUninstallFaceSwap: () => uninstallMut.value,
  useStartFaceSwapEngine: () => startEngineMut.value,
  useStopFaceSwapEngine: () => stopEngineMut.value,
}));

function makeStatus(overrides: Partial<FaceSwapStatus> = {}): FaceSwapStatus {
  return {
    enabled: false,
    install_state: "not_installed",
    installing: false,
    installed: false,
    deps_ready: false,
    models_ready: false,
    progress: 0,
    message: "",
    installed_at: "",
    sources: { pip_index_url: "", hf_endpoint: "https://hf-mirror.com", https_proxy: "" },
    venv_path: "/state/faceswap/venv",
    models_dir: "/state/faceswap/models",
    control_port: 18899,
    engine_running: false,
    local_runtime: true,
    vcam: { system: "darwin", available: true, hint: "" },
    hardware: { os: "darwin", arch: "arm64", has_nvidia: false, provider: "coreml", reason: "Apple Silicon" },
    can_install: true,
    engine_script_present: false,
    log_tail: "",
    ...overrides,
  };
}

function setStatus(data: FaceSwapStatus | undefined, extra: Record<string, unknown> = {}) {
  statusState.value = {
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    data: data ? { data } : undefined,
    ...extra,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  enabledMut.value = { mutate: vi.fn(), isPending: false };
  sourcesMut.value = { mutate: vi.fn(), isPending: false };
  installMut.value = { mutate: vi.fn(), isPending: false };
  uninstallMut.value = { mutate: vi.fn(), isPending: false };
  startEngineMut.value = { mutate: vi.fn(), isPending: false };
  stopEngineMut.value = { mutate: vi.fn(), isPending: false };
});

describe("FaceSwapSection", () => {
  it("shows a loading row before the first status lands", () => {
    setStatus(undefined, { isLoading: true });
    render(<FaceSwapSection />);
    expect(screen.getByText("settings.faceswap.loading")).toBeInTheDocument();
  });

  it("shows a retry control when the status query fails", () => {
    const refetch = vi.fn();
    setStatus(undefined, { error: new Error("boom"), refetch });
    render(<FaceSwapSection />);
    expect(screen.getByText("common.retry")).toBeInTheDocument();
    fireEvent.click(screen.getByText("common.retry"));
    expect(refetch).toHaveBeenCalled();
  });

  it("renders the not-installed state with an install action and a warning icon", () => {
    setStatus(makeStatus());
    render(<FaceSwapSection />);
    expect(screen.getByText("settings.faceswap.state.notInstalled")).toBeInTheDocument();
    expect(screen.getByLabelText("settings.faceswap.warningIconLabel")).toBeInTheDocument();
    expect(screen.getByText("settings.faceswap.install")).toBeInTheDocument();
    // 未装：没有启停边车 / 卸载按钮。
    expect(screen.queryByText("settings.faceswap.startEngine")).not.toBeInTheDocument();
    expect(screen.queryByText("settings.faceswap.uninstall")).not.toBeInTheDocument();
  });

  it("triggers the install mutation when the install action is clicked", () => {
    setStatus(makeStatus());
    render(<FaceSwapSection />);
    fireEvent.click(screen.getByText("settings.faceswap.install"));
    expect(installMut.value.mutate).toHaveBeenCalledTimes(1);
  });

  it("toggles the enabled flag and reports the next value to the mutation", () => {
    setStatus(makeStatus({ enabled: false }));
    render(<FaceSwapSection />);
    fireEvent.click(screen.getByText("settings.faceswap.enable"));
    expect(enabledMut.value.mutate).toHaveBeenCalledWith(true, expect.anything());
  });

  it("shows the installing progress bar and message while installing", () => {
    setStatus(makeStatus({ installing: true, install_state: "installing", progress: 0.5, message: "PULLING" }));
    render(<FaceSwapSection />);
    expect(screen.getByText("settings.faceswap.state.installing")).toBeInTheDocument();
    expect(screen.getByText("PULLING")).toBeInTheDocument();
    // 安装中禁用再次点安装，避免叠装。
    const installButton = screen.getByText("settings.faceswap.install").closest("button");
    expect(installButton).toBeDisabled();
  });

  it("surfaces the failure message when install_state is failed", () => {
    setStatus(makeStatus({ install_state: "failed", message: "inswapper download failed" }));
    render(<FaceSwapSection />);
    expect(screen.getByText("settings.faceswap.state.failed")).toBeInTheDocument();
    expect(screen.getByText("inswapper download failed")).toBeInTheDocument();
  });

  it("offers start-engine and uninstall once installed, and calls the engine start", () => {
    setStatus(makeStatus({ installed: true, install_state: "ready", models_ready: true, deps_ready: true }));
    render(<FaceSwapSection />);
    expect(screen.getByText("settings.faceswap.state.ready")).toBeInTheDocument();
    expect(screen.getByText("settings.faceswap.startEngine")).toBeInTheDocument();
    expect(screen.getByText("settings.faceswap.uninstall")).toBeInTheDocument();
    fireEvent.click(screen.getByText("settings.faceswap.startEngine"));
    expect(startEngineMut.value.mutate).toHaveBeenCalledTimes(1);
  });

  it("disables start-engine when the virtual camera is unavailable", () => {
    setStatus(
      makeStatus({
        installed: true,
        install_state: "ready",
        vcam: { system: "darwin", available: false, hint: "install OBS" },
      }),
    );
    render(<FaceSwapSection />);
    const startButton = screen.getByText("settings.faceswap.startEngine").closest("button");
    expect(startButton).toBeDisabled();
    // 已装但缺 vcam：给安装指引提示。
    expect(screen.getByText("install OBS")).toBeInTheDocument();
  });

  it("shows stop-engine and the OBS hint once the engine is running", () => {
    setStatus(makeStatus({ installed: true, install_state: "ready", engine_running: true }));
    render(<FaceSwapSection />);
    expect(screen.getByText("settings.faceswap.stopEngine")).toBeInTheDocument();
    expect(screen.getByText("settings.faceswap.obsHint")).toBeInTheDocument();
    fireEvent.click(screen.getByText("settings.faceswap.stopEngine"));
    expect(stopEngineMut.value.mutate).toHaveBeenCalledTimes(1);
  });

  it("hides engine controls and warns desktop-only when not a local runtime", () => {
    setStatus(makeStatus({ installed: true, install_state: "ready", local_runtime: false }));
    render(<FaceSwapSection />);
    expect(screen.getByText("settings.faceswap.localOnlyHint")).toBeInTheDocument();
    // 非本机（Web 容器）形态：不给启停边车按钮。
    expect(screen.queryByText("settings.faceswap.startEngine")).not.toBeInTheDocument();
    expect(screen.queryByText("settings.faceswap.stopEngine")).not.toBeInTheDocument();
  });

  it("keeps the source-save action disabled until a field is edited, then submits the change", () => {
    setStatus(makeStatus());
    render(<FaceSwapSection />);
    const saveButton = screen.getByText("settings.faceswap.sources.save").closest("button");
    expect(saveButton).toBeDisabled();
    const hfInput = screen.getByPlaceholderText("settings.faceswap.sources.hfHint");
    fireEvent.change(hfInput, { target: { value: "https://my.mirror" } });
    expect(
      screen.getByText("settings.faceswap.sources.save").closest("button"),
    ).not.toBeDisabled();
    fireEvent.click(screen.getByText("settings.faceswap.sources.save"));
    expect(sourcesMut.value.mutate).toHaveBeenCalledWith(
      expect.objectContaining({ hf_endpoint: "https://my.mirror" }),
      expect.anything(),
    );
  });
});
