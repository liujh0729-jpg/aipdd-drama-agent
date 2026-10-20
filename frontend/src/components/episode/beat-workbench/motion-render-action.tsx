// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Clapperboard, Loader2 } from "lucide-react";
import { toast } from "sonner";

import {
  fetchFreezoneJobResult,
  submitFreezoneMotionRender,
  type FreezoneMotionRenderPayload,
} from "@/api/ops";
import { awaitTaskCompletion } from "@/api/tasks";
import { MotionGalleryModal } from "@/features/canvas/ui/MotionGalleryModal";
import {
  useFreezoneMotionCases,
  useFreezoneMotionStyles,
} from "@/features/canvas/hooks/useFreezoneMotionCases";
import { backendErrorToastMessage } from "@/lib/api-errors";
import type { Orientation } from "@/lib/aspect-ratio";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { MEDIA_PRIMARY_ACTION_BUTTON_CLASS } from "./media-styles";
import type { Beat } from "@/types/episode";

interface MotionRenderActionProps {
  beat: Beat;
  project: string;
  /** 集号：与镜号一起写进渲染 sidecar，导出剪映草稿时靠它们把成片归位。 */
  episode: number;
  /** 项目画幅方向（与分镜视频同一份来源：aspect-ratio store）。 */
  orientation: Orientation;
  /** 宿主所在动作行的按钮样式，保证逐镜按钮与同排按钮同款。 */
  buttonClassName?: string;
}

/**
 * 图册里可发起渲染的出处：案例给分镜结构，画风给画面观感。
 * 后端把两者当互斥项（同时给直接 400），所以这里必须带 kind，而不是只带一个 id。
 */
export interface MotionRenderSource {
  id: string;
  label: string;
  kind: "case" | "style";
}

export interface MotionCaseRenderInput {
  /** 本镜头画面描述/节点提示词：只在后端与案例规格一起拼成写页指令。 */
  subject: string;
  aspectRatio: string;
  resolution?: FreezoneMotionRenderPayload["resolution"];
  fps?: number;
  durationS?: number | null;
  episode?: number;
  beatNumber?: number;
  /** 画布发起时带上节点/画布 id：成片由后端写回这个节点的生成历史。 */
  nodeId?: string;
  canvasId?: string;
  archiveToLibrary?: boolean;
}

/**
 * 「按这条案例/画风渲染」的唯一提交链路：分镜逐镜与画布节点面板共用这一份
 * 提交 → 等待 → 打开成片。工具页要的是面板内嵌的进度条，不弹这套 toast，但它调的
 * 也是同一个 `submitFreezoneMotionRender`，提交口径没有第二份实现。
 * 提示词不在前端拼：图册规格 + 渲染契约
 * 的拼法是后端一处定的，任务里按 case_id 或 style_id 重新取，三处（复制/提示词库/
 * 渲染）才会逐字一致。
 */
export async function renderMotionSource(
  project: string,
  source: MotionRenderSource,
  input: MotionCaseRenderInput,
  t: TFunction,
): Promise<void> {
  let toastId: string | number = "";
  try {
    const ref = await submitFreezoneMotionRender(project, {
      // 二选一：另一栏必须留空，否则后端当场拒掉这一单。
      caseId: source.kind === "case" ? source.id : "",
      styleId: source.kind === "style" ? source.id : "",
      subject: input.subject,
      title: source.label,
      aspectRatio: input.aspectRatio,
      resolution: input.resolution ?? "720p",
      fps: input.fps ?? 30,
      durationS: input.durationS ?? null,
      episode: input.episode,
      beatNumber: input.beatNumber,
      nodeId: input.nodeId,
      canvasId: input.canvasId,
      archiveToLibrary: input.archiveToLibrary,
    });
    toastId = toast.info(t("episode.beat.motion.running", { label: source.label }));
    const completed = await awaitTaskCompletion(ref.task_key, project, {
      taskType: ref.task_type,
    });
    if (completed.status === "failed") {
      toast.error(
        completed.error || t("episode.beat.motion.failed", { label: source.label }),
        { id: toastId },
      );
      return;
    }
    const directUrl = completed.result?.["output_url"] as string | undefined;
    const url =
      directUrl ||
      (await fetchFreezoneJobResult(project, ref.task_type, ref.job_id)).url;
    toast.success(t("episode.beat.motion.done", { label: source.label }), {
      id: toastId,
      duration: 12_000,
      action: {
        label: t("episode.beat.motion.open"),
        onClick: () => window.open(url, "_blank", "noopener,noreferrer"),
      },
    });
  } catch (error) {
    // 路由的提交前闸门（本机缺 Chrome/ffmpeg）是 409，detail 原文就是给用户
    // 的出路说明；`backendErrorToastMessage` 对普通 Error 会原样带回 message。
    toast.error(backendErrorToastMessage(error, t), { id: toastId });
  }
}

/**
 * [aipdd fork delta] 分镜工作台逐镜「生成 MG 大片」。
 *
 * 复用画布那一份图册（`MotionGalleryModal`）：选一条案例 = 选定那份分镜规格，选一条
 * 画风 = 选定那种观感，本镜头的画面描述都作为主体内容一起交给任务。任务在后端先按
 * 渲染契约让文本模型写一页 MG，再用随包 Chrome/ffmpeg 逐帧渲成 MP4，成片同时登记进
 * 资产库「待分类资产」——这样它不是渲完就找不到的孤儿文件，而是下一步能直接取用的素材。
 */
export function MotionRenderAction({
  beat,
  project,
  episode,
  orientation,
  buttonClassName,
}: MotionRenderActionProps) {
  const { t } = useTranslation();
  const catalog = useFreezoneMotionCases(project);
  const styleCatalog = useFreezoneMotionStyles(project);
  const [isOpen, setIsOpen] = useState(false);
  const [isRendering, setIsRendering] = useState(false);

  const subject = (beat.visual_description || beat.narration_segment || "").trim();

  const handleApply = async (_prompt: string, source: MotionRenderSource) => {
    // 提示词不在前端拼：图册规格 + 渲染契约的拼法是后端一处定的，任务里按 case_id
    // 或 style_id 重新取，三处（复制/提示词库/渲染）才会逐字一致。
    setIsRendering(true);
    try {
      await renderMotionSource(
        project,
        source,
        {
          subject,
          aspectRatio: orientation === "portrait" ? "9:16" : "16:9",
          resolution: "720p",
          fps: 30,
          durationS:
            typeof beat.estimated_duration === "number" && beat.estimated_duration > 0
              ? Math.round(beat.estimated_duration)
              : null,
          episode,
          beatNumber: beat.beat_number,
          archiveToLibrary: true,
        },
        t,
      );
    } finally {
      setIsRendering(false);
    }
  };

  return (
    <>
      <Button
        size="xs"
        variant="outline"
        disabled={isRendering}
        onClick={() => setIsOpen(true)}
        className={cn(MEDIA_PRIMARY_ACTION_BUTTON_CLASS, buttonClassName)}
      >
        {isRendering ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <Clapperboard className="size-3" />
        )}
        {t("episode.beat.motion.render")}
      </Button>

      {isOpen ? (
        <MotionGalleryModal
          applyLabelKey="episode.beat.motion.apply"
          assetBase={catalog.assetBase}
          cases={catalog.cases}
          categories={catalog.categories}
          error={catalog.error}
          isLoading={catalog.isLoading}
          methods={catalog.methods}
          onApply={handleApply}
          onClose={() => setIsOpen(false)}
          onRetry={catalog.retry}
          project={project}
          renderContract={catalog.renderContract}
          styles={styleCatalog.styles}
          styleCategories={styleCatalog.categories}
          styleError={styleCatalog.error}
          styleLoading={styleCatalog.isLoading}
          onStyleRetry={styleCatalog.retry}
        />
      ) : null}
    </>
  );
}
