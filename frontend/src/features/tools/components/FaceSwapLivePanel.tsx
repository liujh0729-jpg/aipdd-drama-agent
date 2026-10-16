// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 工具 →「换脸直播」控制面板：仅上传人脸图 / 改参数 / 启停本机换脸边车。
// 「Web 不推流」——视频走 摄像头→边车→系统虚拟摄像头→OBS，本面板只发 localhost 控制请求。
// 门禁：enabled && 已装(依赖+模型) && 本机形态 && 虚拟摄像头可用，才允许启停；否则给指引。
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2, Play, Square, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  useFaceSwapEngineState,
  useFaceSwapStatus,
  useStartFaceSwapEngine,
  useStopFaceSwapEngine,
  useUpdateFaceSwapEngineConfig,
  useUploadFaceSwapSource,
  type FaceSwapEngineConfig,
} from "@/lib/queries/faceswap";
import { cn } from "@/lib/utils";

const PROVIDER_OPTIONS = ["cpu", "coreml", "cuda"] as const;

const PARAM_TOGGLES: Array<{ key: keyof FaceSwapEngineConfig; labelKey: string }> = [
  { key: "many_faces", labelKey: "settings.faceswap.panel.manyFaces" },
  { key: "mouth_mask", labelKey: "settings.faceswap.panel.mouthMask" },
  { key: "nsfw_filter", labelKey: "settings.faceswap.panel.nsfw" },
  { key: "face_enhancer", labelKey: "settings.faceswap.panel.enhancer" },
  { key: "live_mirror", labelKey: "settings.faceswap.panel.mirror" },
];

export function FaceSwapLivePanel() {
  const { t } = useTranslation();
  const statusQuery = useFaceSwapStatus();
  const status = statusQuery.data?.data;

  const engineRunning = status?.engine_running ?? false;
  const stateQuery = useFaceSwapEngineState(engineRunning);
  const engineState = stateQuery.data?.data;

  const startEngine = useStartFaceSwapEngine();
  const stopEngine = useStopFaceSwapEngine();
  const updateConfig = useUpdateFaceSwapEngineConfig();
  const uploadFace = useUploadFaceSwapSource();

  // 控制面板参数：从引擎回读的配置初始化，之后本地可编辑，改动即时透传边车 /config。
  const [form, setForm] = useState<FaceSwapEngineConfig>({});
  const seeded = useRef(false);
  useEffect(() => {
    const cfg = engineState?.config as Record<string, unknown> | undefined;
    if (cfg && !seeded.current) {
      setForm({
        many_faces: Boolean(cfg.many_faces),
        mouth_mask: Boolean(cfg.mouth_mask),
        nsfw_filter: Boolean(cfg.nsfw_filter),
        face_enhancer: Boolean(cfg.face_enhancer),
        live_mirror: Boolean(cfg.live_mirror),
        det_size: Number(cfg.det_size ?? 128),
        execution_provider: String(cfg.provider ?? "cpu"),
      });
      seeded.current = true;
    }
    if (!engineRunning) seeded.current = false;
  }, [engineState, engineRunning]);

  const ready = Boolean(status?.enabled && status?.installed && status?.local_runtime);
  const vcamMissing = Boolean(ready && status && !status.vcam?.available);

  const fps = useMemo(() => {
    const raw = engineState?.fps;
    return typeof raw === "number" ? raw.toFixed(1) : null;
  }, [engineState]);

  const pushConfig = (patch: FaceSwapEngineConfig) => {
    setForm((prev) => ({ ...prev, ...patch }));
    if (!engineRunning) return;
    updateConfig.mutate(patch, {
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("settings.faceswap.panel.uploadFailed")),
    });
  };

  const fileRef = useRef<HTMLInputElement>(null);

  const handleUpload = (file: File | undefined) => {
    if (!file) return;
    uploadFace.mutate(file, {
      onSuccess: () => toast.success(t("settings.faceswap.panel.uploaded")),
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("settings.faceswap.panel.uploadFailed")),
    });
  };

  const handleStart = () => {
    startEngine.mutate(undefined, {
      onSuccess: () => toast.success(t("settings.faceswap.engineStarted")),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("settings.faceswap.engineStartFailed")),
    });
  };

  const handleStop = () => {
    stopEngine.mutate(undefined, {
      onSuccess: () => toast.success(t("settings.faceswap.engineStopped")),
      onError: (err) => toast.error(err instanceof Error ? err.message : t("settings.faceswap.engineStopFailed")),
    });
  };

  // 未就绪：给清晰指引，不渲染控制项（避免点了报错）。
  if (!ready) {
    return (
      <div className="space-y-3 rounded-lg border border-border bg-white/[0.02] p-4">
        <p className="text-sm text-foreground">{t("settings.faceswap.panel.title")}</p>
        <p className="text-xs leading-relaxed text-muted-foreground">
          {status && !status.local_runtime
            ? t("settings.faceswap.panel.localOnly")
            : t("settings.faceswap.panel.notReady")}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-lg border border-border bg-white/[0.02] p-4">
      <div>
        <p className="text-sm font-medium text-foreground">{t("settings.faceswap.panel.title")}</p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {t("settings.faceswap.panel.intro")}
        </p>
      </div>

      {vcamMissing ? (
        <p className="rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">
          {status?.vcam?.hint || t("settings.faceswap.vcamMissing")}
        </p>
      ) : null}

      {/* 运行状态 + 启停 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span
            className={cn(
              "size-1.5 rounded-full",
              engineRunning && engineState?.running ? "bg-emerald-400" : "bg-muted-foreground/50",
            )}
          />
          <span>
            {t("settings.faceswap.panel.status")}
            {": "}
            <span className={engineRunning ? "text-emerald-300" : ""}>
              {engineRunning ? t("settings.faceswap.panel.running") : t("settings.faceswap.panel.idle")}
            </span>
          </span>
          {engineRunning && fps ? (
            <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
              {t("settings.faceswap.panel.fps", { fps })}
            </span>
          ) : null}
        </div>
        {engineRunning ? (
          <Button type="button" size="sm" variant="secondary" disabled={stopEngine.isPending} onClick={handleStop}>
            {stopEngine.isPending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Square className="size-3.5" aria-hidden />}
            {t("settings.faceswap.panel.stop")}
          </Button>
        ) : (
          <Button type="button" size="sm" disabled={startEngine.isPending || !status?.vcam?.available} onClick={handleStart}>
            {startEngine.isPending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
            {t("settings.faceswap.panel.start")}
          </Button>
        )}
      </div>

      {engineRunning ? (
        <>
          {/* 上传人脸源图（边车持有物理摄像头，故运行中才能上传） */}
          <div className="space-y-1.5">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
              {t("settings.faceswap.panel.uploadFace")}
            </Label>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => handleUpload(e.target.files?.[0])}
            />
            <div className="flex items-center gap-2">
              <Button type="button" size="sm" variant="outline" disabled={uploadFace.isPending} onClick={() => fileRef.current?.click()}>
                {uploadFace.isPending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <Upload className="size-3.5" aria-hidden />}
                {t("settings.faceswap.panel.uploadFace")}
              </Button>
              <span className="text-[11px] text-muted-foreground">{t("settings.faceswap.panel.dropHint")}</span>
            </div>
          </div>

          {/* 参数开关 */}
          <div className="space-y-2">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
              {t("settings.faceswap.panel.params")}
            </Label>
            <div className="grid grid-cols-2 gap-x-4 gap-y-2">
              {PARAM_TOGGLES.map(({ key, labelKey }) => (
                <label key={key} className="flex items-center gap-2 text-sm text-foreground">
                  <input
                    type="checkbox"
                    className="h-4 w-4 rounded border-border"
                    checked={Boolean(form[key])}
                    onChange={(e) => pushConfig({ [key]: e.target.checked } as FaceSwapEngineConfig)}
                  />
                  <span>{t(labelKey)}</span>
                </label>
              ))}
            </div>
            <div className="grid grid-cols-2 gap-3 pt-1">
              <div className="space-y-1">
                <Label className="justify-start text-[11px] font-normal text-muted-foreground">
                  {t("settings.faceswap.panel.detSize")}
                </Label>
                <Input
                  type="number"
                  min={64}
                  max={640}
                  step={32}
                  value={form.det_size ?? 128}
                  onChange={(e) => setForm((prev) => ({ ...prev, det_size: Number(e.target.value) }))}
                  onBlur={() => {
                    if (form.det_size != null) pushConfig({ det_size: Math.max(64, Math.min(640, form.det_size)) });
                  }}
                />
              </div>
              <div className="space-y-1">
                <Label className="justify-start text-[11px] font-normal text-muted-foreground">
                  {t("settings.faceswap.panel.provider")}
                </Label>
                <select
                  className="h-9 w-full rounded-md border border-border bg-transparent px-2 text-sm text-foreground"
                  value={form.execution_provider ?? "cpu"}
                  onChange={(e) => pushConfig({ execution_provider: e.target.value })}
                >
                  {PROVIDER_OPTIONS.map((p) => (
                    <option key={p} value={p} className="bg-background">
                      {p}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("settings.faceswap.panel.obsHint")}
          </p>
        </>
      ) : (
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("settings.faceswap.obsHint")}
        </p>
      )}
    </div>
  );
}
