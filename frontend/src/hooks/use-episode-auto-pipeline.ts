// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 分集列表页自动化管线的运行态 hook：串行执行 + 进度 + 停止。
// 两条管线都必须拿得到故事版基础提示词：故事版是分镜脚本/分镜图的硬前置（缺它就不该
// 往下跑），拿不到提示词就整枚按钮禁用并给出原因，而不是静默跳过那一步。
import { useCallback, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { isTaskPollTimeoutError } from "@/api/tasks";
import { notifyTaskStillRunning } from "@/features/canvas/application/errorDialog";
import { backendErrorToastMessage } from "@/lib/api-errors";
import {
  AutoPipelineFailedError,
  AutoPipelineNeedsInputError,
  AutoPipelineStoppedError,
  runScriptPipeline,
  runStoryboardPipeline,
  type AutoEpisodeInput,
  type AutoPipelineKind,
  type AutoStep,
  type StoryboardTarget,
} from "@/lib/episode-auto-pipeline";
import { usePromptLibrary } from "@/lib/queries/prompt-library";

export interface AutoPipelineProgress {
  running: boolean;
  kind: AutoPipelineKind | null;
  /** 本轮「自动化生成分镜图」勾选的产物，供横幅算出完整步骤数。 */
  targets: StoryboardTarget[];
  episode: number | null;
  step: AutoStep | null;
}

const IDLE: AutoPipelineProgress = {
  running: false,
  kind: null,
  targets: [],
  episode: null,
  step: null,
};

/** 取出可展示的错误源：管线失败错误带 cause，优先用原始后端错误。 */
function rootCause(err: unknown): unknown {
  if (err instanceof AutoPipelineFailedError && err.cause != null) return err.cause;
  return err;
}

export function useEpisodeAutoPipeline(project: string) {
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const { data: promptRes } = usePromptLibrary();
  const [progress, setProgress] = useState<AutoPipelineProgress>(IDLE);
  const stopRef = useRef(false);

  const storyVersionBasePrompt = useMemo(() => {
    const items = promptRes?.data?.items ?? [];
    const found = items.find(
      (item) => item.key === "freezone.story_version.system",
    );
    return (found?.value || found?.default || "").trim();
  }, [promptRes]);

  const run = useCallback(
    async (
      kind: AutoPipelineKind,
      episodes: AutoEpisodeInput[],
      targets: StoryboardTarget[] = [],
    ) => {
      if (episodes.length === 0) return;
      stopRef.current = false;
      setProgress({ running: true, kind, targets, episode: null, step: null });
      const callbacks = {
        onEpisodeStart: (episode: number) =>
          setProgress({ running: true, kind, targets, episode, step: null }),
        onStep: (episode: number, step: AutoStep) =>
          setProgress({ running: true, kind, targets, episode, step }),
        isStopped: () => stopRef.current,
      };
      try {
        if (kind === "script") {
          await runScriptPipeline({
            project,
            episodes,
            queryClient,
            callbacks,
            storyVersionBasePrompt,
            storyVersionCategoryLabel: t("canvas.assetLibrary.category.storyVersion"),
          });
        } else {
          await runStoryboardPipeline({
            project,
            episodes,
            queryClient,
            callbacks,
            storyVersionBasePrompt,
            storyVersionCategoryLabel: t("canvas.assetLibrary.category.storyVersion"),
            targets,
          });
        }
        toast.success(
          kind === "script"
            ? t("episode.list.autoScriptDone", { count: episodes.length })
            : t("episode.list.autoStoryboardDone", { count: episodes.length }),
        );
      } catch (err) {
        if (err instanceof AutoPipelineStoppedError) {
          toast.info(t("episode.list.autoStopped"));
        } else if (err instanceof AutoPipelineNeedsInputError) {
          // 计划器判定缺前置输入（如项目还没有故事源）：整轮没发射任何动作，
          // 不能报成功；把服务端的原话作为描述带出，告诉用户该去补什么。
          toast.warning(t("episode.list.autoNeedsInput", { episode: err.episode }), {
            description: err.message,
          });
        } else if (isTaskPollTimeoutError(rootCause(err))) {
          // 失联 ≠ 失败：后端可能仍在跑，提示稍后查看即可。
          notifyTaskStillRunning(t);
        } else if (err instanceof AutoPipelineFailedError) {
          toast.error(
            t("episode.list.autoFailedEpisode", { episode: err.episode }),
            { description: backendErrorToastMessage(rootCause(err), t) },
          );
        } else {
          toast.error(backendErrorToastMessage(err, t));
        }
      } finally {
        setProgress(IDLE);
      }
    },
    [project, queryClient, storyVersionBasePrompt, t],
  );

  const runScript = useCallback(
    (episodes: AutoEpisodeInput[]) => run("script", episodes),
    [run],
  );
  const runStoryboard = useCallback(
    (episodes: AutoEpisodeInput[], targets: StoryboardTarget[]) =>
      run("storyboard", episodes, targets),
    [run],
  );
  const stop = useCallback(() => {
    stopRef.current = true;
  }, []);

  return {
    ...progress,
    // 故事版是两条自动化管线共用的硬前置：没提示词就都不让发（避免“跑完了但前提没做”）。
    canRunScript: Boolean(storyVersionBasePrompt),
    canRunStoryboard: Boolean(storyVersionBasePrompt),
    runScript,
    runStoryboard,
    stop,
  };
}
