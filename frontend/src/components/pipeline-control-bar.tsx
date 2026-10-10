// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 一键整链流水线控制条：与 MCP/REST 共用服务端权威计划器，循环由
// @/lib/pipeline-driver（唯一客户端循环）驱动，三档模式 + 检查点审批 + 进度 + 停止。
// 「运行前设置」只收集生成参数（模型/风格/画幅/时长/故事版）；故事正文一律以「故事」
// 栏目为准（联动：运行前先把页面上未保存的草稿落盘），弹窗不再另贴一份故事。
// 只推进到镜头首帧，绝不提交视频（视频在别处按需生成）。样式遵循 DESIGN.md。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Play, Settings2, Square } from "lucide-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  EMPTY_PIPELINE_RUN_SETTINGS,
  PipelineSettingsDialog,
  type PipelineRunSettings,
} from "@/components/pipeline-settings-dialog";
import type { PlanStep } from "@/api/pipeline";
import type { Episode } from "@/types/episode";
import type { OkResponse } from "@/types/api";
import { useEpisodes } from "@/lib/queries/episodes";
import { useProvisionFeatureRoutes } from "@/lib/queries/model-gateway";
import { usePromptLibrary } from "@/lib/queries/prompt-library";
import { useProjectAspectRatio } from "@/stores/aspect-ratio-store";
import { STORY_VERSION_PROMPT_KEY } from "@/lib/episode-auto-pipeline";
import {
  PipelineStoppedError,
  runPipelineDriven,
  type PipelineGenerationSettings,
  type PipelineMode,
  type StoryVersionConfig,
} from "@/lib/pipeline-driver";
import { queryKeys } from "@/lib/query-keys";
import { cn } from "@/lib/utils";

const MODES: PipelineMode[] = ["manual", "checkpoint", "auto"];

const MODE_LABEL_KEY: Record<PipelineMode, string> = {
  manual: "pipeline.modeManual",
  checkpoint: "pipeline.modeCheckpoint",
  auto: "pipeline.modeAuto",
};

// 故事版创作要求来自该集摘要 / 原文，截断避免把整集正文塞进图像 prompt（与 episode-auto 一致）。
const STORY_VERSION_REQUIREMENT_MAX_CHARS = 800;

/** 渲染一个动作的可读标签（方法 + 去掉项目前缀的路径尾段）。 */
function actionLabel(action: { method?: string; path?: string; kind?: string }): string {
  if (action.kind) return action.kind.replace(/_/g, " ");
  const tail = (action.path ?? "").replace(/^\/api\/v1\/projects\/[^/]+/, "");
  return `${(action.method ?? "POST").toUpperCase()} ${tail || "/"}`;
}

/** 收集到的运行设置 → driver 生成参数（只带上非默认值，其余交后端智能默认/推导）。 */
function toGenerationSettings(s: PipelineRunSettings): PipelineGenerationSettings {
  const out: PipelineGenerationSettings = { aspectRatio: s.aspectRatio };
  if (s.model) out.model = s.model;
  if (s.storyStyle) out.storyStyle = s.storyStyle;
  if (s.episodeCount != null) out.episodeCount = s.episodeCount;
  if (s.totalSeconds != null) out.totalSeconds = s.totalSeconds;
  if (s.secondsPerEpisode != null) out.secondsPerEpisode = s.secondsPerEpisode;
  return out;
}

/** 设置是否偏离默认（用于齿轮上的「已配置」提示点）。 */
function isCustomized(s: PipelineRunSettings, defaultAspect: PipelineRunSettings["aspectRatio"]): boolean {
  return Boolean(
    s.model ||
      s.textModel ||
      s.videoModel ||
      s.storyStyle ||
      // 故事版是全自动的默认前置，开着不算「已配置」；用户主动关掉才提示偏离。
      !s.includeStoryVersion ||
      s.episodeCount != null ||
      s.totalSeconds != null ||
      s.secondsPerEpisode != null ||
      s.aspectRatio !== defaultAspect,
  );
}

/** 页面已选参数 → 运行设置的联动默认（导演风格 / 集数与故事卡共用一组状态）。 */
export interface PipelineLinkedDefaults {
  storyStyle?: string;
  episodeCount?: number | null;
}

export interface PipelineControlBarProps {
  project: string;
  /**
   * [aipdd fork delta] 与「故事」页联动：整链一律以服务端故事源为准，运行前调用本钩子
   * 把页面上的故事（含未保存草稿）落盘；返回 false = 前置动作失败，本次运行中止。
   */
  prepareStory?: () => Promise<boolean>;
  /** 页面故事正文字数：用于「将使用当前故事」回显与空内容拦截（undefined = 不拦截）。 */
  storyCharCount?: number;
  /** 故事卡上的导演风格 / 集数，作为设置项初值并随页面选择同步。 */
  defaults?: PipelineLinkedDefaults;
  /** 宿主页上有互斥任务在跑（如故事→剧本生成中）：不让叠发整链。 */
  busy?: boolean;
  className?: string;
}

export function PipelineControlBar({
  project,
  prepareStory,
  storyCharCount,
  defaults,
  busy = false,
  className,
}: PipelineControlBarProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const { orientation } = useProjectAspectRatio(project);
  const defaultAspect: PipelineRunSettings["aspectRatio"] =
    orientation === "landscape" ? "16:9" : "2:3";

  const [mode, setMode] = useState<PipelineMode>("checkpoint");
  const [running, setRunning] = useState(false);
  const [stageMessage, setStageMessage] = useState<string | null>(null);
  const [pending, setPending] = useState<PlanStep | null>(null);
  const [settings, setSettings] = useState<PipelineRunSettings>(() => ({
    ...EMPTY_PIPELINE_RUN_SETTINGS,
    aspectRatio: defaultAspect,
    storyStyle: defaults?.storyStyle ?? "",
    episodeCount: defaults?.episodeCount ?? null,
    // [aipdd fork delta] 全自动把「故事版」也当前置：默认就开（提示词未配时自动失效），
    // 不要求用户先去设置里摸一下开关。
    includeStoryVersion: true,
  }));
  const [settingsOpen, setSettingsOpen] = useState(false);
  const stopRef = useRef(false);
  const resolverRef = useRef<((approved: boolean) => void) | null>(null);

  const { data: promptRes } = usePromptLibrary();
  const storyVersionBasePrompt = useMemo(() => {
    const items = promptRes?.data?.items ?? [];
    const found = items.find((item) => item.key === STORY_VERSION_PROMPT_KEY);
    return (found?.value || found?.default || "").trim();
  }, [promptRes]);
  const storyVersionAvailable = Boolean(storyVersionBasePrompt);
  useEpisodes(project);

  // [aipdd fork delta] 与故事卡联动：页面上改了风格 / 集数，运行设置跟着走（不运行才同步，
  // 避免打断正在推进的整链）。依赖原始值而非对象字面量，否则会每帧重置。
  const linkedStyle = defaults?.storyStyle ?? "";
  const linkedEpisodes = defaults?.episodeCount ?? null;
  useEffect(() => {
    if (running) return;
    setSettings((prev) =>
      prev.storyStyle === linkedStyle && prev.episodeCount === linkedEpisodes
        ? prev
        : { ...prev, storyStyle: linkedStyle, episodeCount: linkedEpisodes },
    );
  }, [linkedStyle, linkedEpisodes, running]);

  const invalidate = useCallback(
    (episode: number | null) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.episodes(project) });
      queryClient.invalidateQueries({ queryKey: queryKeys.pipelineStatus(project) });
      if (episode != null) {
        queryClient.invalidateQueries({ queryKey: queryKeys.script(project, episode) });
        queryClient.invalidateQueries({ queryKey: queryKeys.beats(project, episode) });
        queryClient.invalidateQueries({ queryKey: queryKeys.grids(project, episode) });
      }
    },
    [project, queryClient],
  );

  // 检查点档：把「下一步动作清单」弹给用户确认，批准才真正发射。
  const approveGate = useCallback(
    (step: PlanStep) =>
      new Promise<boolean>((resolve) => {
        resolverRef.current = resolve;
        setPending(step);
      }),
    [],
  );

  const decide = useCallback((approved: boolean) => {
    setPending(null);
    const resolve = resolverRef.current;
    resolverRef.current = null;
    resolve?.(approved);
  }, []);

  // 故事版配置：basePrompt 取提示词库，创作要求运行时按集从最新分集缓存现取
  // （driver 每段完成会 invalidate episodes，故这里读 getQueryData 而非挂载时快照）。
  const buildStoryVersion = useCallback((): StoryVersionConfig | undefined => {
    if (!settings.includeStoryVersion || !storyVersionBasePrompt) return undefined;
    return {
      basePrompt: storyVersionBasePrompt,
      categoryLabel: t("canvas.assetLibrary.category.storyVersion"),
      requirementFor: (ep: number) => {
        const cached = queryClient.getQueryData<OkResponse<Episode[]>>(
          queryKeys.episodes(project),
        );
        const row = cached?.data?.find((e) => e.number === ep);
        const text = String(row?.summary || row?.beat_source_text || row?.raw_content || row?.story || "")
          .replace(/\s+/g, " ")
          .trim();
        return text.slice(0, STORY_VERSION_REQUIREMENT_MAX_CHARS);
      },
    };
  }, [settings.includeStoryVersion, storyVersionBasePrompt, project, queryClient, t]);

  const provision = useProvisionFeatureRoutes();

  /**
   * [aipdd fork delta] 运行前把「三个模型」这一次选择铺开成整链要用的全部功能位绑定。
   *
   * 只在用户真的选过时发这一笔（三个都留空 = 什么都不动，交回后端既有的解析链）。名字
   * 不在渠道目录里时后端回 400 中文 —— 那是「配齐」唯一该拦住一次运行的理由：继续跑
   * 下去只会让整链在网关的 ``No available channel``（503）上撞得七零八落。
   */
  const provisionModels = useCallback(async (): Promise<boolean> => {
    const input = {
      textModel: settings.textModel || undefined,
      imageModel: settings.model || undefined,
      videoModel: settings.videoModel || undefined,
    };
    if (!input.textModel && !input.imageModel && !input.videoModel) return true;
    try {
      const result = await provision.mutateAsync(input);
      if (result?.hints?.length) {
        // 挑不到的那几位（典型是向量位要人填维度）：说清楚去哪儿补，但不拦这次运行。
        toast.warning(result.hints.slice(0, 2).join("；"));
      } else if ((result?.projectionFailed ?? []).length || result?.projectionError) {
        toast.warning(t("pipeline.provisionProjectionFailed"));
      }
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
      return false;
    }
  }, [provision, settings.textModel, settings.model, settings.videoModel, t]);

  const run = useCallback(async () => {
    // 空故事 / 前置任务未终态都不发计划请求：整链第一步就是故事→剧本，
    // 叠发只会拿到半截状态白烧一轮轮询。
    if (storyCharCount === 0 || busy) {
      toast.warning(t(storyCharCount === 0 ? "pipeline.needStory" : "pipeline.busy"));
      return;
    }
    stopRef.current = false;
    setRunning(true);
    setStageMessage(null);
    try {
      // 联动：先把页面上的故事落盘，计划器读到的就是用户刚看到的那一份；前置失败
      // （保存报错）直接中止，不用旧故事静默跑一轮。故事正文不再走运行参数。
      if (prepareStory && !(await prepareStory())) {
        setRunning(false);
        return;
      }
      if (!(await provisionModels())) {
        setRunning(false);
        return;
      }
      const result = await runPipelineDriven({
        project,
        mode,
        ceiling: "full",
        settings: toGenerationSettings(settings),
        // 资产规划（身份/场景/道具）也是整链前提：缺什么由服务端计划器补什么，
        // 先后次序按真实依赖（脚本前补身份，脚本后补场景/道具）。
        planEpisodeAssets: true,
        includeStoryVersion: settings.includeStoryVersion && storyVersionAvailable,
        storyVersion: buildStoryVersion(),
        isStopped: () => stopRef.current,
        onStage: (step) => setStageMessage(step.message || step.stage),
        approveGate,
        onInvalidate: invalidate,
      });
      if (result.kind === "requires-input") {
        // 服务端仍判定缺前置输入（如故事源为空）：不是「完成」，只提醒去页面上补故事。
        toast.warning(t("pipeline.needStory"));
      } else if (result.kind === "completed") {
        toast.success(t("pipeline.completed"));
      }
      // manual-step / ceiling（含取消审批）：静默，进度条已反映当前阶段。
    } catch (err) {
      if (err instanceof PipelineStoppedError) {
        toast.info(t("pipeline.stopped"));
      } else {
        toast.error(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setRunning(false);
      decide(false); // 收尾：撤销任何未决审批，避免循环悬挂
    }
  }, [
    project,
    mode,
    settings,
    storyCharCount,
    provisionModels,
    prepareStory,
    busy,
    storyVersionAvailable,
    buildStoryVersion,
    approveGate,
    invalidate,
    t,
    decide,
  ]);

  const stop = useCallback(() => {
    stopRef.current = true;
    decide(false);
  }, [decide]);

  const customized = isCustomized(settings, defaultAspect);
  // 页面上没故事 / 还有互斥任务在跑时不让按：按了也没可用入参。
  const noStory = storyCharCount === 0;
  const blockedReason = noStory
    ? t("pipeline.needStory")
    : busy
      ? t("pipeline.busy")
      : undefined;

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <div
        role="radiogroup"
        aria-label={t("pipeline.modeGroupLabel")}
        className="inline-flex items-center gap-0.5 rounded-[8px] border border-border bg-card p-0.5"
      >
        {MODES.map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={mode === m}
            disabled={running}
            onClick={() => setMode(m)}
            className={cn(
              "rounded-[6px] px-2.5 py-1 text-xs font-medium transition-colors outline-none",
              "focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
              mode === m
                ? "bg-foreground/90 text-background"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {t(MODE_LABEL_KEY[m])}
          </button>
        ))}
      </div>

      <Button
        size="sm"
        variant="outline"
        disabled={running}
        onClick={() => setSettingsOpen(true)}
        title={t("pipeline.settings.title")}
        className="relative h-8 gap-1.5 rounded-[8px]"
      >
        <Settings2 className="size-3.5" />
        {t("pipeline.settings.button")}
        {customized ? (
          <span
            className="absolute -right-1 -top-1 size-2 rounded-full bg-primary"
            aria-hidden
          />
        ) : null}
      </Button>

      {running ? (
        <Button size="sm" variant="outline" onClick={stop}>
          <Square className="size-3.5" />
          {t("pipeline.stop")}
        </Button>
      ) : (
        <Button
          size="sm"
          onClick={run}
          disabled={noStory || busy}
          title={blockedReason}
        >
          <Play className="size-3.5" />
          {t(mode === "manual" ? "pipeline.stepOnce" : "pipeline.runAll")}
        </Button>
      )}

      {running && stageMessage ? (
        <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 className="size-3.5 animate-spin" />
          {stageMessage}
        </span>
      ) : null}

      <PipelineSettingsDialog
        project={project}
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        value={settings}
        onChange={setSettings}
        storyVersionAvailable={storyVersionAvailable}
        storyCharCount={storyCharCount}
      />

      <Dialog open={pending !== null} onOpenChange={(open) => !open && decide(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("pipeline.approvalTitle")}</DialogTitle>
            <DialogDescription>
              {pending?.message || pending?.stage || t("pipeline.approvalDesc")}
            </DialogDescription>
          </DialogHeader>
          <ul className="max-h-60 space-y-1 overflow-auto rounded-[6px] border border-border bg-muted/40 p-2">
            {(pending?.actions ?? []).map((action, idx) => (
              <li
                key={idx}
                className="font-mono text-xs text-foreground/80"
              >
                {actionLabel(action)}
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button size="sm" variant="outline" onClick={() => decide(false)}>
              {t("pipeline.approvalCancel")}
            </Button>
            <Button size="sm" onClick={() => decide(true)}>
              {t("pipeline.approvalApprove")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
