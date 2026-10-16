// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 工具 →「换脸直播」控制面板：门禁（启用+已装+本机+vcam）、启停、
// 参数即时透传、人脸上传的渲染与交互回归。仿现有 section 测试的钩子存根模式。
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { FaceSwapLivePanel } from "@/features/tools/components/FaceSwapLivePanel";
import type { FaceSwapStatus } from "@/lib/queries/faceswap";

const { statusState, engineStateState, startMut, stopMut, configMut, uploadMut } = vi.hoisted(() => ({
  statusState: { value: {} as Record<string, unknown> },
  engineStateState: { value: {} as Record<string, unknown> },
  startMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
  stopMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
  configMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
  uploadMut: { value: { mutate: vi.fn(), isPending: false } as Record<string, unknown> },
}));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock("@/lib/queries/faceswap", () => ({
  useFaceSwapStatus: () => statusState.value,
  useFaceSwapEngineState: () => engineStateState.value,
  useStartFaceSwapEngine: () => startMut.value,
  useStopFaceSwapEngine: () => stopMut.value,
  useUpdateFaceSwapEngineConfig: () => configMut.value,
  useUploadFaceSwapSource: () => uploadMut.value,
}));

function makeStatus(overrides: Partial<FaceSwapStatus> = {}): FaceSwapStatus {
  return {
    enabled: true,
    install_state: "ready",
    installing: false,
    installed: true,
    deps_ready: true,
    models_ready: true,
    progress: 1,
    message: "",
    installed_at: "2026-01-01",
    sources: { pip_index_url: "", hf_endpoint: "https://hf-mirror.com", https_proxy: "" },
    venv_path: "/v",
    models_dir: "/m",
    control_port: 18899,
    engine_running: false,
    local_runtime: true,
    vcam: { system: "darwin", available: true, hint: "" },
    hardware: { os: "darwin", arch: "arm64", has_nvidia: false, provider: "coreml", reason: "Apple Silicon" },
    can_install: true,
    engine_script_present: true,
    log_tail: "",
    ...overrides,
  };
}

function setStatus(data: FaceSwapStatus | undefined) {
  statusState.value = { isLoading: false, error: null, refetch: vi.fn(), data: data ? { data } : undefined };
}

function setEngineState(running: boolean, extra: Record<string, unknown> = {}) {
  engineStateState.value = {
    isLoading: false,
    refetch: vi.fn(),
    data: { data: { running, ...extra } },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  startMut.value = { mutate: vi.fn(), isPending: false };
  stopMut.value = { mutate: vi.fn(), isPending: false };
  configMut.value = { mutate: vi.fn(), isPending: false };
  uploadMut.value = { mutate: vi.fn(), isPending: false };
  setEngineState(false);
});

describe("FaceSwapLivePanel", () => {
  it("shows the not-ready guidance and hides controls until enabled+installed+local", () => {
    setStatus(makeStatus({ enabled: false }));
    render(<FaceSwapLivePanel />);
    expect(screen.getByText("settings.faceswap.panel.notReady")).toBeInTheDocument();
    expect(screen.queryByText("settings.faceswap.panel.start")).not.toBeInTheDocument();
  });

  it("explains the desktop-only limit on a non-local runtime", () => {
    setStatus(makeStatus({ local_runtime: false }));
    render(<FaceSwapLivePanel />);
    expect(screen.getByText("settings.faceswap.panel.localOnly")).toBeInTheDocument();
    expect(screen.queryByText("settings.faceswap.panel.start")).not.toBeInTheDocument();
  });

  it("shows the start action when ready but idle, and fires the start mutation", () => {
    setStatus(makeStatus({ engine_running: false }));
    render(<FaceSwapLivePanel />);
    expect(screen.getByText("settings.faceswap.panel.idle")).toBeInTheDocument();
    expect(screen.getByText("settings.faceswap.panel.start")).toBeInTheDocument();
    // 空闲：不渲染参数/上传区，只给 OBS 读取指引。
    expect(screen.queryByText("settings.faceswap.panel.uploadFace")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("settings.faceswap.panel.start"));
    expect(startMut.value.mutate).toHaveBeenCalledTimes(1);
  });

  it("disables start when the virtual camera is unavailable", () => {
    setStatus(makeStatus({ engine_running: false, vcam: { system: "darwin", available: false, hint: "install OBS vcam" } }));
    render(<FaceSwapLivePanel />);
    const startButton = screen.getByText("settings.faceswap.panel.start").closest("button");
    expect(startButton).toBeDisabled();
    expect(screen.getByText("install OBS vcam")).toBeInTheDocument();
  });

  it("reveals stop, upload, params and fps once the engine is running", () => {
    setStatus(makeStatus({ engine_running: true }));
    setEngineState(true, { fps: 24.0 });
    render(<FaceSwapLivePanel />);
    expect(screen.getByText("settings.faceswap.panel.running")).toBeInTheDocument();
    expect(screen.getByText("settings.faceswap.panel.stop")).toBeInTheDocument();
    // 上传人脸：Label 与按钮共用同一文案，故两处出现。
    expect(screen.getAllByText("settings.faceswap.panel.uploadFace").length).toBeGreaterThan(0);
    expect(screen.getByText("settings.faceswap.panel.fps")).toBeInTheDocument();
    fireEvent.click(screen.getByText("settings.faceswap.panel.stop"));
    expect(stopMut.value.mutate).toHaveBeenCalledTimes(1);
  });

  it("pushes a config patch to the sidecar the moment a parameter toggle changes", () => {
    setStatus(makeStatus({ engine_running: true }));
    setEngineState(true, { config: { many_faces: false, det_size: 128, provider: "cpu" } });
    render(<FaceSwapLivePanel />);
    fireEvent.click(screen.getByText("settings.faceswap.panel.mouthMask"));
    expect(configMut.value.mutate).toHaveBeenCalledWith(
      { mouth_mask: true },
      expect.anything(),
    );
  });

  it("pushes the provider selection to the sidecar", () => {
    setStatus(makeStatus({ engine_running: true }));
    setEngineState(true, { config: { provider: "cpu" } });
    render(<FaceSwapLivePanel />);
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "cuda" } });
    expect(configMut.value.mutate).toHaveBeenCalledWith(
      { execution_provider: "cuda" },
      expect.anything(),
    );
  });
});
