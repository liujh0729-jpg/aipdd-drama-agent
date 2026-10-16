// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 换脸直播：本机引擎按需部署（依赖 + 模型自助下载）+ 开关 + 下载源
// + 边车控制（启停 / 参数 / 上传人脸）。后端端点见 src/novelvideo/api/routes/faceswap.py。
// 「Web 不推流」：这里只有控制类请求，视频走 摄像头→边车→系统虚拟摄像头→OBS，不经浏览器。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

export type FaceSwapInstallState = "not_installed" | "installing" | "ready" | "failed";

export interface FaceSwapSources {
  pip_index_url: string;
  hf_endpoint: string;
  https_proxy: string;
}

/** 后端探测到的运行环境/硬件（建议的 onnxruntime 执行后端）。 */
export interface FaceSwapHardware {
  os: string;
  arch: string;
  has_nvidia: boolean;
  provider: string;
  reason: string;
}

/** 系统级虚拟摄像头前置探测（OBS 读它）。 */
export interface FaceSwapVcam {
  system: string;
  available: boolean;
  hint: string;
}

export interface FaceSwapStatus {
  enabled: boolean;
  install_state: FaceSwapInstallState;
  installing: boolean;
  installed: boolean;
  deps_ready: boolean;
  models_ready: boolean;
  progress: number;
  message: string;
  installed_at: string;
  sources: FaceSwapSources;
  venv_path: string;
  models_dir: string;
  control_port: number;
  engine_running: boolean;
  local_runtime: boolean;
  vcam: FaceSwapVcam;
  hardware: FaceSwapHardware;
  can_install: boolean;
  engine_script_present: boolean;
  log_tail: string;
}

/** 边车运行态（经后端 localhost 转发取得）。 */
export interface FaceSwapEngineState {
  running: boolean;
  fps?: number;
  source_image?: string;
  config?: Record<string, unknown>;
  message?: string;
}

export function useFaceSwapStatus(enabled = true) {
  return useQuery({
    queryKey: queryKeys.faceswapStatus(),
    queryFn: () =>
      jsonWithBackendError<OkResponse<FaceSwapStatus>>(
        api.get("api/v1/admin/faceswap/status", { throwHttpErrors: false }),
      ),
    enabled,
    staleTime: 5_000,
    refetchInterval: (query) =>
      query.state.data?.data?.installing ? 2_000 : false,
  });
}

export function useUpdateFaceSwapSources() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: Partial<FaceSwapSources>) =>
      jsonWithBackendError<OkResponse<FaceSwapStatus>>(
        api.put("api/v1/admin/faceswap/sources", {
          json: input,
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.faceswapStatus() }),
  });
}

export function useSetFaceSwapEnabled() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (enabled: boolean) =>
      jsonWithBackendError<OkResponse<FaceSwapStatus>>(
        api.post("api/v1/admin/faceswap/enable", {
          json: { enabled },
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.faceswapStatus() }),
  });
}

export function useStartFaceSwapInstall() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      jsonWithBackendError<OkResponse<FaceSwapStatus>>(
        api.post("api/v1/admin/faceswap/install", { throwHttpErrors: false }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.faceswapStatus() }),
  });
}

export function useUninstallFaceSwap() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      jsonWithBackendError<OkResponse<FaceSwapStatus>>(
        api.post("api/v1/admin/faceswap/uninstall", { throwHttpErrors: false }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.faceswapStatus() }),
  });
}

// ── 边车引擎生命周期 + 控制（Web 不推流，仅控制）────────────────────────
export function useStartFaceSwapEngine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      jsonWithBackendError<OkResponse<FaceSwapStatus>>(
        api.post("api/v1/admin/faceswap/engine/start", { throwHttpErrors: false }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.faceswapStatus() });
      qc.invalidateQueries({ queryKey: queryKeys.faceswapEngineState() });
    },
  });
}

export function useStopFaceSwapEngine() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      jsonWithBackendError<OkResponse<FaceSwapStatus>>(
        api.post("api/v1/admin/faceswap/engine/stop", { throwHttpErrors: false }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.faceswapStatus() });
      qc.invalidateQueries({ queryKey: queryKeys.faceswapEngineState() });
    },
  });
}

export function useFaceSwapEngineState(enabled = true) {
  return useQuery({
    queryKey: queryKeys.faceswapEngineState(),
    queryFn: () =>
      jsonWithBackendError<OkResponse<FaceSwapEngineState>>(
        api.get("api/v1/admin/faceswap/engine/state", { throwHttpErrors: false }),
      ),
    enabled,
    staleTime: 2_000,
    refetchInterval: 2_000,
  });
}

export interface FaceSwapEngineConfig {
  many_faces?: boolean;
  mouth_mask?: boolean;
  nsfw_filter?: boolean;
  face_enhancer?: boolean;
  live_mirror?: boolean;
  det_size?: number;
  execution_provider?: string;
  source_image?: string;
}

export function useUpdateFaceSwapEngineConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: FaceSwapEngineConfig) =>
      jsonWithBackendError<OkResponse<FaceSwapEngineState>>(
        api.post("api/v1/admin/faceswap/engine/config", {
          json: input,
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.faceswapEngineState() }),
  });
}

/** 上传人脸源图到边车（multipart）。视频不经此口。 */
export function useUploadFaceSwapSource() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return jsonWithBackendError<OkResponse<{ path: string }>>(
        api.post("api/v1/admin/faceswap/engine/face", {
          body: form,
          throwHttpErrors: false,
        }),
      );
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.faceswapEngineState() });
    },
  });
}
