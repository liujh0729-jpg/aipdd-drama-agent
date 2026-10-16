// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 →「环境安装 · 换脸直播」：本机换脸引擎的按需部署（依赖 + 模型自助
// 下载，仿 3D 世界）+ 开关 + 下载源 + 边车启停。视频不经浏览器，OBS 读系统虚拟摄像头。
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  Download,
  Loader2,
  Play,
  Square,
  Trash2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useFaceSwapStatus,
  useSetFaceSwapEnabled,
  useStartFaceSwapEngine,
  useStartFaceSwapInstall,
  useStopFaceSwapEngine,
  useUninstallFaceSwap,
  useUpdateFaceSwapSources,
  type FaceSwapSources,
} from "@/lib/queries/faceswap";
import { cn } from "@/lib/utils";

const SOURCE_FIELDS: Array<{ key: keyof FaceSwapSources; labelKey: string; hintKey: string }> = [
  { key: "pip_index_url", labelKey: "settings.faceswap.sources.pip", hintKey: "settings.faceswap.sources.pipHint" },
  { key: "hf_endpoint", labelKey: "settings.faceswap.sources.hf", hintKey: "settings.faceswap.sources.hfHint" },
  { key: "https_proxy", labelKey: "settings.faceswap.sources.proxy", hintKey: "settings.faceswap.sources.proxyHint" },
];

const CHIP_CLASS = "rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium";

export function FaceSwapSection() {
  const { t } = useTranslation();
  const statusQuery = useFaceSwapStatus();
  const setEnabled = useSetFaceSwapEnabled();
  const updateSources = useUpdateFaceSwapSources();
  const startInstall = useStartFaceSwapInstall();
  const uninstall = useUninstallFaceSwap();
  const startEngine = useStartFaceSwapEngine();
  const stopEngine = useStopFaceSwapEngine();

  const status = statusQuery.data?.data;
  const [sources, setSources] = useState<FaceSwapSources | null>(null);
  const seeded = useRef(false);

  // 只在首次拿到状态时用后端值填充输入框，避免轮询覆盖用户正在编辑的内容。
  useEffect(() => {
    if (status && !seeded.current) {
      setSources(status.sources);
      seeded.current = true;
    }
  }, [status]);

  const installing = status?.installing ?? false;
  const stateText = status?.installed
    ? t("settings.faceswap.state.ready")
    : installing
      ? t("settings.faceswap.state.installing")
      : status?.install_state === "failed"
        ? t("settings.faceswap.state.failed")
        : t("settings.faceswap.state.notInstalled");

  const dirty = status && sources
    ? SOURCE_FIELDS.some(({ key }) => (sources[key] ?? "") !== (status.sources[key] ?? ""))
    : false;

  const handleSaveSources = () => {
    if (!sources) return;
    updateSources.mutate(sources, {
      onSuccess: () => toast.success(t("settings.faceswap.sources.saved")),
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("settings.faceswap.sources.saveFailed")),
    });
  };

  const handleToggleEnabled = () => {
    if (!status) return;
    const next = !status.enabled;
    setEnabled.mutate(next, {
      onSuccess: () =>
        toast.success(next ? t("settings.faceswap.enabledOn") : t("settings.faceswap.enabledOff")),
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("settings.faceswap.toggleFailed")),
    });
  };

  const handleInstall = () => {
    startInstall.mutate(undefined, {
      onSuccess: () => toast.success(t("settings.faceswap.installStarted")),
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("settings.faceswap.installFailed")),
    });
  };

  const handleUninstall = () => {
    uninstall.mutate(undefined, {
      onSuccess: () => toast.success(t("settings.faceswap.uninstalled")),
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("settings.faceswap.uninstallFailed")),
    });
  };

  const handleStartEngine = () => {
    startEngine.mutate(undefined, {
      onSuccess: () => toast.success(t("settings.faceswap.engineStarted")),
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("settings.faceswap.engineStartFailed")),
    });
  };

  const handleStopEngine = () => {
    stopEngine.mutate(undefined, {
      onSuccess: () => toast.success(t("settings.faceswap.engineStopped")),
      onError: (err) =>
        toast.error(err instanceof Error ? err.message : t("settings.faceswap.engineStopFailed")),
    });
  };

  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 rounded-full",
            status?.installed ? "bg-emerald-400" : "bg-amber-400",
          )}
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.faceswap.title")}
        </h3>
        {status && !status.installed ? (
          <AlertTriangle
            className="size-3.5 text-amber-400"
            aria-label={t("settings.faceswap.warningIconLabel")}
          />
        ) : null}
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.faceswap.description")}
      </p>

      {status ? (
        <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <span>
            {t("settings.faceswap.status")}
            {": "}
            <span className={status.installed ? "text-emerald-400" : "text-amber-300"}>
              {stateText}
            </span>
          </span>
          <span
            className={cn(
              CHIP_CLASS,
              status.enabled ? "text-emerald-300" : "text-muted-foreground",
            )}
          >
            {status.enabled ? t("settings.faceswap.enabledOn") : t("settings.faceswap.enabledOff")}
          </span>
          {status.deps_ready ? (
            <span className={cn(CHIP_CLASS, "text-muted-foreground")}>
              {t("settings.faceswap.depsReady")}
            </span>
          ) : null}
          <span
            className={cn(
              CHIP_CLASS,
              status.models_ready ? "text-muted-foreground" : "text-amber-300",
            )}
          >
            {status.models_ready
              ? t("settings.faceswap.modelsReady")
              : t("settings.faceswap.modelsMissing")}
          </span>
          <span
            className={cn(
              CHIP_CLASS,
              status.vcam?.available ? "text-muted-foreground" : "text-amber-300",
            )}
          >
            {status.vcam?.available
              ? t("settings.faceswap.vcamReady")
              : t("settings.faceswap.vcamMissing")}
          </span>
        </p>
      ) : null}

      {status && !status.local_runtime ? (
        <p className="mt-3 rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">
          {t("settings.faceswap.localOnlyHint")}
        </p>
      ) : null}

      {statusQuery.error ? (
        <div className="mt-6 flex items-center gap-3 text-sm text-muted-foreground">
          <span>{statusQuery.error.message || t("settings.faceswap.loadFailed")}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void statusQuery.refetch()}
          >
            {t("common.retry")}
          </Button>
        </div>
      ) : !status || !sources ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          {t("settings.faceswap.loading")}
        </div>
      ) : (
        <>
          {/* 状态 + 开关 + 边车启停 */}
          <div className="mt-4 space-y-3 rounded-lg border border-border bg-white/[0.02] p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("settings.faceswap.enableHint")}
              </p>
              <Button
                type="button"
                size="sm"
                variant={status.enabled ? "secondary" : "default"}
                disabled={setEnabled.isPending}
                onClick={handleToggleEnabled}
              >
                {setEnabled.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : null}
                {status.enabled ? t("settings.faceswap.disable") : t("settings.faceswap.enable")}
              </Button>
            </div>

            {installing ? (
              <div className="space-y-1.5">
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                  <div
                    className="h-full rounded-full bg-emerald-400 transition-[width] duration-500"
                    style={{ width: `${Math.max(4, Math.round(status.progress * 100))}%` }}
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  {status.message || t("settings.faceswap.installing")}
                </p>
              </div>
            ) : status.install_state === "failed" && status.message ? (
              <p className="rounded-md border border-rose-400/30 bg-rose-400/10 px-3 py-2 text-xs text-rose-200">
                {status.message}
              </p>
            ) : null}

            {status.log_tail && (installing || status.install_state === "failed") ? (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground">
                  {t("settings.faceswap.log")}
                </summary>
                <pre className="mt-2 max-h-52 overflow-auto rounded-md bg-black/40 p-3 text-[11px] leading-relaxed text-muted-foreground">
                  {status.log_tail}
                </pre>
              </details>
            ) : null}

            {status.vcam && !status.vcam.available && status.installed ? (
              <p className="rounded-md border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-200">
                {status.vcam.hint}
              </p>
            ) : null}

            <div className="flex flex-wrap items-center gap-2 pt-1">
              <Button
                type="button"
                size="sm"
                disabled={installing || startInstall.isPending}
                onClick={handleInstall}
              >
                {installing ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : (
                  <Download className="size-3.5" aria-hidden />
                )}
                {status.installed
                  ? t("settings.faceswap.reinstall")
                  : t("settings.faceswap.install")}
              </Button>

              {status.installed && status.local_runtime ? (
                status.engine_running ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={stopEngine.isPending}
                    onClick={handleStopEngine}
                  >
                    {stopEngine.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Square className="size-3.5" aria-hidden />
                    )}
                    {t("settings.faceswap.stopEngine")}
                  </Button>
                ) : (
                  <Button
                    type="button"
                    size="sm"
                    disabled={startEngine.isPending || !status.vcam?.available}
                    onClick={handleStartEngine}
                  >
                    {startEngine.isPending ? (
                      <Loader2 className="size-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Play className="size-3.5" aria-hidden />
                    )}
                    {t("settings.faceswap.startEngine")}
                  </Button>
                )
              ) : null}

              {status.installed ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="text-rose-300 hover:bg-rose-400/10 hover:text-rose-200"
                  disabled={installing || uninstall.isPending}
                  onClick={handleUninstall}
                >
                  {uninstall.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" aria-hidden />
                  ) : (
                    <Trash2 className="size-3.5" aria-hidden />
                  )}
                  {t("settings.faceswap.uninstall")}
                </Button>
              ) : null}
            </div>

            {status.engine_running ? (
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {t("settings.faceswap.obsHint")}
              </p>
            ) : null}
          </div>

          {/* 下载源 */}
          <div className="mt-4 space-y-3 rounded-lg border border-border bg-white/[0.02] p-4">
            <div>
              <h4 className="text-sm font-medium text-foreground">
                {t("settings.faceswap.sources.title")}
              </h4>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {t("settings.faceswap.sources.description")}
              </p>
              {status.hardware ? (
                <div className="mt-2 rounded-md border border-border bg-white/[0.03] px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
                  <span className="font-medium text-foreground">
                    {t("settings.faceswap.hardware.title")}
                    {": "}
                  </span>
                  <span>{status.hardware.reason}</span>
                </div>
              ) : null}
            </div>
            <div className="space-y-3">
              {SOURCE_FIELDS.map(({ key, labelKey, hintKey }) => (
                <div key={key} className="grid grid-cols-[120px_1fr] items-start gap-3">
                  <Label className="mt-1.5 justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
                    {t(labelKey)}
                  </Label>
                  <div className="space-y-1">
                    <Input
                      value={sources[key] ?? ""}
                      placeholder={t(hintKey)}
                      onChange={(event) =>
                        setSources((prev) =>
                          prev ? { ...prev, [key]: event.target.value } : prev,
                        )
                      }
                    />
                    <p className="text-[11px] text-muted-foreground/80">{t(hintKey)}</p>
                  </div>
                </div>
              ))}
            </div>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                size="sm"
                disabled={!dirty || updateSources.isPending}
                onClick={handleSaveSources}
              >
                {updateSources.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : null}
                {t("settings.faceswap.sources.save")}
              </Button>
              <span className="text-[11px] text-muted-foreground">
                {t("settings.faceswap.sources.saveHint")}
              </span>
            </div>
          </div>
        </>
      )}
    </section>
  );
}
