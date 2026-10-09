// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 白模视频节点（需求 #29）：承载一段从 Blender 等工具导出／上传
// 的白模（clay）预览视频，作为参考创作成片的素材源。轻量定位 —— 只负责「落一条后端
// 可静态打开的视频 URL + 预览」，源 handle 开放可往下游成片供参考，不做转码／分析。
// [aipdd fork delta B] 「从 Blender 拉取」经后端直连本机 Blender 插件 socket 渲染导出，
// 再入库成本项目静态直链写回本节点；连不上 Blender 时回退手动入库（粘贴链接／选文件）。
import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from "@xyflow/react";
import { Boxes, Upload, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  ingestWhiteModelVideo,
  pullWhiteModelFromBlender,
  uploadFreezoneVideo,
} from "@/api/ops";
import { useCanvasStore } from "@/stores/canvasStore";
import {
  CANVAS_NODE_TYPES,
  type WhiteModelVideoNodeData,
} from "@/features/canvas/domain/canvasNodes";
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

type WhiteModelVideoNodeProps = NodeProps & {
  id: string;
  data: WhiteModelVideoNodeData;
  selected?: boolean;
};

const NODE_WIDTH = 320;
const NODE_HEIGHT = 200;

export const WhiteModelVideoNode = memo(
  ({ id, data, selected }: WhiteModelVideoNodeProps) => {
    const { t } = useTranslation();
    const updateNodeInternals = useUpdateNodeInternals();
    const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
    const updateNodeData = useCanvasStore((state) => state.updateNodeData);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [showFallback, setShowFallback] = useState(false);
    const [fallbackUrl, setFallbackUrl] = useState("");

    const resolvedTitle = useMemo(
      () => localizeNodeDisplayName(CANVAS_NODE_TYPES.whiteModelVideo, data, t),
      [data, t],
    );
    const cardToneClass = canvasNodeFrameClass({ selected });
    const videoUrl = typeof data.videoUrl === "string" && data.videoUrl ? data.videoUrl : null;
    const isUploading = Boolean(data.isUploading);
    const isPulling = Boolean(data.isPulling);
    const busy = isUploading || isPulling;
    const url = readUrl();
    const project = url.project;
    const canvasId = url.canvas || "default";

    useEffect(() => {
      updateNodeInternals(id);
    }, [id, updateNodeInternals]);

    const openPicker = useCallback(() => {
      fileInputRef.current?.click();
    }, []);

    const applyResult = useCallback(
      (resultUrl: string, filename?: string) => {
        updateNodeData(id, {
          videoUrl: resultUrl,
          sourceFileName: filename || null,
          isUploading: false,
          isPulling: false,
        });
        setShowFallback(false);
        setFallbackUrl("");
      },
      [id, updateNodeData],
    );

    const handleFile = useCallback(
      async (file: File | undefined) => {
        if (!file || !project) return;
        updateNodeData(id, { isUploading: true });
        try {
          // 后端 /freezone/upload 返回可静态打开的直链，直接落进节点，跨刷新可回放。
          const uploaded = await uploadFreezoneVideo(project, file, file.name);
          applyResult(uploaded.url, uploaded.filename || file.name);
          toast.success(t("whiteModelVideo.actions.success"));
        } catch (error) {
          console.error("[white-model-video] upload failed", error);
          updateNodeData(id, { isUploading: false });
          toast.error(
            error instanceof Error
              ? error.message
              : t("whiteModelVideo.actions.error"),
          );
        }
      },
      [applyResult, id, project, t, updateNodeData],
    );

    const handlePull = useCallback(async () => {
      if (!project) return;
      updateNodeData(id, { isPulling: true });
      try {
        const result = await pullWhiteModelFromBlender(project, {
          canvasId,
          nodeId: id,
        });
        if (result.status === "pulled" && result.url) {
          applyResult(result.url, result.filename);
          toast.success(t("whiteModelVideo.actions.pullSuccess"));
        } else if (result.status === "unreachable") {
          // Blender 不可达不是错误而是需要兜底的结局：弹手动入库，别把栈直接吐给用户。
          updateNodeData(id, { isPulling: false });
          setShowFallback(true);
          toast.warning(result.message || t("whiteModelVideo.actions.unreachable"));
        } else {
          updateNodeData(id, { isPulling: false });
          toast.error(t("whiteModelVideo.actions.pullError"));
        }
      } catch (error) {
        console.error("[white-model-video] pull from blender failed", error);
        updateNodeData(id, { isPulling: false });
        setShowFallback(true);
        toast.error(
          error instanceof Error
            ? error.message
            : t("whiteModelVideo.actions.pullError"),
        );
      }
    }, [applyResult, canvasId, id, project, t, updateNodeData]);

    const handleIngestUrl = useCallback(async () => {
      const trimmed = fallbackUrl.trim();
      if (!trimmed || !project) return;
      updateNodeData(id, { isPulling: true });
      try {
        const result = await ingestWhiteModelVideo(project, {
          canvasId,
          nodeId: id,
          url: trimmed,
        });
        if (result.url) {
          applyResult(result.url, result.filename);
          toast.success(t("whiteModelVideo.actions.ingestSuccess"));
        } else {
          updateNodeData(id, { isPulling: false });
          toast.error(t("whiteModelVideo.actions.pullError"));
        }
      } catch (error) {
        console.error("[white-model-video] ingest url failed", error);
        updateNodeData(id, { isPulling: false });
        toast.error(
          error instanceof Error
            ? error.message
            : t("whiteModelVideo.actions.pullError"),
        );
      }
    }, [applyResult, canvasId, fallbackUrl, id, project, t, updateNodeData]);

    return (
      <div
        className="group relative h-full w-full overflow-visible"
        style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
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
          icon={<Boxes className="h-4 w-4" />}
          titleText={resolvedTitle}
          editable
          onTitleChange={(next) => updateNodeData(id, { displayName: next })}
        />

        <input
          ref={fileInputRef}
          type="file"
          accept="video/*"
          className="hidden"
          onChange={(event) => {
            void handleFile(event.target.files?.[0]);
            // 允许重复选同一个文件也能再次触发 change。
            event.target.value = "";
          }}
        />

        <div
          className={`relative flex h-full w-full flex-col overflow-hidden rounded-[var(--node-radius)] border ${CANVAS_NODE_INPUT_SURFACE_CLASS} transition-colors ${cardToneClass}`}
        >
          {showFallback ? (
            <div className="flex h-full w-full flex-col gap-2 px-4 py-3">
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-medium text-text-dark">
                  {t("whiteModelVideo.actions.fallbackTitle")}
                </span>
                <button
                  type="button"
                  onClick={() => setShowFallback(false)}
                  className="text-text-muted/90 hover:text-text-dark"
                  aria-label={t("whiteModelVideo.actions.fallbackCancel")}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
              <input
                type="url"
                value={fallbackUrl}
                onChange={(event) => setFallbackUrl(event.target.value)}
                placeholder={t("whiteModelVideo.actions.fallbackUrlPlaceholder")}
                className="h-8 w-full rounded-lg border border-border bg-background/40 px-2 text-[12px] text-text-dark outline-none focus:border-white/30"
              />
              <div className="mt-auto flex items-center gap-2">
                <button
                  type="button"
                  disabled={busy || !fallbackUrl.trim()}
                  onClick={() => void handleIngestUrl()}
                  className="flex h-8 flex-1 items-center justify-center rounded-[10px] border border-white/15 bg-white/[0.06] px-3 text-[12px] text-text-dark transition-colors hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {t("whiteModelVideo.actions.fallbackSubmit")}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={(event) => {
                    event.stopPropagation();
                    openPicker();
                  }}
                  className="flex h-8 items-center justify-center gap-1 rounded-[10px] border border-white/15 bg-transparent px-3 text-[12px] text-text-muted/90 transition-colors hover:bg-white/[0.06] disabled:opacity-50"
                >
                  <Upload className="h-3.5 w-3.5" />
                  {t("whiteModelVideo.actions.fallbackOrFile")}
                </button>
              </div>
            </div>
          ) : videoUrl ? (
            <div className="relative flex h-full w-full items-center justify-center">
              <video
                src={videoUrl}
                controls
                playsInline
                className="h-full w-full object-contain"
              />
              <button
                type="button"
                disabled={busy}
                onClick={(event) => {
                  event.stopPropagation();
                  void handlePull();
                }}
                className="absolute right-2 top-11 flex items-center gap-1 rounded-[8px] border border-white/15 bg-black/45 px-2 py-1 text-[11px] text-text-dark backdrop-blur transition-colors hover:bg-black/60 disabled:opacity-50"
              >
                <Boxes className="h-3 w-3" />
                {isPulling
                  ? t("whiteModelVideo.actions.pulling")
                  : t("whiteModelVideo.actions.pull")}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={(event) => {
                  event.stopPropagation();
                  openPicker();
                }}
                className="absolute right-2 top-2 flex items-center gap-1 rounded-[8px] border border-white/15 bg-black/45 px-2 py-1 text-[11px] text-text-dark backdrop-blur transition-colors hover:bg-black/60 disabled:opacity-50"
              >
                <Upload className="h-3 w-3" />
                {t("whiteModelVideo.actions.replace")}
              </button>
            </div>
          ) : (
            <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-5 py-4 text-center">
              <button
                type="button"
                disabled={busy}
                onClick={(event) => {
                  event.stopPropagation();
                  void handlePull();
                }}
                className="flex h-10 w-full items-center justify-center gap-2 rounded-[12px] border border-white/15 bg-white/[0.06] px-4 text-[13px] text-text-dark transition-colors hover:bg-white/[0.1] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Boxes className="h-4 w-4" />
                {isPulling
                  ? t("whiteModelVideo.actions.pulling")
                  : t("whiteModelVideo.actions.pull")}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={(event) => {
                  event.stopPropagation();
                  openPicker();
                }}
                className="flex h-8 w-full items-center justify-center gap-2 rounded-[10px] border border-white/10 bg-transparent px-4 text-[12px] text-text-muted/90 transition-colors hover:bg-white/[0.06] disabled:cursor-not-allowed disabled:opacity-50"
              >
                <Upload className="h-3.5 w-3.5" />
                {isUploading
                  ? t("whiteModelVideo.actions.uploading")
                  : t("whiteModelVideo.actions.upload")}
              </button>
              <span className="text-[12px] text-text-muted/90">
                {t("whiteModelVideo.hint")}
              </span>
            </div>
          )}
        </div>
      </div>
    );
  },
);

WhiteModelVideoNode.displayName = "WhiteModelVideoNode";
