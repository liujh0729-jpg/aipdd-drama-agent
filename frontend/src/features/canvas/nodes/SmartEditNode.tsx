// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 智能剪辑节点：读上游素材 → 识别内容类型（视频/音频/图片）→
// 列举该类型对应的工具技能 → 选技能、调参数、运行 → 结果自动 spawn 为下游节点。
// 全部复用既有 freezone 提交/结果函数，不新增后端 task_type。
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from "@xyflow/react";
import {
  Clapperboard,
  Eraser,
  Mic,
  ScrollText,
  Scissors,
  Split,
  Sparkles,
  Type,
  Wand2,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  ensureBackendImageUrl,
  ensureBackendImageUrls,
  fetchFreezoneAudioSeparateResult,
  fetchFreezoneImageModels,
  fetchFreezoneMediaCopywriteModels,
  fetchFreezoneReversePromptResult,
  fetchFreezoneTextModels,
  fetchFreezoneVideoModels,
  fetchFreezoneVisionModels,
  submitFreezoneAudioSeparate,
  submitFreezoneEdit,
  submitFreezoneMediaCopywrite,
  submitFreezoneReversePrompt,
  submitFreezoneUpscale,
  submitFreezoneVideoClone,
  submitFreezoneVideoOmniGen,
  submitFreezoneVideoScriptReverse,
  submitFreezoneVideoUpscale,
  type FreezoneImageModelInfo,
  type FreezoneTextModelInfo,
  type FreezoneVideoModelInfo,
} from "@/api/ops";
import { awaitTaskCompletion, isTaskPollTimeoutError } from "@/api/tasks";
import { useCanvasStore } from "@/stores/canvasStore";
import { useUpstreamNodes } from "@/features/canvas/application/useUpstreamGraph";
import { notifyTaskStillRunning } from "@/features/canvas/application/errorDialog";
import {
  CANVAS_NODE_TYPES,
  type CanvasNodeData,
  type SmartEditNodeData,
} from "@/features/canvas/domain/canvasNodes";
import {
  collectUpstreamMediaByKind,
  detectInputKind,
  resolveSkillSource,
  skillsForKind,
  type SmartEditSkill,
} from "@/features/canvas/domain/smartEditSkills";
import { localizeNodeDisplayName } from "@/features/canvas/domain/nodeDisplay";
import {
  NodeHeader,
  NODE_HEADER_FLOATING_POSITION_CLASS,
} from "@/features/canvas/ui/NodeHeader";
import {
  CANVAS_NODE_INPUT_SURFACE_CLASS,
  canvasNodeFrameClass,
} from "@/features/canvas/ui/nodeFrameStyles";
import { readUrl } from "@/lib/url-params";
import { firstMediaUrlInResult } from "@/features/tools/core";
import { isVideoModeSupportedByModel } from "@/features/canvas/nodes/shared/videoModelCapabilities";

type SmartEditNodeProps = NodeProps & {
  id: string;
  data: SmartEditNodeData;
  selected?: boolean;
};

const NODE_WIDTH = 300;

// 技能图标 key → lucide 组件。
const SKILL_ICON_MAP: Record<string, LucideIcon> = {
  mic: Mic,
  scroll: ScrollText,
  split: Split,
  clarity: Sparkles,
  text: Type,
  eraser: Eraser,
  wand: Wand2,
  clapper: Clapperboard,
};

interface ModelOptionLite {
  id: string;
  label: string;
  ready?: boolean;
}

// 从任务 result 尽力取一条指定扩展名的 servable URL（放大类技能产物）。
function pickMediaUrl(result: unknown, ext: RegExp): string | null {
  const url = firstMediaUrlInResult(result);
  if (url && ext.test(url)) return url;
  // firstMediaUrlInResult 返回的是第一条媒体；若类型不符再递归找匹配扩展名的。
  const seen = new Set<unknown>();
  const walk = (node: unknown): string | null => {
    if (node == null || seen.has(node)) return null;
    if (typeof node === "string") {
      return /^(https?:)?\/\/|^\/static\//.test(node) && ext.test(node) ? node : null;
    }
    if (typeof node !== "object") return null;
    seen.add(node);
    if (Array.isArray(node)) {
      for (const item of node) {
        const found = walk(item);
        if (found) return found;
      }
      return null;
    }
    for (const value of Object.values(node as Record<string, unknown>)) {
      const found = walk(value);
      if (found) return found;
    }
    return null;
  };
  return walk(result);
}

// 音视频分离结果分类（沿用 NodeActionToolbar 的判据）：优先 *_url 字段，
// 绝不从 *_path 反构旧版静态地址。
function classifySeparate(
  result: unknown,
): { audio: string | null; video: string | null } {
  const source =
    result && typeof result === "object" && !Array.isArray(result)
      ? (result as Record<string, unknown>)
      : null;
  const pick = (keys: string[]): string | null => {
    if (!source) return null;
    for (const key of keys) {
      const value = source[key];
      if (typeof value === "string" && value.length > 0) return value;
    }
    return null;
  };
  let audio = pick(["audio_url", "audioUrl", "vocal_url", "vocalUrl"]);
  let video = pick(["mute_video_url", "muteVideoUrl", "silent_video_url"]);
  if (!audio || !video) {
    const audioUrl = pickMediaUrl(result, /\.(mp3|wav|m4a|aac|ogg|flac)(\?|$)/i);
    const videoUrl = pickMediaUrl(result, /\.(mp4|mov|webm|mkv)(\?|$)/i);
    audio = audio ?? audioUrl;
    video = video ?? videoUrl;
  }
  return { audio, video };
}

export const SmartEditNode = memo(
  ({ id, data, selected }: SmartEditNodeProps) => {
    const { t } = useTranslation();
    const updateNodeInternals = useUpdateNodeInternals();
    const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
    const updateNodeData = useCanvasStore((state) => state.updateNodeData);
    const upstreamNodes = useUpstreamNodes(id);

    const kind = useMemo(() => detectInputKind(upstreamNodes), [upstreamNodes]);
    const availableSkills = useMemo(
      () => (kind ? skillsForKind(kind) : []),
      [kind],
    );

    // activeSkill 必须仍属于当前内容类型，否则视为未选（素材换类型时兜正）。
    const activeSkill = useMemo<SmartEditSkill | null>(() => {
      if (!data.activeSkillId) return null;
      return availableSkills.find((s) => s.id === data.activeSkillId) ?? null;
    }, [data.activeSkillId, availableSkills]);

    const isProcessing = Boolean(data.isProcessing);

    // 模型候选：挂载时或首次需要时各拉一次，遵循空兜底。
    const [asrModels, setAsrModels] = useState<ModelOptionLite[] | null>(null);
    const [visionModels, setVisionModels] = useState<ModelOptionLite[] | null>(
      null,
    );
    // [aipdd fork delta] 去水印/多输入生成技能的生成类模型目录（image/video/text）。
    const [imageModels, setImageModels] = useState<
      FreezoneImageModelInfo[] | null
    >(null);
    const [videoModels, setVideoModels] = useState<
      FreezoneVideoModelInfo[] | null
    >(null);
    const [textModels, setTextModels] = useState<FreezoneTextModelInfo[] | null>(
      null,
    );
    const project = readUrl().project;
    const canvasId = readUrl().canvas ?? "default";

    const needAsr = activeSkill?.params.some(
      (p) => p.type === "modelSelect" && p.catalog === "asr",
    );
    const needVision = activeSkill?.params.some(
      (p) => p.type === "modelSelect" && p.catalog === "vision",
    );
    const needImage = activeSkill?.params.some(
      (p) => p.type === "modelSelect" && p.catalog === "image",
    );
    const needVideo = activeSkill?.params.some(
      (p) => p.type === "modelSelect" && p.catalog === "video",
    );
    const needText = activeSkill?.params.some(
      (p) => p.type === "modelSelect" && p.catalog === "text",
    );

    useEffect(() => {
      updateNodeInternals(id);
    }, [id, updateNodeInternals]);

    useEffect(() => {
      if (needAsr && asrModels === null && project) {
        fetchFreezoneMediaCopywriteModels(project)
          .then((list) =>
            setAsrModels(
              list.map((m) => ({ id: m.id, label: m.label, ready: m.ready })),
            ),
          )
          .catch(() => setAsrModels([]));
      }
    }, [needAsr, asrModels, project]);

    useEffect(() => {
      if (needVision && visionModels === null) {
        fetchFreezoneVisionModels()
          .then((list) =>
            setVisionModels(list.map((m) => ({ id: m.id, label: m.label }))),
          )
          .catch(() => setVisionModels([]));
      }
    }, [needVision, visionModels]);

    useEffect(() => {
      if (needImage && imageModels === null && project) {
        fetchFreezoneImageModels(project)
          .then(setImageModels)
          .catch(() => setImageModels([]));
      }
    }, [needImage, imageModels, project]);

    useEffect(() => {
      if (needVideo && videoModels === null && project) {
        fetchFreezoneVideoModels(project)
          .then(setVideoModels)
          .catch(() => setVideoModels([]));
      }
    }, [needVideo, videoModels, project]);

    useEffect(() => {
      if (needText && textModels === null) {
        fetchFreezoneTextModels()
          .then(setTextModels)
          .catch(() => setTextModels([]));
      }
    }, [needText, textModels]);

    const setParam = useCallback(
      (key: string, value: string) => {
        const skillId = activeSkill?.id;
        if (!skillId) return;
        const next = {
          ...(data.skillParams ?? {}),
          [skillId]: { ...(data.skillParams?.[skillId] ?? {}), [key]: value },
        };
        updateNodeData(id, { skillParams: next });
      },
      [activeSkill, data.skillParams, id, updateNodeData],
    );

    const paramValue = useCallback(
      (key: string, fallback = ""): string => {
        const stored = activeSkill
          ? data.skillParams?.[activeSkill.id]?.[key]
          : undefined;
        return typeof stored === "string" && stored.length > 0 ? stored : fallback;
      },
      [activeSkill, data.skillParams],
    );

    const addResultNode = useCallback(
      (type: CanvasNodeTypeLike, seed: Partial<CanvasNodeData>) => {
        const store = useCanvasStore.getState();
        const position = store.findNodePosition(id, 480, 300);
        const newId = store.addNode(type, position, seed as Partial<CanvasNodeData>);
        store.addEdge(id, newId);
        store.setSelectedNode(newId);
        store.requestFocusNode(newId);
      },
      [id],
    );

    type CanvasNodeTypeLike = (typeof CANVAS_NODE_TYPES)[keyof typeof CANVAS_NODE_TYPES];

    const handleRun = useCallback(async () => {
      if (!kind || !activeSkill || isProcessing || !project) return;
      const rawSource = resolveSkillSource(kind, upstreamNodes);
      if (!rawSource) {
        toast.error(t("smartEdit.actions.noSource"));
        return;
      }
      const params = data.skillParams?.[activeSkill.id] ?? {};
      updateNodeData(id, { isProcessing: true, processingStartedAt: Date.now(), lastError: null });

      const finish = (payload: Record<string, unknown>) =>
        updateNodeData(id, {
          isProcessing: false,
          processingStartedAt: null,
          ...payload,
        });

      try {
        const sourceUrl = await ensureBackendImageUrl(project, rawSource);
        const skillId = activeSkill.id;

        // [aipdd fork delta] 多输入技能：先校验必需的第二路上游素材是否已连入，
        // 缺失直接提示并中止（否则会把空 URL 塞进 references）。
        const collected = collectUpstreamMediaByKind(upstreamNodes);
        for (const extra of activeSkill.extraInputs ?? []) {
          if (extra.required && collected[extra.kind].length === 0) {
            const hint = t(extra.hintKey);
            finish({ lastError: hint });
            toast.error(hint);
            return;
          }
        }

        if (skillId === "extract-copy" || skillId === "audio-extract") {
          const model =
            (params.model as string) || asrModels?.[0]?.id || undefined;
          const ref = await submitFreezoneMediaCopywrite(project, {
            sourceType: skillId === "extract-copy" ? "upload_video" : "upload_audio",
            url: sourceUrl,
            model,
          });
          const completed = await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          const text = readResultText(completed.result);
          if (!text) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.textAnnotation, {
            content: text,
            displayName: t("smartEdit.resultNames.copy"),
          });
          finish({});
          return;
        }

        if (skillId === "reverse-script") {
          const ref = await submitFreezoneVideoScriptReverse(project, {
            videoUrl: sourceUrl,
            model: (params.model as string) || undefined,
            prompt: (params.prompt as string) || undefined,
          });
          const completed = await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          const text = readResultText(completed.result);
          if (!text) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.textAnnotation, {
            content: text,
            displayName: t("smartEdit.resultNames.script"),
          });
          finish({});
          return;
        }

        if (skillId === "reverse-prompt") {
          const ref = await submitFreezoneReversePrompt(project, {
            sourceUrl,
            instruction: (params.instruction as string) || "",
            canvasId,
            nodeId: id,
          });
          await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          const { prompt } = await fetchFreezoneReversePromptResult(
            project,
            ref.job_id,
          );
          if (!prompt || !prompt.trim()) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.textAnnotation, {
            content: prompt,
            displayName: t("smartEdit.resultNames.prompt"),
          });
          finish({});
          return;
        }

        if (skillId === "audio-separate") {
          const ref = await submitFreezoneAudioSeparate(project, {
            sourceUrl,
          });
          const completed = await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          let { audio, video } = classifySeparate(completed.result);
          if (!audio || !video) {
            const jobResult = await fetchFreezoneAudioSeparateResult(
              project,
              ref.job_id,
            );
            const extra = classifySeparate(jobResult);
            audio = audio ?? extra.audio;
            video = video ?? extra.video;
          }
          if (!audio || !video) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.audio, {
            audioUrl: audio,
            displayName: t("smartEdit.resultNames.backgroundAudio"),
            sourceFileName: t("smartEdit.resultNames.backgroundAudio"),
          });
          addResultNode(CANVAS_NODE_TYPES.video, {
            videoUrl: video,
            displayName: t("smartEdit.resultNames.silentVideo"),
            sourceFileName: `${t("smartEdit.resultNames.silentVideo")}.mp4`,
          });
          finish({});
          return;
        }

        if (skillId === "video-upscale") {
          const ref = await submitFreezoneVideoUpscale(project, {
            sourceUrl,
            resolution:
              ((params.resolution as string) || "1080p") as "1080p" | "2k" | "4k",
            denoiseStrength:
              ((params.denoise as string) || "1x") as "none" | "1x" | "2x",
            canvasId,
            nodeId: id,
          });
          const completed = await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          const url = pickMediaUrl(
            completed.result,
            /\.(mp4|mov|webm|mkv)(\?|$)/i,
          );
          if (!url) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.video, {
            videoUrl: url,
            displayName: t("smartEdit.resultNames.upscaledVideo"),
          });
          finish({});
          return;
        }

        if (skillId === "image-upscale") {
          const scale = Number(params.scaleFactor) || 2;
          const ref = await submitFreezoneUpscale(project, {
            sourceUrl,
            scaleFactor: (scale === 4 || scale === 6 ? scale : 2) as 2 | 4 | 6,
          });
          const completed = await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          const url = pickMediaUrl(
            completed.result,
            /\.(png|jpe?g|webp|gif)(\?|$)/i,
          );
          if (!url) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.exportImage, {
            imageUrl: url,
            previewImageUrl: null,
            aspectRatio: "1:1",
            resultKind: "upscale",
            displayName: t("smartEdit.resultNames.upscaleImage"),
          });
          finish({});
          return;
        }

        if (skillId === "image-watermark") {
          const pickedId =
            (params.model as string) || imageModels?.[0]?.id || "";
          const picked = imageModels?.find((m) => m.id === pickedId) ?? null;
          const ref = await submitFreezoneEdit(project, {
            baseUrl: sourceUrl,
            prompt:
              ((params.prompt as string) || "").trim() ||
              t("smartEdit.skills.imageWatermark.defaultPrompt"),
            aspectRatio: "original",
            model: picked?.apiModel ?? null,
            modelId: picked?.catalogId ?? picked?.id ?? null,
            canvasId,
            nodeId: id,
          });
          const completed = await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          const url = pickMediaUrl(
            completed.result,
            /\.(png|jpe?g|webp|gif)(\?|$)/i,
          );
          if (!url) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.exportImage, {
            imageUrl: url,
            previewImageUrl: null,
            aspectRatio: "original",
            resultKind: "generic",
            displayName: t("smartEdit.resultNames.watermarkImage"),
          });
          finish({});
          return;
        }

        if (skillId === "lip-sync") {
          const driverUrl = await ensureBackendImageUrl(
            project,
            collected.audio[0],
          );
          const omniFirst = (videoModels ?? []).find((m) =>
            isVideoModeSupportedByModel("allReference", m),
          );
          const pickedVid = (params.model as string) || omniFirst?.id || "";
          const vidModel =
            videoModels?.find((m) => m.id === pickedVid) ?? null;
          const ref = await submitFreezoneVideoOmniGen(project, {
            prompt: "",
            references: [
              { type: "video", url: sourceUrl },
              { type: "audio", url: driverUrl },
            ],
            model:
              vidModel?.catalogId ?? vidModel?.id ?? (pickedVid || undefined),
            genMode: "allReference",
            // [aipdd fork delta req42] 把技能 id 交出去：后端据此把「保留源视频、只换
            // 人物 / 产品 / 对齐口型」写成明确指令，而不是让模型猜。
            sourceTool: skillId,
            canvasId,
            nodeId: id,
          });
          const completed = await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          const url = pickMediaUrl(
            completed.result,
            /\.(mp4|mov|webm|mkv)(\?|$)/i,
          );
          if (!url) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.video, {
            videoUrl: url,
            durationMs: null,
            displayName: t("smartEdit.resultNames.lipSyncVideo"),
            sourceFileName: `${t("smartEdit.resultNames.lipSyncVideo")}.mp4`,
          });
          finish({});
          return;
        }

        if (skillId === "replace-person" || skillId === "replace-product") {
          const refImageUrl = await ensureBackendImageUrl(
            project,
            collected.image[0],
          );
          const omniFirst = (videoModels ?? []).find((m) =>
            isVideoModeSupportedByModel("allReference", m),
          );
          const pickedVid = (params.model as string) || omniFirst?.id || "";
          const vidModel =
            videoModels?.find((m) => m.id === pickedVid) ?? null;
          const ref = await submitFreezoneVideoOmniGen(project, {
            prompt: "",
            references: [
              { type: "video", url: sourceUrl },
              { type: "image", url: refImageUrl },
            ],
            model:
              vidModel?.catalogId ?? vidModel?.id ?? (pickedVid || undefined),
            genMode: "allReference",
            // [aipdd fork delta req42] 把技能 id 交出去：后端据此把「保留源视频、只换
            // 人物 / 产品 / 对齐口型」写成明确指令，而不是让模型猜。
            sourceTool: skillId,
            canvasId,
            nodeId: id,
          });
          const completed = await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          const url = pickMediaUrl(
            completed.result,
            /\.(mp4|mov|webm|mkv)(\?|$)/i,
          );
          if (!url) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.video, {
            videoUrl: url,
            durationMs: null,
            displayName: t(
              skillId === "replace-person"
                ? "smartEdit.resultNames.replacePersonVideo"
                : "smartEdit.resultNames.replaceProductVideo",
            ),
          });
          finish({});
          return;
        }

        if (skillId === "video-clone") {
          const refUrls = await ensureBackendImageUrls(
            project,
            collected.image.slice(0, 4),
          );
          const pickedVid =
            (params.model as string) || videoModels?.[0]?.id || "";
          const vidModel =
            videoModels?.find((m) => m.id === pickedVid) ?? null;
          const ref = await submitFreezoneVideoClone(project, {
            videoUrl: sourceUrl,
            prompt: ((params.prompt as string) || "").trim(),
            ...(refUrls.length ? { referenceUrls: refUrls } : {}),
            ...(vidModel ? { model: vidModel.catalogId ?? vidModel.id } : {}),
            ...(params.modelText
              ? { modelText: params.modelText as string }
              : {}),
            referenceMode:
              ((params.referenceMode as "frames" | "text") || "frames"),
            audioMode:
              ((params.audioMode as "generate" | "mute" | "source") ||
                "generate"),
          });
          const completed = await awaitTaskCompletion(ref.task_key, project, {
            taskType: ref.task_type,
          });
          const url = pickMediaUrl(
            completed.result,
            /\.(mp4|mov|webm|mkv)(\?|$)/i,
          );
          if (!url) {
            finish({ lastError: t("smartEdit.actions.emptyResult") });
            toast.error(t("smartEdit.actions.emptyResult"));
            return;
          }
          addResultNode(CANVAS_NODE_TYPES.video, {
            videoUrl: url,
            durationMs: null,
            displayName: t("smartEdit.resultNames.clonedVideo"),
          });
          finish({});
          return;
        }

        finish({});
      } catch (error) {
        if (isTaskPollTimeoutError(error)) {
          notifyTaskStillRunning(t);
          updateNodeData(id, { isProcessing: false, processingStartedAt: null });
          return;
        }
        console.error("[smart-edit] run failed", error);
        const message =
          error instanceof Error ? error.message : t("smartEdit.actions.error");
        updateNodeData(id, {
          isProcessing: false,
          processingStartedAt: null,
          lastError: message,
        });
        toast.error(message);
      }
    }, [
      kind,
      activeSkill,
      isProcessing,
      project,
      upstreamNodes,
      data.skillParams,
      asrModels,
      imageModels,
      videoModels,
      textModels,
      id,
      updateNodeData,
      canvasId,
      addResultNode,
      t,
    ]);

    const resolvedTitle = useMemo(
      () => localizeNodeDisplayName(CANVAS_NODE_TYPES.smartEdit, data, t),
      [data, t],
    );
    const cardToneClass = canvasNodeFrameClass({ selected });

    return (
      <div
        className="group relative"
        style={{ width: NODE_WIDTH }}
        onClick={() => setSelectedNode(id)}
      >
        <Handle
          type="target"
          position={Position.Left}
          id="target"
          className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
        />
        <Handle
          type="source"
          position={Position.Right}
          id="source"
          className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]"
        />

        <NodeHeader
          className={NODE_HEADER_FLOATING_POSITION_CLASS}
          icon={<Scissors className="h-4 w-4" />}
          titleText={resolvedTitle}
          editable
          onTitleChange={(next) => updateNodeData(id, { displayName: next })}
        />

        <div
          className={`relative flex w-full flex-col overflow-hidden rounded-[var(--node-radius)] border ${CANVAS_NODE_INPUT_SURFACE_CLASS} transition-colors ${cardToneClass}`}
        >
          <div className="flex flex-col gap-3 p-4">
            {!kind ? (
              <div className="flex h-16 items-center justify-center text-center text-[12px] text-text-muted/90">
                {t("smartEdit.noInput")}
              </div>
            ) : (
              <>
                <span className="inline-flex w-fit items-center rounded-full border border-white/15 bg-white/[0.06] px-2.5 py-0.5 text-[11px] text-text-dark">
                  {t(`smartEdit.input.${kind}`)}
                </span>

                <div className="flex flex-col gap-1.5">
                  {availableSkills.map((skill) => {
                    const Icon = SKILL_ICON_MAP[skill.icon] ?? Sparkles;
                    const isActive = activeSkill?.id === skill.id;
                    return (
                      <button
                        key={skill.id}
                        type="button"
                        disabled={isProcessing}
                        onClick={(event) => {
                          event.stopPropagation();
                          updateNodeData(id, {
                            activeSkillId: isActive ? null : skill.id,
                          });
                        }}
                        className={`flex items-center gap-2.5 rounded-[10px] border px-3 py-2 text-left text-[12.5px] transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
                          isActive
                            ? "border-white/25 bg-white/[0.1] text-text-dark"
                            : "border-white/12 bg-white/[0.03] text-text-dark/90 hover:bg-white/[0.07]"
                        }`}
                      >
                        <Icon className="h-4 w-4 shrink-0 opacity-80" />
                        <span className="min-w-0 flex-1 truncate">
                          {t(skill.titleKey)}
                        </span>
                      </button>
                    );
                  })}
                </div>

                {activeSkill && (
                  <div className="flex flex-col gap-2.5 border-t border-white/10 pt-3">
                    <p className="text-[11px] leading-relaxed text-text-muted/90">
                      {t(activeSkill.descKey)}
                    </p>

                    {activeSkill.params.map((param) => {
                      if (param.type === "modelSelect") {
                        let options: { id: string; label: string }[] | null =
                          null;
                        if (param.catalog === "asr") options = asrModels;
                        else if (param.catalog === "vision")
                          options = visionModels;
                        else if (param.catalog === "image")
                          options =
                            imageModels?.map((m) => ({
                              id: m.id,
                              label: m.label,
                            })) ?? null;
                        else if (param.catalog === "text")
                          options =
                            textModels?.map((m) => ({
                              id: m.id,
                              label: m.label,
                            })) ?? null;
                        else if (param.catalog === "video") {
                          const omniFiltered =
                            videoModels && param.filter === "omni"
                              ? videoModels.filter((m) =>
                                  isVideoModeSupportedByModel(
                                    "allReference",
                                    m,
                                  ),
                                )
                              : videoModels;
                          options =
                            omniFiltered?.map((m) => ({
                              id: m.id,
                              label: m.label,
                            })) ?? null;
                        }
                        const modelLabelKey =
                          param.catalog === "asr"
                            ? "smartEdit.params.model"
                            : param.catalog === "vision"
                              ? "smartEdit.params.visionModel"
                              : param.catalog === "image"
                                ? "smartEdit.params.imageModel"
                                : param.catalog === "video"
                                  ? "smartEdit.params.videoModel"
                                  : "smartEdit.params.textModel";
                        const current = paramValue(
                          param.key,
                          !param.optional && options?.length
                            ? options[0].id
                            : "",
                        );
                        return (
                          <label
                            key={param.key}
                            className="flex flex-col gap-1 text-[11px] text-text-muted/90"
                          >
                            <span>{t(modelLabelKey)}</span>
                            <select
                              value={current}
                              disabled={isProcessing}
                              onChange={(event) =>
                                setParam(param.key, event.target.value)
                              }
                              className="rounded-[8px] border border-white/15 bg-white/[0.04] px-2 py-1.5 text-[12px] text-text-dark outline-none disabled:opacity-50"
                            >
                              {options === null ? (
                                <option value="">
                                  {t("common.loading", { defaultValue: "加载中…" })}
                                </option>
                              ) : options.length === 0 ? (
                                <option value="">
                                  {t("modelParams.noModelsAvailable", {
                                    defaultValue: "暂无可用模型",
                                  })}
                                </option>
                              ) : (
                                <>
                                  {param.optional && (
                                    <option value="">
                                      {t("smartEdit.params.backendDefault")}
                                    </option>
                                  )}
                                  {options.map((opt) => (
                                    <option key={opt.id} value={opt.id}>
                                      {opt.label}
                                    </option>
                                  ))}
                                </>
                              )}
                            </select>
                          </label>
                        );
                      }
                      if (param.type === "select") {
                        const current = paramValue(param.key, param.default);
                        return (
                          <label
                            key={param.key}
                            className="flex flex-col gap-1 text-[11px] text-text-muted/90"
                          >
                            <span>
                              {param.key === "resolution"
                                ? t("smartEdit.params.resolution")
                                : param.key === "denoise"
                                  ? t("smartEdit.params.denoise")
                                  : param.key === "referenceMode"
                                    ? t("smartEdit.params.referenceMode")
                                    : param.key === "audioMode"
                                      ? t("smartEdit.params.audioMode")
                                      : t("smartEdit.params.scale")}
                            </span>
                            <select
                              value={current}
                              disabled={isProcessing}
                              onChange={(event) =>
                                setParam(param.key, event.target.value)
                              }
                              className="rounded-[8px] border border-white/15 bg-white/[0.04] px-2 py-1.5 text-[12px] text-text-dark outline-none disabled:opacity-50"
                            >
                              {param.options.map((opt) => (
                                <option key={opt.value} value={opt.value}>
                                  {t(opt.labelKey)}
                                </option>
                              ))}
                            </select>
                          </label>
                        );
                      }
                      // textarea
                      return (
                        <label
                          key={param.key}
                          className="flex flex-col gap-1 text-[11px] text-text-muted/90"
                        >
                          <span>
                            {param.key === "instruction"
                              ? t("smartEdit.params.instruction")
                              : t("smartEdit.params.prompt")}
                          </span>
                          <textarea
                            value={paramValue(param.key, "")}
                            disabled={isProcessing}
                            rows={2}
                            onChange={(event) =>
                              setParam(param.key, event.target.value)
                            }
                            className="resize-none rounded-[8px] border border-white/15 bg-white/[0.04] px-2 py-1.5 text-[12px] text-text-dark outline-none disabled:opacity-50"
                          />
                        </label>
                      );
                    })}

                    <button
                      type="button"
                      disabled={isProcessing}
                      onClick={(event) => {
                        event.stopPropagation();
                        void handleRun();
                      }}
                      className="mt-1 flex h-9 w-full items-center justify-center rounded-[10px] border border-white/15 bg-white/[0.06] px-4 text-[13px] text-text-dark transition-colors hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-45"
                    >
                      {isProcessing
                        ? t("smartEdit.actions.processing")
                        : t("smartEdit.actions.run")}
                    </button>

                    {data.lastError ? (
                      <p className="text-[11px] text-red-400">{data.lastError}</p>
                    ) : null}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>
    );
  },
);

// 从任务 result 尽力取文本字段（copy / 剧本类技能）。
function readResultText(result: unknown): string {
  if (!result || typeof result !== "object") return "";
  const source = result as Record<string, unknown>;
  for (const key of ["text", "copywriting", "content", "transcript"]) {
    const value = source[key];
    if (typeof value === "string" && value.trim().length > 0) return value;
  }
  return "";
}

SmartEditNode.displayName = "SmartEditNode";
