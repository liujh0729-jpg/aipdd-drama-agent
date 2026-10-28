// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 输入区：Enter 发送 / Shift+Enter 换行；流式进行中变为「停止生成」。
// 对话类型（文字/图片/视频，默认文字）随回合发送；参考素材有两个入口：
// 选材弹窗（pick 模式）与左栏资产树（拖拽到位即收，见 core 的 AGENT_ASSET_DRAG_MIME；
// 点击直引走 ComposerHandle）。以 chips 展示并随回合附带——发送后清空，参考原文不落库、
// 不进入历史重放。
import {
  forwardRef,
  lazy,
  Suspense,
  useImperativeHandle,
  useState,
  type DragEvent as ReactDragEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { Image as ImageIcon, Music, Paperclip, Send, Square, Video, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { SUBTLE_HEADER_ACTION_BUTTON_CLASS } from "@/components/ui/header-action-styles";
import { Textarea } from "@/components/ui/textarea";
import type {
  AgentDialogMode,
  AgentTurnReference,
  AgentTurnReferenceMedia,
} from "@/lib/queries/agent";
import { cn } from "@/lib/utils";

import { AGENT_ASSET_DRAG_MIME } from "../core";
import type { AgentMediaParamsValue } from "../mediaParams";
import { ComposerMediaParams } from "./ComposerMediaParams";
import { ComposerModelPicker } from "./ComposerModelPicker";

// 资产库弹窗是画布域的重依赖（取数 + 文件夹管理），只在用户实际点「参考素材」时加载。
const AssetLibraryModal = lazy(() =>
  import("@/features/canvas/ui/AssetLibraryModal").then((module) => ({
    default: module.AssetLibraryModal,
  })),
);

/** 单回合最多附带的参考素材条数（与后端 _REFERENCE_LIMIT 同口径）。 */
const MAX_REFERENCES = 12;

const REFERENCE_ICONS: Record<AgentTurnReferenceMedia, LucideIcon> = {
  image: ImageIcon,
  video: Video,
  audio: Music,
};

/** 发送载荷：内容 + 本回合对话类型 + 参考素材 + 用户显式设定的媒体参数。 */
export interface ComposerSendPayload {
  content: string;
  mode: AgentDialogMode;
  references: AgentTurnReference[];
  /** 图片模式：用户在输入区显式选择的分辨率（未选不携带）。 */
  imageSize?: string;
  /** 视频模式：用户显式选择的分辨率（未选不携带）。 */
  videoResolution?: string;
  /** 视频模式：用户显式选择的时长（秒；未选不携带）。 */
  videoDuration?: number;
  /** 视频模式：超清目标档位（req22；非空 = 开启两步处理，未开启不携带）。 */
  videoHdResolution?: string;
}

/** 命令式句柄：左栏资产树点击素材时，把条目直接并入参考 chips。 */
export interface ComposerHandle {
  addReference: (reference: AgentTurnReference) => void;
}

/** 仅接受这三种媒介的拖拽载荷（draft 等一律拒收）。 */
function isReferenceMedia(value: unknown): value is AgentTurnReferenceMedia {
  return value === "image" || value === "video" || value === "audio";
}

export interface ComposerProps {
  /** 当前项目（参考素材选自该项目的资产库）。 */
  project: string;
  /** 未选择会话等不可发送状态。 */
  disabled?: boolean;
  streaming: boolean;
  onSend: (payload: ComposerSendPayload) => void;
  onStop: () => void;
}

export const Composer = forwardRef<ComposerHandle, ComposerProps>(function Composer(
  { project, disabled, streaming, onSend, onStop },
  ref,
) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const [mode, setMode] = useState<AgentDialogMode>("text");
  const [references, setReferences] = useState<AgentTurnReference[]>([]);
  // 回合媒体参数（分辨率 / 时长）：粘性设置，发送后不清空；只在用户显式选过后发送。
  const [mediaParams, setMediaParams] = useState<AgentMediaParamsValue>({});
  const [pickerOpen, setPickerOpen] = useState(false);
  // 资产树拖拽悬停在输入区上方时的高亮态。
  const [dropActive, setDropActive] = useState(false);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled || streaming) return;
    setValue("");
    const payload: ComposerSendPayload = { content: trimmed, mode, references };
    // 媒体参数按本回合类型映射：只带用户显式选过的字段（见 mediaParams 说明）。
    if (mode === "image" && mediaParams.imageSize) {
      payload.imageSize = mediaParams.imageSize;
    } else if (mode === "video") {
      if (mediaParams.videoResolution) payload.videoResolution = mediaParams.videoResolution;
      if (mediaParams.videoDuration != null) payload.videoDuration = mediaParams.videoDuration;
      if (mediaParams.videoHdResolution) {
        payload.videoHdResolution = mediaParams.videoHdResolution;
      }
    }
    onSend(payload);
    setReferences([]);
  };

  const removeReference = (url: string) => {
    setReferences((prev) => prev.filter((item) => item.url !== url));
  };

  // 选中即合并去重（同 URL 只保留一条），上限与后端一致。
  const mergeReferences = (picked: AgentTurnReference[]) => {
    setReferences((prev) => {
      const merged = [...prev];
      for (const item of picked) {
        if (merged.length >= MAX_REFERENCES) break;
        if (!item.url || merged.some((ref) => ref.url === item.url)) continue;
        merged.push(item);
      }
      return merged;
    });
  };

  // 左栏资产树的「点击即引用」通道：树在中栏之外，用命令式句柄把素材并进 chips。
  useImperativeHandle(ref, () => ({
    addReference: (reference) => mergeReferences([reference]),
  }));

  const handleDragOver = (event: ReactDragEvent<HTMLDivElement>) => {
    // 只接资产树的自定义载荷：文件、文本等其它拖拽不进这个收口。
    if (!event.dataTransfer.types.includes(AGENT_ASSET_DRAG_MIME)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    if (!dropActive) setDropActive(true);
  };

  const handleDragLeave = (event: ReactDragEvent<HTMLDivElement>) => {
    // 在子元素之间移动也会触发 dragleave：真正离开容器时才收起高亮。
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDropActive(false);
  };

  const handleDrop = (event: ReactDragEvent<HTMLDivElement>) => {
    setDropActive(false);
    const raw = event.dataTransfer.getData(AGENT_ASSET_DRAG_MIME);
    if (!raw) return;
    event.preventDefault();
    try {
      const picked = JSON.parse(raw) as Partial<AgentTurnReference>;
      if (typeof picked?.url === "string" && picked.url && isReferenceMedia(picked.media)) {
        mergeReferences([
          {
            name: typeof picked.name === "string" ? picked.name : "",
            url: picked.url,
            media: picked.media,
          },
        ]);
      }
    } catch {
      // 载荷不是本页写的 JSON：忽略，不当参考素材处理。
    }
  };

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={cn(
        "border-t border-border/30 bg-background px-3 py-2.5 transition-colors",
        dropActive && "border-primary/60 bg-primary/[0.04]",
      )}
    >
      {dropActive ? (
        <p className="mb-1.5 rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-[11px] text-primary">
          {t("agent.chat.dropHint")}
        </p>
      ) : null}
      {references.length > 0 ? (
        <div className="mb-1.5 flex flex-wrap items-center gap-1">
          {references.map((item) => {
            const Icon = REFERENCE_ICONS[item.media] ?? ImageIcon;
            return (
              <span
                key={item.url}
                className="flex max-w-52 items-center gap-1 rounded-md border border-white/10 bg-white/[0.05] px-1.5 py-0.5 text-[11px] text-muted-foreground"
                title={item.url}
              >
                <Icon className="size-3 shrink-0" />
                <span className="truncate">{item.name || item.url}</span>
                <button
                  type="button"
                  title={t("agent.chat.reference.remove")}
                  onClick={() => removeReference(item.url)}
                  className="shrink-0 rounded-sm p-0.5 transition-colors hover:text-foreground"
                >
                  <X className="size-3" />
                </button>
              </span>
            );
          })}
        </div>
      ) : null}
      <Textarea
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder={t("agent.chat.placeholder")}
        rows={4}
        disabled={disabled}
        className="min-h-24 w-full resize-none"
      />
      <div className="mt-1.5 flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <ComposerModelPicker mode={mode} onModeChange={setMode} />
          <ComposerMediaParams
            project={project}
            mode={mode}
            value={mediaParams}
            onChange={setMediaParams}
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 shrink-0 gap-1 rounded-[7px] border-white/10 bg-transparent px-2 text-[11px] font-normal shadow-none hover:bg-white/[0.06]"
            disabled={disabled || references.length >= MAX_REFERENCES}
            onClick={() => setPickerOpen(true)}
          >
            <Paperclip className="size-3.5" />
            {t("agent.chat.reference.add")}
          </Button>
        </div>
        <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
          {streaming ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onStop}
              className={SUBTLE_HEADER_ACTION_BUTTON_CLASS}
            >
              <Square className="size-3.5" />
              {t("agent.chat.stop")}
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              onClick={submit}
              disabled={disabled || !value.trim()}
              className="h-8 gap-1.5 rounded-[8px] bg-primary px-3 text-xs font-normal text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75"
            >
              <Send className="size-3.5" />
              {t("agent.chat.send")}
            </Button>
          )}
        </div>
      </div>
      {pickerOpen ? (
        <Suspense fallback={null}>
          <AssetLibraryModal
            mode="pick"
            open={pickerOpen}
            project={project}
            onClose={() => setPickerOpen(false)}
            maxSelectable={MAX_REFERENCES}
            onConfirm={(selections) =>
              mergeReferences(
                // [aipdd fork delta] 剪映草稿 / 3D / 文本不是可引用的素材（选材态
                // 本就选不中），这里只做类型上的收口：媒体类型收窄到 image / video / audio。
                selections.flatMap((selection) =>
                  selection.media === "draft" ||
                  selection.media === "model3d" ||
                  selection.media === "text"
                    ? []
                    : [
                        {
                          name: selection.name,
                          url: selection.url,
                          media: selection.media,
                        },
                      ],
                ),
              )
            }
          />
        </Suspense>
      ) : null}
    </div>
  );
});