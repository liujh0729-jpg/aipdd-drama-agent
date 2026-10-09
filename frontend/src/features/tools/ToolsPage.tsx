// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 项目「工具」栏目页：8 个可批量执行的功能，全部复用现有 freezone
// 提交函数与任务中心。批量语义 = 每条输入各提交一个任务，并发上限 3 以规避通道限流。
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle, ExternalLink, Loader2, Wrench } from "lucide-react";

import {
  fetchFreezoneAudioReferences,
  submitFreezoneAudioSpeech,
  submitFreezoneEdit,
  // [aipdd fork delta] 科普讲解视频提交。
  submitFreezoneExplainerVideo,
  submitFreezoneMediaCopywrite,
  // [aipdd fork delta req46] 音乐创作提交与两步翻唱预处理（抽歌词）。
  submitFreezoneMusicCoverPreprocess,
  submitFreezoneMusicCreate,
  submitFreezoneVideoClone,
  submitFreezoneVideoEdit,
  submitFreezoneVideoErase,
  submitFreezoneVideoIdea,
  submitFreezoneVideoMix,
  submitFreezoneVideoOmniGen,
  submitFreezoneVideoScriptReverse,
  // [aipdd fork delta] 白板动画视频提交。
  submitFreezoneWhiteboardAnimation,
  // [aipdd fork delta] 人声复刻「替换人声」模式提交。
  submitFreezoneVoiceReplace,
  // [aipdd fork delta] 工具页「视频配音」提交。
  submitFreezoneVideoDubbing,
  uploadFreezoneImage,
  type FreezoneAudioVoiceRef,
  // [aipdd fork delta req46] 音乐创作模式（与后端 Literal 同名）。
  type FreezoneMusicMode,
  type FreezoneVideoResolution,
} from "@/api/ops";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { useQuery } from "@tanstack/react-query";

import { BatchInputArea } from "@/features/tools/components/BatchInputArea";
import { BatchTaskList } from "@/features/tools/components/BatchTaskList";
import { BoxRegionEditor } from "@/features/tools/components/BoxRegionEditor";
import { ModelPicker } from "@/features/tools/components/ModelPicker";
import { ModelSelect } from "@/features/tools/components/ModelSelect";
import {
  pickAllowedOption,
  resolveModelAspectOptions,
} from "@/features/canvas/domain/mediaModelOptions";
import { useFreezoneMediaCopywriteModels } from "@/features/canvas/hooks/useFreezoneMediaCopywriteModels";
// [aipdd fork delta req46] 音乐创作面板要按目录里的渠道决定「站点切换」是否出现。
import { useFreezoneMusicModels } from "@/features/canvas/hooks/useFreezoneMusicModels";
import { useFreezoneTextModels } from "@/features/canvas/hooks/useFreezoneTextModels";
import { useFreezoneVideoModels } from "@/features/canvas/hooks/useFreezoneVideoModels";
import { useFreezoneVisionModels } from "@/features/canvas/hooks/useFreezoneVisionModels";
import { isVideoModeSupportedByModel } from "@/features/canvas/nodes/shared/videoModelCapabilities";
import {
  channelForDisplay,
  type ModelOption,
} from "@/features/canvas/ui/ProviderModelPicker";
import { displayModelName, providerChannelLabel } from "@/lib/provider-labels";
import { useModelGatewayConfig } from "@/lib/queries/model-gateway";
import { useTasks } from "@/lib/queries/tasks";
import {
  runBatch,
  toolByKind,
  toolGroupMembers,
  TOOL_GROUPS,
  TOOL_NAV,
  type BatchItem,
  type NormalizedBox,
  type ToolGroupId,
  type ToolKind,
} from "@/features/tools/core";

const BATCH_CONCURRENCY = 3;

/** omni（all_reference）适配过滤：与画布同源的能力口径，避免选到不支持参考输入的模型。 */
const OMNI_MODEL_FILTER = (model: ModelOption) =>
  isVideoModeSupportedByModel("allReference", model);

/** 视频去水印 AI 模式过滤：与后端 video-edit 门禁同源（目录声明 video_edit 能力的模型）。 */
const VIDEO_EDIT_MODEL_FILTER = (model: ModelOption) =>
  isVideoModeSupportedByModel("videoEdit", model);

function useSubmitting() {
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async (
      total: number,
      worker: (index: number) => Promise<unknown>,
      successHintKey?: string,
    ) => {
      if (total <= 0) return false;
      setBusy(true);
      try {
        await runBatch(
          Array.from({ length: total }, (_, i) => i),
          BATCH_CONCURRENCY,
          (index) => worker(index),
        );
        toast.success(useToolsT()("tools.common.submitted", { n: total }), {
          // 工具页产物统一自动入库「待分类资产」，提示用户去哪里找；产物不是
          // 媒体资产（如抖音文案提取的 txt）时由调用方传入自定义说明。
          description: useToolsT()(successHintKey ?? "tools.common.archivedHint"),
        });
        return true;
      } catch (error) {
        toast.error(error instanceof Error ? error.message : String(error));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [],
  );
  return { busy, run };
}

// 极小的 i18n 取词桥：useSubmitting 里不方便用 hook，运行时读一次。
let _tRef: ((key: string, opts?: Record<string, unknown>) => string) | null = null;
function useToolsT() {
  return (key: string, opts?: Record<string, unknown>) =>
    _tRef ? _tRef(key, opts) : key;
}

function SubmitButton({
  busy,
  disabled,
  onClick,
  children,
}: {
  busy: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      onClick={onClick}
      disabled={busy || disabled}
      className="h-8 gap-1.5 rounded-[8px] bg-primary px-3 text-sm font-medium text-primary-foreground shadow-none hover:bg-primary/85 active:bg-primary/75"
    >
      {busy ? <Loader2 className="size-3.5 animate-spin" /> : null}
      {children}
    </Button>
  );
}

/** 单个驱动素材（音频或人物参考图）上传入口。 */
function SingleRefInput({
  project,
  accept,
  value,
  onChange,
}: {
  project: string;
  accept: string;
  value: string | null;
  onChange: (url: string | null, name: string | null) => void;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLInputElement>(null);
  const [loading, setLoading] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <input
        ref={ref}
        type="file"
        accept={accept}
        className="hidden"
        onChange={async (e) => {
          const file = e.target.files?.[0];
          if (!file) return;
          setLoading(true);
          try {
            const up = await uploadFreezoneImage(project, file, file.name);
            onChange(up.url, file.name);
          } finally {
            setLoading(false);
            if (ref.current) ref.current.value = "";
          }
        }}
      />
      <Button type="button" size="sm" variant="outline" onClick={() => ref.current?.click()}>
        {loading ? <Loader2 className="size-4 animate-spin" /> : null}
        {t("tools.common.pickFile")}
      </Button>
      {value ? (
        <span className="max-w-[200px] truncate text-xs text-muted-foreground">
          {t("tools.common.picked")}
        </span>
      ) : null}
    </div>
  );
}

// [aipdd fork delta] 视频复刻（全自动成片）：参考视频 → 抽帧解读 → 逐镜复刻规划
// → 逐镜生成 → 自动拼接成片。frames 模式用原视频关键帧作逐镜参考图（需模型支持
// all_reference 能力）；text 模式纯文本重制。音频三态：模型生成音 / 静音 / 保留原声。
// 参考内容（图片，≤4 张）：角色/场景/风格参照，与提示词一起作用于对原视频的处理。
type CloneReferenceMode = "frames" | "text";
type CloneAudioMode = "generate" | "mute" | "source";
/**
 * [aipdd fork delta] 带参考内容时的复刻优先级：subject 主体替换优先（只提交参考
 * 内容，画面主体以参考图为准）；composition 忠实构图优先（关键帧与参考内容一并
 * 提交，实测部分模型会整体还原关键帧里的原主体）。默认 subject。
 */
type CloneReferencePriority = "subject" | "composition";

/** 参考内容上限，与后端 FreezoneVideoCloneRequest.reference_urls 的 max_length 对齐。 */
const CLONE_MAX_REFERENCE_CONTENT = 4;

function VideoClonePanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [items, setItems] = useState<BatchItem[]>([]);
  const [model, setModel] = useState<ModelOption | null>(null);
  const [prompt, setPrompt] = useState("");
  const [referenceMode, setReferenceMode] = useState<CloneReferenceMode>("frames");
  const [audioMode, setAudioMode] = useState<CloneAudioMode>("generate");
  const [refItems, setRefItems] = useState<BatchItem[]>([]);
  const [referencePriority, setReferencePriority] =
    useState<CloneReferencePriority>("subject");
  // [aipdd fork delta] 成片画幅：选项只来自模型目录声明的 ratioOptions（与画布同一
  // 解析入口，不在前端维护档位常量）。复刻以前不给出选择器、固定 16:9，竖屏参考
  // 视频会被复刻成横屏成片；目录没声明档位时不给选择器，沿用后端默认。
  const [aspectRatio, setAspectRatio] = useState("16:9");
  // [aipdd fork delta] 逐镜复刻规划（抽帧解读之后）的文本（LLM）模型："" =
  // 用系统默认文本模型（下拉默认项回显具体默认模型名；后端 env 兜底链）；
  // 默认模型渠道不可用时用户可改选其它 LLM 重试。
  const [planModel, setPlanModel] = useState("");
  const textModels = useFreezoneTextModels(project);
  // [aipdd fork delta] 系统默认文本模型（后端标 isDefault，兜底取首项）：下拉
  // 默认项直接回显具体模型名与渠道，不再只说「跟随系统默认」。
  const defaultTextModel =
    textModels.models.find((m) => m.isDefault) ?? textModels.models[0];

  // frames 模式或带参考内容时需 all_reference 能力：与画布/omni 同源的能力过滤；
  // 纯 text 模式不过滤。
  const needModel = referenceMode === "frames" || refItems.length > 0;
  const aspectOptions = useMemo(() => resolveModelAspectOptions(model), [model]);
  // 换模型后把越界的旧选择收回（目录声明为空时保留已选值，不发给后端空串）。
  const effectiveAspectRatio = pickAllowedOption(aspectRatio, aspectOptions);
  // 参考优先级只在「原帧参考 + 已选参考内容」时有意义：纯文本模式本来就不提交
  // 关键帧，没参考内容时两种取值行为相同。
  const showReferencePriority =
    referenceMode === "frames" && refItems.length > 0;

  const onSubmit = () => {
    if (items.length === 0) {
      toast.error(t("tools.common.needInput"));
      return;
    }
    if (refItems.length > CLONE_MAX_REFERENCE_CONTENT) {
      toast.error(
        t("tools.videoClone.referenceContentLimit", { n: CLONE_MAX_REFERENCE_CONTENT }),
      );
      return;
    }
    if (needModel && !model) {
      toast.error(t("tools.common.needModel"));
      return;
    }
    void run(items.length, (i) =>
      submitFreezoneVideoClone(project, {
        videoUrl: items[i].url,
        prompt: prompt.trim(),
        ...(refItems.length ? { referenceUrls: refItems.map((item) => item.url) } : {}),
        // 画布同口径：优先目录条目 catalogId，退目录 id（后端 identifiers 都认）。
        model: model?.catalogId ?? model?.id ?? undefined,
        // 空 = 不传 model_text，走后端系统默认文本模型链。
        modelText: planModel || undefined,
        referenceMode,
        referencePriority,
        audioMode,
        aspectRatio: effectiveAspectRatio,
        // 工具页批量任务：产物完成后自动存入资产库「待分类资产」。
        archiveToLibrary: true,
        sourceTool: "video-clone",
      }),
    ).then((ok) => {
      if (ok) {
        setItems([]);
        setRefItems([]);
      }
    });
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.model")}</Label>
        <ModelPicker
          project={project}
          kind="video"
          value={model?.id ?? null}
          onChange={setModel}
          filter={needModel ? OMNI_MODEL_FILTER : undefined}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoClone.modelHint")}</p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoClone.planModel")}</Label>
        <ModelSelect
          value={planModel}
          onChange={setPlanModel}
          ariaLabel={t("tools.videoClone.planModel")}
          groups={[
            {
              options: [
                {
                  value: "",
                  // [aipdd fork delta] 展示去渠道前缀（唯一真源 displayModelName）：
                  // 只显示模型真名，来源由旁的渠道胶囊表达；提交值仍为完整 id。
                  label: defaultTextModel
                    ? t("tools.common.modelDefault", {
                        model: displayModelName(
                          defaultTextModel.label,
                          defaultTextModel.channel,
                        ),
                      })
                    : t("tools.videoClone.planModelAuto"),
                  ...(defaultTextModel
                    ? { channel: channelForDisplay(defaultTextModel) }
                    : {}),
                },
                ...textModels.models.map((m) => ({
                  value: m.id,
                  label: displayModelName(m.label, m.channel),
                  channel: channelForDisplay(m),
                })),
              ],
            },
          ]}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoClone.planModelHint")}</p>
      </div>
      <BatchInputArea project={project} accept="video" value={items} onChange={setItems} />
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoClone.referenceMode")}</Label>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="clone-reference-mode"
              checked={referenceMode === "frames"}
              onChange={() => setReferenceMode("frames")}
            />
            {t("tools.videoClone.referenceFrames")}
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="clone-reference-mode"
              checked={referenceMode === "text"}
              onChange={() => setReferenceMode("text")}
            />
            {t("tools.videoClone.referenceText")}
          </label>
        </div>
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoClone.referenceHint")}</p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoClone.referenceContent", { n: CLONE_MAX_REFERENCE_CONTENT })}</Label>
        <BatchInputArea project={project} accept="image" value={refItems} onChange={setRefItems} />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoClone.referenceContentHint")}</p>
      </div>
      {showReferencePriority ? (
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoClone.referencePriority")}</Label>
          <div className="flex items-center gap-3 text-sm">
            <label className="flex items-center gap-1.5">
              <input
                type="radio"
                name="clone-reference-priority"
                checked={referencePriority === "subject"}
                onChange={() => setReferencePriority("subject")}
              />
              {t("tools.videoClone.prioritySubject")}
            </label>
            <label className="flex items-center gap-1.5">
              <input
                type="radio"
                name="clone-reference-priority"
                checked={referencePriority === "composition"}
                onChange={() => setReferencePriority("composition")}
              />
              {t("tools.videoClone.priorityComposition")}
            </label>
          </div>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.videoClone.referencePriorityHint")}
          </p>
        </div>
      ) : null}
      {aspectOptions.length > 0 ? (
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoClone.aspectRatio")}</span>
          <select
            value={effectiveAspectRatio}
            onChange={(e) => setAspectRatio(e.target.value)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            {aspectOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoClone.audioMode")}</Label>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="clone-audio-mode"
              checked={audioMode === "generate"}
              onChange={() => setAudioMode("generate")}
            />
            {t("tools.videoClone.audioGenerate")}
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="clone-audio-mode"
              checked={audioMode === "mute"}
              onChange={() => setAudioMode("mute")}
            />
            {t("tools.videoClone.audioMute")}
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="clone-audio-mode"
              checked={audioMode === "source"}
              onChange={() => setAudioMode("source")}
            />
            {t("tools.videoClone.audioSource")}
          </label>
        </div>
        {audioMode === "generate" ? (
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.videoClone.audioGenerateHint")}
          </p>
        ) : null}
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.prompt")}</Label>
        <Textarea
          rows={2}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("tools.videoClone.promptPlaceholder")}
        />
      </div>
      <SubmitButton
        busy={busy}
        disabled={items.length === 0 || (needModel && !model)}
        onClick={onSubmit}
      >
        {t("tools.common.submit", { n: items.length })}
      </SubmitButton>
    </div>
  );
}

// [aipdd fork delta] 混剪短视频：素材池（图片/视频，按勾选顺序循环铺满）→ 主题
// 或手写文案 → AI 配音（可选声线/音频模型）→ 可选烧录字幕 + BGM 混音 → 本地
// ffmpeg 成片。与其它工具不同：素材池一次提交只产出一条成片（run(1)），不是逐素材批量。
type MixAudioMode = "generate" | "mute";
type MixAspectRatio = "9:16" | "16:9" | "1:1";
type MixResolution = "720p" | "1080p";
type MixFitMode = "cover" | "contain";
type MixTransition = "fade" | "none";
type MixSubtitleSize = "small" | "medium" | "large";

/** 素材池上限，与后端 _MIX_MAX_MATERIALS 对齐。 */
const MIX_MAX_MATERIALS = 60;

function VideoMixPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [items, setItems] = useState<BatchItem[]>([]);
  const [subject, setSubject] = useState("");
  const [script, setScript] = useState("");
  const [audioMode, setAudioMode] = useState<MixAudioMode>("generate");
  // [aipdd fork delta] 字幕默认不烧录，由用户勾选决定；勾选后随载荷提交。
  const [subtitleEnabled, setSubtitleEnabled] = useState(false);
  // [aipdd fork delta] 字幕字号档位（勾选烧录时生效；medium=随画布自动换算）。
  const [subtitleFontSize, setSubtitleFontSize] = useState<MixSubtitleSize>("medium");
  const [model, setModel] = useState<ModelOption | null>(null);
  // [aipdd fork delta] 由主题自动生成口播文案的文本（LLM）模型："" = 跟随系统默认。
  const [scriptModel, setScriptModel] = useState("");
  const [voice, setVoice] = useState<FreezoneAudioVoiceRef | null>(null);
  const [bgmUrl, setBgmUrl] = useState<string | null>(null);
  const [bgmVolume, setBgmVolume] = useState("");
  const [emotionPrompt, setEmotionPrompt] = useState("");
  const [clipDuration, setClipDuration] = useState(3);
  const [maxDuration, setMaxDuration] = useState("");
  const [aspectRatio, setAspectRatio] = useState<MixAspectRatio>("9:16");
  const [resolution, setResolution] = useState<MixResolution>("720p");
  const [fitMode, setFitMode] = useState<MixFitMode>("cover");
  const [transition, setTransition] = useState<MixTransition>("fade");

  // 配音模式才需要声线列表；静音模式关闭请求（与 VoiceClonePanel 共享缓存）。
  const voiced = audioMode === "generate";
  const { data: refs } = useQuery({
    queryKey: ["tools", "freezone-audio-references", project],
    enabled: Boolean(project) && voiced,
    queryFn: async () => (await fetchFreezoneAudioReferences(project)).available,
  });
  // 静音模式不生成文案，文本模型目录关闭拉取（与声线列表同口径）。
  const textModels = useFreezoneTextModels(voiced ? project : null);
  // [aipdd fork delta] 系统默认文本模型（后端标 isDefault，兜底取首项）：下拉
  // 默认项直接回显具体模型名与渠道，不再只说「跟随系统默认」。
  const defaultTextModel =
    textModels.models.find((m) => m.isDefault) ?? textModels.models[0];

  const onSubmit = () => {
    if (items.length === 0) {
      toast.error(t("tools.common.needInput"));
      return;
    }
    if (items.length > MIX_MAX_MATERIALS) {
      toast.error(t("tools.videoMix.tooMany", { n: MIX_MAX_MATERIALS }));
      return;
    }
    if (voiced && !subject.trim() && !script.trim()) {
      toast.error(t("tools.videoMix.needTopicOrScript"));
      return;
    }
    // 素材池 -> 一条成片：run(1) 只包一层提交通知，不逐素材拆任务。
    void run(1, () =>
      submitFreezoneVideoMix(project, {
        // 勾选顺序 = 素材循环顺序（后端按该顺序切段循环铺满）。
        materialUrls: items.map((item) => item.url),
        subject: subject.trim(),
        script: script.trim(),
        audioMode,
        subtitleEnabled: voiced ? subtitleEnabled : false,
        subtitleFontSize: voiced && subtitleEnabled ? subtitleFontSize : undefined,
        bgmUrl: bgmUrl ?? "",
        bgmVolume: bgmVolume.trim() === "" ? null : Number(bgmVolume),
        aspectRatio,
        resolution,
        clipDuration,
        transition,
        fitMode,
        maxDurationSeconds:
          !voiced && maxDuration.trim() !== "" ? Number(maxDuration) : null,
        emotionPrompt: emotionPrompt.trim(),
        voiceRef: voiced ? voice : null,
        model: voiced ? model?.apiModel || undefined : undefined,
        // 空 = 不传 model_text，走后端系统默认文本模型链。
        modelText: voiced ? scriptModel || undefined : undefined,
        // 工具页任务：产物完成后自动存入资产库「待分类资产」。
        archiveToLibrary: true,
        sourceTool: "video-mix",
      }),
    ).then((ok) => ok && setItems([]));
  };

  return (
    <div className="space-y-3">
      {voiced ? (
        <>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.model")}</Label>
            <ModelPicker
              project={project}
              kind="audio"
              value={model?.id ?? null}
              onChange={setModel}
            />
            <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoMix.modelHint")}</p>
          </div>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.scriptModel")}</Label>
            <ModelSelect
              value={scriptModel}
              onChange={setScriptModel}
              ariaLabel={t("tools.videoMix.scriptModel")}
              groups={[
                {
                  options: [
                    {
                      value: "",
                      label: defaultTextModel
                        ? t("tools.common.modelDefault", {
                            model: displayModelName(
                              defaultTextModel.label,
                              defaultTextModel.channel,
                            ),
                          })
                        : t("tools.videoMix.scriptModelAuto"),
                      ...(defaultTextModel
                        ? { channel: channelForDisplay(defaultTextModel) }
                        : {}),
                    },
                    ...textModels.models.map((m) => ({
                      value: m.id,
                      label: displayModelName(m.label, m.channel),
                      channel: channelForDisplay(m),
                    })),
                  ],
                },
              ]}
            />
            <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoMix.scriptModelHint")}</p>
          </div>
        </>
      ) : null}
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.materials")}</Label>
        <BatchInputArea
          project={project}
          accept="video-or-image"
          value={items}
          onChange={setItems}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoMix.materialsHint")}</p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.subject")}</Label>
        <input
          value={subject}
          onChange={(e) => setSubject(e.target.value)}
          placeholder={t("tools.videoMix.subjectPlaceholder")}
          className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
        />
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.script")}</Label>
        <Textarea
          rows={3}
          value={script}
          onChange={(e) => setScript(e.target.value)}
          placeholder={t("tools.videoMix.scriptPlaceholder")}
        />
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.audioMode")}</Label>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="mix-audio-mode"
              checked={voiced}
              onChange={() => setAudioMode("generate")}
            />
            {t("tools.videoMix.audioGenerate")}
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="mix-audio-mode"
              checked={!voiced}
              onChange={() => setAudioMode("mute")}
            />
            {t("tools.videoMix.audioMute")}
          </label>
        </div>
      </div>
      {voiced ? (
        <>
          <div className="flex items-center gap-2 text-sm">
            <input
              id="mix-subtitle"
              type="checkbox"
              checked={subtitleEnabled}
              onChange={(e) => setSubtitleEnabled(e.target.checked)}
            />
            <label htmlFor="mix-subtitle">{t("tools.videoMix.subtitle")}</label>
          </div>
          {subtitleEnabled ? (
            <div className="space-y-1">
              <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.subtitleSize")}</Label>
              <select
                className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
                value={subtitleFontSize}
                onChange={(e) => setSubtitleFontSize(e.target.value as MixSubtitleSize)}
                aria-label={t("tools.videoMix.subtitleSize")}
              >
                <option value="small">{t("tools.videoMix.subtitleSizeSmall")}</option>
                <option value="medium">{t("tools.videoMix.subtitleSizeMedium")}</option>
                <option value="large">{t("tools.videoMix.subtitleSizeLarge")}</option>
              </select>
            </div>
          ) : null}
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.voice")}</Label>
            <select
              className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
              value={voice ? `${voice.scope}::${voice.characterName ?? ""}::${voice.voiceId ?? ""}` : ""}
              onChange={(e) => {
                const picked = (refs ?? []).find(
                  (r) =>
                    `${r.scope}::${(r.character_name as string) ?? ""}::${(r.voice_id as string) ?? ""}` ===
                    e.target.value,
                );
                if (!picked) {
                  setVoice(null);
                  return;
                }
                setVoice({
                  scope: picked.scope,
                  characterName: (picked.character_name as string) || undefined,
                  identityId: (picked.identity_id as string) || undefined,
                  slot: (picked.slot as string) || undefined,
                  voiceId: (picked.voice_id as string) || undefined,
                });
              }}
            >
              <option value="">{t("tools.videoMix.voiceDefault")}</option>
              {(refs ?? []).map((r, i) => (
                <option
                  key={i}
                  value={`${r.scope}::${(r.character_name as string) ?? ""}::${(r.voice_id as string) ?? ""}`}
                >
                  {(r.label as string) || r.character_name || r.scope}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.emotionPrompt")}</Label>
            <input
              value={emotionPrompt}
              onChange={(e) => setEmotionPrompt(e.target.value)}
              placeholder={t("tools.videoMix.emotionPlaceholder")}
              className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
            />
          </div>
        </>
      ) : (
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.maxDuration")}</Label>
          <input
            type="number"
            min={5}
            max={600}
            value={maxDuration}
            onChange={(e) => setMaxDuration(e.target.value)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoMix.maxDurationHint")}</p>
        </div>
      )}
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoMix.bgm")}</Label>
        <SingleRefInput
          project={project}
          accept="audio/*"
          value={bgmUrl}
          onChange={(url) => setBgmUrl(url)}
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoMix.bgmVolume")}</span>
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={bgmVolume}
            onChange={(e) => setBgmVolume(e.target.value)}
            placeholder={t("tools.videoMix.bgmVolumePlaceholder")}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoMix.clipDuration")}</span>
          <input
            type="number"
            min={2}
            max={10}
            step={1}
            value={clipDuration}
            onChange={(e) => setClipDuration(Number(e.target.value))}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoMix.aspectRatio")}</span>
          <select
            value={aspectRatio}
            onChange={(e) => setAspectRatio(e.target.value as MixAspectRatio)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="9:16">9:16</option>
            <option value="16:9">16:9</option>
            <option value="1:1">1:1</option>
          </select>
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoMix.resolution")}</span>
          <select
            value={resolution}
            onChange={(e) => setResolution(e.target.value as MixResolution)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
          </select>
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoMix.transition")}</span>
          <select
            value={transition}
            onChange={(e) => setTransition(e.target.value as MixTransition)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="fade">{t("tools.videoMix.transitionFade")}</option>
            <option value="none">{t("tools.videoMix.transitionNone")}</option>
          </select>
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoMix.fitMode")}</span>
          <select
            value={fitMode}
            onChange={(e) => setFitMode(e.target.value as MixFitMode)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="cover">{t("tools.videoMix.fitCover")}</option>
            <option value="contain">{t("tools.videoMix.fitContain")}</option>
          </select>
        </label>
      </div>
      <SubmitButton busy={busy} disabled={items.length === 0} onClick={onSubmit}>
        {t("tools.videoMix.submit")}
      </SubmitButton>
    </div>
  );
}

// [aipdd fork delta] 白板动画视频：口播文案 → AI 分镜 → 逐幕暖米黄线稿 → 逐笔
// 揭示手绘动画 → 拼接 + 配音（+ 可选字幕/BGM）成片。逐笔揭示与拼接/混音全部在
// 本地渲染（OpenCV + ffmpeg），不依赖浏览器、不需要额外安装；一次提交只产出一条
// 成片（不逐幕拆任务）。三类模型都可自选：分镜规划 LLM / 线稿出图 / 配音。
type WhiteboardAspectRatio = "16:9" | "9:16" | "1:1";
type WhiteboardResolution = "720p" | "1080p";
type WhiteboardSubtitleSize = "small" | "medium" | "large";

function WhiteboardAnimationPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [script, setScript] = useState("");
  // 分镜规划文本模型："" = 跟随系统默认文本链。
  const [textModel, setTextModel] = useState("");
  const [imageModel, setImageModel] = useState<ModelOption | null>(null);
  const [voiceModel, setVoiceModel] = useState<ModelOption | null>(null);
  const [voice, setVoice] = useState<FreezoneAudioVoiceRef | null>(null);
  const [maxScenes, setMaxScenes] = useState(8);
  const [penCursor, setPenCursor] = useState(true);
  const [subtitleEnabled, setSubtitleEnabled] = useState(false);
  const [subtitleFontSize, setSubtitleFontSize] =
    useState<WhiteboardSubtitleSize>("medium");
  const [emotionPrompt, setEmotionPrompt] = useState("");
  const [bgmUrl, setBgmUrl] = useState<string | null>(null);
  const [bgmVolume, setBgmVolume] = useState("");
  const [aspectRatio, setAspectRatio] = useState<WhiteboardAspectRatio>("16:9");
  const [resolution, setResolution] = useState<WhiteboardResolution>("720p");

  const { data: refs } = useQuery({
    queryKey: ["tools", "freezone-audio-references", project],
    enabled: Boolean(project),
    queryFn: async () => (await fetchFreezoneAudioReferences(project)).available,
  });
  const textModels = useFreezoneTextModels(project);
  // 默认项直接回显具体模型名与渠道，不再只说「跟随系统默认」（与混剪/一句话同口径）。
  const defaultTextModel =
    textModels.models.find((m) => m.isDefault) ?? textModels.models[0];

  const onSubmit = () => {
    if (!script.trim()) {
      toast.error(t("tools.whiteboardAnimation.needScript"));
      return;
    }
    // 文案 -> 一条成片：run(1) 只包一层提交通知，不逐幕拆任务。
    void run(1, () =>
      submitFreezoneWhiteboardAnimation(project, {
        script: script.trim(),
        // 空 = 不传 model_text，走后端系统默认文本模型链。
        modelText: textModel || undefined,
        // 画布同口径：优先目录条目 catalogId，退目录 id（后端 identifiers 都认）。
        modelImage: imageModel?.catalogId ?? imageModel?.id ?? undefined,
        model: voiceModel?.apiModel || undefined,
        maxScenes,
        penCursor,
        subtitleEnabled,
        subtitleFontSize: subtitleEnabled ? subtitleFontSize : undefined,
        emotionPrompt: emotionPrompt.trim(),
        voiceRef: voice,
        bgmUrl: bgmUrl ?? "",
        bgmVolume: bgmVolume.trim() === "" ? null : Number(bgmVolume),
        aspectRatio,
        resolution,
        // 工具页任务：产物完成后自动存入资产库「待分类资产」。
        archiveToLibrary: true,
        sourceTool: "whiteboard-animation",
      }),
    ).then((ok) => ok && setScript(""));
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.whiteboardAnimation.script")}</Label>
        <Textarea
          rows={4}
          value={script}
          onChange={(e) => setScript(e.target.value)}
          placeholder={t("tools.whiteboardAnimation.scriptPlaceholder")}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.whiteboardAnimation.scriptHint")}
        </p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.whiteboardAnimation.textModel")}</Label>
        <ModelSelect
          value={textModel}
          onChange={setTextModel}
          ariaLabel={t("tools.whiteboardAnimation.textModel")}
          groups={[
            {
              options: [
                {
                  value: "",
                  label: defaultTextModel
                    ? t("tools.common.modelDefault", {
                        model: displayModelName(
                          defaultTextModel.label,
                          defaultTextModel.channel,
                        ),
                      })
                    : t("tools.whiteboardAnimation.textModelAuto"),
                  ...(defaultTextModel
                    ? { channel: channelForDisplay(defaultTextModel) }
                    : {}),
                },
                ...textModels.models.map((m) => ({
                  value: m.id,
                  label: displayModelName(m.label, m.channel),
                  channel: channelForDisplay(m),
                })),
              ],
            },
          ]}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.whiteboardAnimation.textModelHint")}
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.whiteboardAnimation.imageModel")}</Label>
          <ModelPicker
            project={project}
            kind="image"
            value={imageModel?.id ?? null}
            onChange={setImageModel}
            ariaLabel={t("tools.whiteboardAnimation.imageModel")}
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.whiteboardAnimation.imageModelHint")}
          </p>
        </div>
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.whiteboardAnimation.voiceModel")}</Label>
          <ModelPicker
            project={project}
            kind="audio"
            value={voiceModel?.id ?? null}
            onChange={setVoiceModel}
            ariaLabel={t("tools.whiteboardAnimation.voiceModel")}
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.whiteboardAnimation.voiceModelHint")}
          </p>
        </div>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.whiteboardAnimation.voice")}</Label>
        <select
          className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          value={voice ? `${voice.scope}::${voice.characterName ?? ""}::${voice.voiceId ?? ""}` : ""}
          onChange={(e) => {
            const picked = (refs ?? []).find(
              (r) =>
                `${r.scope}::${(r.character_name as string) ?? ""}::${(r.voice_id as string) ?? ""}` ===
                e.target.value,
            );
            if (!picked) {
              setVoice(null);
              return;
            }
            setVoice({
              scope: picked.scope,
              characterName: (picked.character_name as string) || undefined,
              identityId: (picked.identity_id as string) || undefined,
              slot: (picked.slot as string) || undefined,
              voiceId: (picked.voice_id as string) || undefined,
            });
          }}
        >
          <option value="">{t("tools.whiteboardAnimation.voiceDefault")}</option>
          {(refs ?? []).map((r, i) => (
            <option
              key={i}
              value={`${r.scope}::${(r.character_name as string) ?? ""}::${(r.voice_id as string) ?? ""}`}
            >
              {(r.label as string) || r.character_name || r.scope}
            </option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.whiteboardAnimation.maxScenes")}</span>
          <input
            type="number"
            min={1}
            max={12}
            step={1}
            value={maxScenes}
            onChange={(e) => setMaxScenes(Number(e.target.value))}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.whiteboardAnimation.emotionPrompt")}</span>
          <input
            value={emotionPrompt}
            onChange={(e) => setEmotionPrompt(e.target.value)}
            placeholder={t("tools.whiteboardAnimation.emotionPlaceholder")}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <label className="flex items-center gap-1.5">
          <input
            id="whiteboard-pen-cursor"
            type="checkbox"
            checked={penCursor}
            onChange={(e) => setPenCursor(e.target.checked)}
          />
          <span>{t("tools.whiteboardAnimation.penCursor")}</span>
        </label>
        <label className="flex items-center gap-1.5">
          <input
            id="whiteboard-subtitle"
            type="checkbox"
            checked={subtitleEnabled}
            onChange={(e) => setSubtitleEnabled(e.target.checked)}
          />
          <span>{t("tools.whiteboardAnimation.subtitle")}</span>
        </label>
      </div>
      {subtitleEnabled ? (
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.whiteboardAnimation.subtitleSize")}</Label>
          <select
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
            value={subtitleFontSize}
            onChange={(e) =>
              setSubtitleFontSize(e.target.value as WhiteboardSubtitleSize)
            }
            aria-label={t("tools.whiteboardAnimation.subtitleSize")}
          >
            <option value="small">{t("tools.whiteboardAnimation.subtitleSizeSmall")}</option>
            <option value="medium">{t("tools.whiteboardAnimation.subtitleSizeMedium")}</option>
            <option value="large">{t("tools.whiteboardAnimation.subtitleSizeLarge")}</option>
          </select>
        </div>
      ) : null}
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.whiteboardAnimation.bgm")}</Label>
        <SingleRefInput
          project={project}
          accept="audio/*"
          value={bgmUrl}
          onChange={(url) => setBgmUrl(url)}
        />
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.whiteboardAnimation.bgmVolume")}</span>
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={bgmVolume}
            onChange={(e) => setBgmVolume(e.target.value)}
            placeholder={t("tools.whiteboardAnimation.bgmVolumePlaceholder")}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.whiteboardAnimation.aspectRatio")}</span>
          <select
            value={aspectRatio}
            onChange={(e) => setAspectRatio(e.target.value as WhiteboardAspectRatio)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="16:9">16:9</option>
            <option value="9:16">9:16</option>
            <option value="1:1">1:1</option>
          </select>
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.whiteboardAnimation.resolution")}</span>
          <select
            value={resolution}
            onChange={(e) => setResolution(e.target.value as WhiteboardResolution)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
          </select>
        </label>
      </div>
      <SubmitButton busy={busy} disabled={!script.trim()} onClick={onSubmit}>
        {t("tools.whiteboardAnimation.submit")}
      </SubmitButton>
    </div>
  );
}

// [aipdd fork delta] 科普讲解视频（anything2explainer 口径的本系统自研版）：
// 参考资料 → 分章解说词 → 分镜 DSL（term/flow/compare/bars/bignumber/list/
// cycle/timeline）→ 逐帧代码绘制 MG 动画 → 配音拼接成片。渲染全部在本地完成
// （Pillow 逐帧 + ffmpeg 流式编码），不依赖浏览器、不调图像/视频模型，只用网关
// 的文本与音频两类能力；成片固定 16:9 横屏，一次提交只产出一条成片（run(1)）。
type ExplainerBackground = "dots" | "stars";
type ExplainerResolution = "720p" | "1080p";

function ExplainerVideoPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [reference, setReference] = useState("");
  const [title, setTitle] = useState("");
  const [durationS, setDurationS] = useState(180);
  // 解说词与分镜共用的文本模型："" = 跟随系统默认文本链。
  const [textModel, setTextModel] = useState("");
  const [voiceModel, setVoiceModel] = useState<ModelOption | null>(null);
  const [voice, setVoice] = useState<FreezoneAudioVoiceRef | null>(null);
  const [emotionPrompt, setEmotionPrompt] = useState("");
  const [background, setBackground] = useState<ExplainerBackground>("dots");
  const [resolution, setResolution] = useState<ExplainerResolution>("720p");
  const [bgmUrl, setBgmUrl] = useState<string | null>(null);
  const [bgmVolume, setBgmVolume] = useState("");

  const { data: refs } = useQuery({
    queryKey: ["tools", "freezone-audio-references", project],
    enabled: Boolean(project),
    queryFn: async () => (await fetchFreezoneAudioReferences(project)).available,
  });
  const textModels = useFreezoneTextModels(project);
  const defaultTextModel =
    textModels.models.find((m) => m.isDefault) ?? textModels.models[0];

  const onSubmit = () => {
    if (!reference.trim()) {
      toast.error(t("tools.explainerVideo.needReference"));
      return;
    }
    // 参考资料 -> 一条成片：run(1) 只包一层提交通知，不逐镜拆任务。
    void run(1, () =>
      submitFreezoneExplainerVideo(project, {
        referenceContent: reference.trim(),
        title: title.trim(),
        durationS,
        // 空 = 不传 model_text，走后端系统默认文本模型链。
        modelText: textModel || undefined,
        model: voiceModel?.apiModel || undefined,
        background,
        resolution,
        emotionPrompt: emotionPrompt.trim(),
        voiceRef: voice,
        bgmUrl: bgmUrl ?? "",
        bgmVolume: bgmVolume.trim() === "" ? null : Number(bgmVolume),
        // 工具页任务：产物完成后自动存入资产库「待分类资产」。
        archiveToLibrary: true,
        sourceTool: "explainer-video",
      }),
    ).then((ok) => {
      if (ok) setReference("");
    });
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.explainerVideo.reference")}</Label>
        <Textarea
          rows={6}
          value={reference}
          onChange={(e) => setReference(e.target.value)}
          placeholder={t("tools.explainerVideo.referencePlaceholder")}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.explainerVideo.referenceHint")}
        </p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.explainerVideo.titleLabel")}</span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder={t("tools.explainerVideo.titlePlaceholder")}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.explainerVideo.duration")}</span>
          <input
            type="number"
            min={90}
            max={330}
            step={10}
            value={durationS}
            onChange={(e) => setDurationS(Number(e.target.value))}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.explainerVideo.textModel")}</Label>
        <ModelSelect
          value={textModel}
          onChange={setTextModel}
          ariaLabel={t("tools.explainerVideo.textModel")}
          groups={[
            {
              options: [
                {
                  value: "",
                  label: defaultTextModel
                    ? t("tools.common.modelDefault", {
                        model: displayModelName(
                          defaultTextModel.label,
                          defaultTextModel.channel,
                        ),
                      })
                    : t("tools.explainerVideo.textModelAuto"),
                  ...(defaultTextModel
                    ? { channel: channelForDisplay(defaultTextModel) }
                    : {}),
                },
                ...textModels.models.map((m) => ({
                  value: m.id,
                  label: displayModelName(m.label, m.channel),
                  channel: channelForDisplay(m),
                })),
              ],
            },
          ]}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.explainerVideo.textModelHint")}
        </p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.explainerVideo.voiceModel")}</Label>
        <ModelPicker
          project={project}
          kind="audio"
          value={voiceModel?.id ?? null}
          onChange={setVoiceModel}
          ariaLabel={t("tools.explainerVideo.voiceModel")}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.explainerVideo.voiceModelHint")}
        </p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.explainerVideo.voice")}</Label>
        <select
          className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          value={voice ? `${voice.scope}::${voice.characterName ?? ""}::${voice.voiceId ?? ""}` : ""}
          onChange={(e) => {
            const picked = (refs ?? []).find(
              (r) =>
                `${r.scope}::${(r.character_name as string) ?? ""}::${(r.voice_id as string) ?? ""}` ===
                e.target.value,
            );
            if (!picked) {
              setVoice(null);
              return;
            }
            setVoice({
              scope: picked.scope,
              characterName: (picked.character_name as string) || undefined,
              identityId: (picked.identity_id as string) || undefined,
              slot: (picked.slot as string) || undefined,
              voiceId: (picked.voice_id as string) || undefined,
            });
          }}
        >
          <option value="">{t("tools.explainerVideo.voiceDefault")}</option>
          {(refs ?? []).map((r, i) => (
            <option
              key={i}
              value={`${r.scope}::${(r.character_name as string) ?? ""}::${(r.voice_id as string) ?? ""}`}
            >
              {(r.label as string) || r.character_name || r.scope}
            </option>
          ))}
        </select>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.explainerVideo.background")}</span>
          <select
            value={background}
            onChange={(e) =>
              setBackground(e.target.value as ExplainerBackground)
            }
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="dots">{t("tools.explainerVideo.backgroundDots")}</option>
            <option value="stars">{t("tools.explainerVideo.backgroundStars")}</option>
          </select>
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.explainerVideo.resolution")}</span>
          <select
            value={resolution}
            onChange={(e) =>
              setResolution(e.target.value as ExplainerResolution)
            }
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
          </select>
        </label>
      </div>
      <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
        <span>{t("tools.explainerVideo.emotionPrompt")}</span>
        <input
          value={emotionPrompt}
          onChange={(e) => setEmotionPrompt(e.target.value)}
          placeholder={t("tools.explainerVideo.emotionPlaceholder")}
          className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
        />
      </label>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.explainerVideo.bgm")}</Label>
        <SingleRefInput
          project={project}
          accept="audio/*"
          value={bgmUrl}
          onChange={(url) => setBgmUrl(url)}
        />
      </div>
      <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
        <span>{t("tools.explainerVideo.bgmVolume")}</span>
        <input
          type="number"
          min={0}
          max={1}
          step={0.05}
          value={bgmVolume}
          onChange={(e) => setBgmVolume(e.target.value)}
          placeholder={t("tools.explainerVideo.bgmVolumePlaceholder")}
          className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
        />
      </label>
      <SubmitButton busy={busy} disabled={!reference.trim()} onClick={onSubmit}>
        {t("tools.explainerVideo.submit")}
      </SubmitButton>
    </div>
  );
}

// [aipdd fork delta] 一句话生成短视频（ViMax idea2video 口径）：一句话创意 →
// 故事扩写 → 角色三视图 → 分场景脚本 → 分镜/机位树/首尾帧 → 逐镜视频 → 拼接成片。
// 图片模型出肖像与帧、视频模型出逐镜；一次提交产出一条完整成片（run(1)）。
type IdeaPortraitMode = "three" | "front";

function IdeaVideoPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [idea, setIdea] = useState("");
  const [userRequirement, setUserRequirement] = useState("");
  const [style, setStyle] = useState("Cinematic, realistic, film-like.");
  // 文本（LLM）模型："" = 跟随系统默认（后端 env 兜底链），保持既有行为；
  // 显式选择才覆盖故事/角色/脚本/分镜等文本步骤（默认模型渠道不可用时换）。
  const [textModel, setTextModel] = useState("");
  const [imageModel, setImageModel] = useState<ModelOption | null>(null);
  const [videoModel, setVideoModel] = useState<ModelOption | null>(null);
  // 总时长口径：唯一数字输入（秒）；分场与分镜（场景数/镜头数/单镜时长）
  // 由 AI 依总时长自主规划。
  const [totalDuration, setTotalDuration] = useState(60);
  const [portraitMode, setPortraitMode] = useState<IdeaPortraitMode>("three");
  const [aspectRatio, setAspectRatio] = useState("16:9");
  const [resolution, setResolution] = useState<FreezoneVideoResolution>("720p");
  const [generateAudio, setGenerateAudio] = useState(true);
  // 文本模型候选（全局 store；目录拉取失败回落空列表 → 只留「跟随系统默认」）。
  const textModels = useFreezoneTextModels(project);
  // [aipdd fork delta] 系统默认文本模型（后端标 isDefault，兜底取首项）：下拉
  // 默认项直接回显具体模型名与渠道，不再只说「跟随系统默认」。
  const defaultTextModel =
    textModels.models.find((m) => m.isDefault) ?? textModels.models[0];

  const onSubmit = () => {
    if (!idea.trim()) {
      toast.error(t("tools.videoIdea.needIdea"));
      return;
    }
    // 创意 → 一条完整成片：run(1) 只包一层提交通知，不逐场景拆任务。
    void run(1, () =>
      submitFreezoneVideoIdea(project, {
        idea: idea.trim(),
        userRequirement: userRequirement.trim(),
        style: style.trim(),
        // 空 = 不传 model_text，走后端系统默认文本模型链（与改动前行为一致）。
        modelText: textModel || undefined,
        totalDuration,
        portraitMode,
        // 画布同口径：优先目录条目 catalogId，退目录 id（后端 identifiers 都认）。
        modelImage: imageModel?.catalogId ?? imageModel?.id ?? undefined,
        modelVideo: videoModel?.catalogId ?? videoModel?.id ?? undefined,
        aspectRatio,
        resolution,
        generateAudio,
        // 工具页任务：产物完成后自动存入资产库「待分类资产」。
        archiveToLibrary: true,
        sourceTool: "video-idea",
      }),
    ).then((ok) => ok && setIdea(""));
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoIdea.idea")}</Label>
        <Textarea
          rows={3}
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          placeholder={t("tools.videoIdea.ideaPlaceholder")}
        />
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoIdea.userRequirement")}</Label>
        <input
          value={userRequirement}
          onChange={(e) => setUserRequirement(e.target.value)}
          placeholder={t("tools.videoIdea.userRequirementPlaceholder")}
          className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
        />
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoIdea.style")}</Label>
        <input
          value={style}
          onChange={(e) => setStyle(e.target.value)}
          placeholder={t("tools.videoIdea.stylePlaceholder")}
          className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
        />
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoIdea.textModel")}</Label>
        <ModelSelect
          value={textModel}
          onChange={setTextModel}
          ariaLabel={t("tools.videoIdea.textModel")}
          groups={[
            {
              options: [
                {
                  value: "",
                  label: defaultTextModel
                    ? t("tools.common.modelDefault", {
                        model: displayModelName(
                          defaultTextModel.label,
                          defaultTextModel.channel,
                        ),
                      })
                    : t("tools.videoIdea.textModelAuto"),
                  ...(defaultTextModel
                    ? { channel: channelForDisplay(defaultTextModel) }
                    : {}),
                },
                ...textModels.models.map((m) => ({
                  value: m.id,
                  label: displayModelName(m.label, m.channel),
                  channel: channelForDisplay(m),
                })),
              ],
            },
          ]}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoIdea.textModelHint")}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoIdea.imageModel")}</Label>
          <ModelPicker
            project={project}
            kind="image"
            value={imageModel?.id ?? null}
            onChange={setImageModel}
            ariaLabel={t("tools.videoIdea.imageModel")}
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoIdea.imageModelHint")}</p>
        </div>
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoIdea.videoModel")}</Label>
          <ModelPicker
            project={project}
            kind="video"
            value={videoModel?.id ?? null}
            onChange={setVideoModel}
            ariaLabel={t("tools.videoIdea.videoModel")}
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoIdea.videoModelHint")}</p>
        </div>
      </div>
      <div className="space-y-1">
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoIdea.totalDuration")}</span>
          <input
            type="number"
            min={5}
            max={300}
            step={1}
            value={totalDuration}
            onChange={(e) => setTotalDuration(Number(e.target.value))}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          />
        </label>
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.videoIdea.totalDurationHint")}
        </p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoIdea.portraitMode")}</Label>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="idea-portrait-mode"
              checked={portraitMode === "three"}
              onChange={() => setPortraitMode("three")}
            />
            {t("tools.videoIdea.portraitThree")}
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="idea-portrait-mode"
              checked={portraitMode === "front"}
              onChange={() => setPortraitMode("front")}
            />
            {t("tools.videoIdea.portraitFront")}
          </label>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoIdea.aspectRatio")}</span>
          <select
            value={aspectRatio}
            onChange={(e) => setAspectRatio(e.target.value)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="16:9">16:9</option>
            <option value="9:16">9:16</option>
            <option value="1:1">1:1</option>
            <option value="4:3">4:3</option>
            <option value="3:4">3:4</option>
            <option value="21:9">21:9</option>
          </select>
        </label>
        <label className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
          <span>{t("tools.videoIdea.resolution")}</span>
          <select
            value={resolution}
            onChange={(e) => setResolution(e.target.value as FreezoneVideoResolution)}
            className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          >
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
          </select>
        </label>
        <label className="flex items-end gap-2 pb-1.5 text-sm">
          <input
            type="checkbox"
            checked={generateAudio}
            onChange={(e) => setGenerateAudio(e.target.checked)}
          />
          <span>{t("tools.videoIdea.generateAudio")}</span>
        </label>
      </div>
      <SubmitButton busy={busy} disabled={!idea.trim()} onClick={onSubmit}>
        {t("tools.videoIdea.submit")}
      </SubmitButton>
    </div>
  );
}

function VoiceClonePanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  // [aipdd fork delta] 两段式模式：文本合成（既有）/ 替换人声（新）。
  const [mode, setMode] = useState<"text" | "replace">("text");
  const [voice, setVoice] = useState<FreezoneAudioVoiceRef | null>(null);
  const [model, setModel] = useState<ModelOption | null>(null);
  const [text, setText] = useState("");
  // [aipdd fork delta] 替换人声：原始素材（音频/视频）+ 参考音频（目标音色）。
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [referenceUrl, setReferenceUrl] = useState<string | null>(null);
  const [referenceName, setReferenceName] = useState<string | null>(null);
  // [aipdd fork delta] req32：台词识别（ASR）候选下拉。本地音频不接受只吃公网
  // 直链的通道：候选清单按后端下发的 acceptsLocalAudio（渠道目录声明的转写端点）
  // 筛选，不再拿通道名反推；空值 = 执行时自动挑已就绪通道（doubao_asr → gateway），
  // 不再由提交端猜默认。
  const [asrModel, setAsrModel] = useState("");
  const { models: asrModels, isLoading: asrModelsLoading } =
    useFreezoneMediaCopywriteModels(project);
  const asrCandidates = useMemo(
    () => asrModels.filter((m) => m.acceptsLocalAudio !== false),
    [asrModels],
  );
  const selectedAsr = asrCandidates.find((m) => m.id === asrModel) ?? null;

  const { data: refs } = useQuery({
    queryKey: ["tools", "freezone-audio-references", project],
    enabled: Boolean(project),
    queryFn: async () => (await fetchFreezoneAudioReferences(project)).available,
  });

  const lines = useMemo(
    () => text.split("\n").map((l) => l.trim()).filter(Boolean),
    [text],
  );

  const onSubmit = () => {
    if (lines.length === 0) {
      toast.error(t("tools.voiceClone.emptyLines"));
      return;
    }
    void run(lines.length, (i) =>
      submitFreezoneAudioSpeech(project, {
        text: lines[i],
        voiceRef: voice,
        // 目录条目 -> 后端认的 apiModel（目录就绪后默认选中目录首项 = 后台
        // 「设置 → 默认生成模型」里的音频默认模型）；目录未配置时省略，走后端内置引擎。
        model: model?.apiModel || undefined,
        // 工具页批量任务：产物完成后自动存入资产库「待分类资产」。
        archiveToLibrary: true,
        sourceTool: "voice-clone",
      }),
    );
  };

  // [aipdd fork delta] 替换人声提交：两个上传齐备才提交，一次一条任务。
  const canReplace = Boolean(sourceUrl && referenceUrl);
  const onReplaceSubmit = () => {
    if (!canReplace) {
      toast.error(t("tools.common.needInput"));
      return;
    }
    void run(
      1,
      () =>
        submitFreezoneVoiceReplace(project, {
          sourceUrl: sourceUrl!,
          referenceAudioUrl: referenceUrl!,
          referenceName: referenceName || undefined,
          model: model?.apiModel || undefined,
          asrModel: asrModel || undefined,
          archiveToLibrary: true,
          sourceTool: "voice-clone",
        }),
      // 替换人声为单条任务，沿用默认「已入待分类资产」提示。
    ).then((ok) => {
      if (ok) {
        setSourceUrl(null);
        setReferenceUrl(null);
        setReferenceName(null);
      }
    });
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.voiceClone.mode")}</Label>
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="voice-clone-mode"
              checked={mode === "text"}
              onChange={() => setMode("text")}
            />
            {t("tools.voiceClone.modeText")}
          </label>
          <label className="flex items-center gap-1.5">
            <input
              type="radio"
              name="voice-clone-mode"
              checked={mode === "replace"}
              onChange={() => setMode("replace")}
            />
            {t("tools.voiceClone.modeReplace")}
          </label>
        </div>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.model")}</Label>
        <ModelPicker
          project={project}
          kind="audio"
          value={model?.id ?? null}
          onChange={setModel}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.voiceClone.modelHint")}</p>
      </div>
      {mode === "text" ? (
        <>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.voiceClone.voice")}</Label>
            <select
              className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
              value={voice ? `${voice.scope}::${voice.characterName ?? ""}::${voice.voiceId ?? ""}` : ""}
              onChange={(e) => {
                const picked = (refs ?? []).find(
                  (r) => `${r.scope}::${(r.character_name as string) ?? ""}::${(r.voice_id as string) ?? ""}` === e.target.value,
                );
                if (!picked) {
                  setVoice(null);
                  return;
                }
                setVoice({
                  scope: picked.scope,
                  characterName: (picked.character_name as string) || undefined,
                  identityId: (picked.identity_id as string) || undefined,
                  slot: (picked.slot as string) || undefined,
                  voiceId: (picked.voice_id as string) || undefined,
                });
              }}
            >
              <option value="">{t("tools.voiceClone.voiceDefault")}</option>
              {(refs ?? []).map((r, i) => (
                <option
                  key={i}
                  value={`${r.scope}::${(r.character_name as string) ?? ""}::${(r.voice_id as string) ?? ""}`}
                >
                  {(r.label as string) || r.character_name || r.scope}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.voiceClone.lines")}</Label>
            <Textarea
              rows={6}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={t("tools.voiceClone.linesPlaceholder")}
            />
            <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.voiceClone.linesHint", { n: lines.length })}</p>
          </div>
          <SubmitButton busy={busy} disabled={lines.length === 0} onClick={onSubmit}>
            {t("tools.common.submit", { n: lines.length })}
          </SubmitButton>
        </>
      ) : (
        <>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.voiceReplace.source")}</Label>
            <SingleRefInput
              project={project}
              accept="audio/*,video/*"
              value={sourceUrl}
              onChange={(url) => setSourceUrl(url)}
            />
            <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.voiceReplace.sourceHint")}</p>
          </div>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.voiceReplace.reference")}</Label>
            <SingleRefInput
              project={project}
              accept="audio/*"
              value={referenceUrl}
              onChange={(url, name) => {
                setReferenceUrl(url);
                setReferenceName(name);
              }}
            />
            <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.voiceReplace.referenceHint")}</p>
          </div>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.voiceReplace.asrModel")}</Label>
            <ModelSelect
              value={asrModel}
              onChange={setAsrModel}
              disabled={asrModelsLoading}
              ariaLabel={t("tools.voiceReplace.asrModel")}
              placeholder={t("tools.voiceReplace.asrAuto")}
              groups={[
                {
                  options: [
                    { value: "", label: t("tools.voiceReplace.asrAuto") },
                    ...asrCandidates.map((m) => ({
                      value: m.id,
                      label: displayModelName(
                        m.label,
                        m.channel !== undefined ? m.channel : m.provider,
                      ),
                      channel: m.channel !== undefined ? m.channel : m.provider,
                    })),
                  ],
                },
              ]}
            />
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {selectedAsr
                ? t("tools.voiceReplace.asrHintSelected")
                : t("tools.voiceReplace.asrHint")}
            </p>
          </div>
          {/* [aipdd fork delta] 显式选中的候选凭证/渠道未就绪时如实提醒去设置页
              配置；自动选择只会挑已就绪的候选，不触发该提醒。 */}
          {selectedAsr?.ready === false ? (
            <p className="flex items-start gap-1.5 rounded-md border border-amber-400/40 bg-amber-400/10 px-2.5 py-2 text-xs leading-relaxed text-amber-200">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
              <span>{t("tools.voiceReplace.asrNotConfigured")}</span>
            </p>
          ) : null}
          <SubmitButton busy={busy} disabled={!canReplace} onClick={onReplaceSubmit}>
            {t("tools.voiceReplace.submit")}
          </SubmitButton>
        </>
      )}
    </div>
  );
}

// [aipdd fork delta] 视频配音：独立工具页。与 VoiceClonePanel「替换人声」分支同族，
// 复用源素材上传 / 参考音频 / ASR 候选（本地音频口径：acceptsLocalAudio!==false 过滤）
// 与 audio 模型选择；不同之处：源固定为视频、新增语言下拉（作发音提示不翻译）、
// 末端把新声回填替换原视频整条音轨，产物是配好音的 mp4 视频。
// 语言下拉：默认「原语言/自动」（value 空，不带提示）；其余为发音/语种提示透传
// 合成上游 metadata（支持的渠道认、不支持的自然忽略），台词本身不翻译。选项名用
// 各语言自称（跨语言常量，不随界面语言切换）。
// 各语言自称（跨语言常量，不随界面语言切换）。
// i18n-exempt-start
const DUBBING_LANGUAGE_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "zh", label: "中文" },
  { value: "en", label: "English" },
  { value: "ja", label: "日本語" },
  { value: "ko", label: "한국어" },
  { value: "vi", label: "Tiếng Việt" },
  { value: "th", label: "ไทย" },
  { value: "id", label: "Bahasa Indonesia" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
  { value: "de", label: "Deutsch" },
  { value: "pt", label: "Português" },
  { value: "ru", label: "Русский" },
];
// i18n-exempt-end

function VideoDubbingPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [model, setModel] = useState<ModelOption | null>(null);
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [referenceUrl, setReferenceUrl] = useState<string | null>(null);
  const [referenceName, setReferenceName] = useState<string | null>(null);
  // [aipdd fork delta] 语种/发音提示（空 = 原语言/自动，不带提示）。
  const [language, setLanguage] = useState("");
  // ASR 候选：本地音频不接受只吃公网直链的通道（与 VoiceClonePanel 同口径）。
  const [asrModel, setAsrModel] = useState("");
  const { models: asrModels, isLoading: asrModelsLoading } =
    useFreezoneMediaCopywriteModels(project);
  const asrCandidates = useMemo(
    () => asrModels.filter((m) => m.acceptsLocalAudio !== false),
    [asrModels],
  );
  const selectedAsr = asrCandidates.find((m) => m.id === asrModel) ?? null;

  // 两个上传齐备才提交，一次一条任务。
  const canDub = Boolean(sourceUrl && referenceUrl);
  const onSubmit = () => {
    if (!canDub) {
      toast.error(t("tools.common.needInput"));
      return;
    }
    void run(
      1,
      () =>
        submitFreezoneVideoDubbing(project, {
          sourceUrl: sourceUrl!,
          referenceAudioUrl: referenceUrl!,
          referenceName: referenceName || undefined,
          language: language || undefined,
          model: model?.apiModel || undefined,
          asrModel: asrModel || undefined,
          archiveToLibrary: true,
          sourceTool: "video-dubbing",
        }),
      // 视频配音为单条任务，沿用默认「已入待分类资产」提示。
    ).then((ok) => {
      if (ok) {
        setSourceUrl(null);
        setReferenceUrl(null);
        setReferenceName(null);
      }
    });
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.model")}</Label>
        <ModelPicker
          project={project}
          kind="audio"
          value={model?.id ?? null}
          onChange={setModel}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoDubbing.modelHint")}</p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoDubbing.source")}</Label>
        <SingleRefInput
          project={project}
          accept="video/*"
          value={sourceUrl}
          onChange={(url) => setSourceUrl(url)}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoDubbing.sourceHint")}</p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoDubbing.reference")}</Label>
        <SingleRefInput
          project={project}
          accept="audio/*"
          value={referenceUrl}
          onChange={(url, name) => {
            setReferenceUrl(url);
            setReferenceName(name);
          }}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoDubbing.referenceHint")}</p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoDubbing.language")}</Label>
        <select
          className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
        >
          <option value="">{t("tools.videoDubbing.languageOriginal")}</option>
          {DUBBING_LANGUAGE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.videoDubbing.languageHint")}</p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.voiceReplace.asrModel")}</Label>
        <ModelSelect
          value={asrModel}
          onChange={setAsrModel}
          disabled={asrModelsLoading}
          ariaLabel={t("tools.voiceReplace.asrModel")}
          placeholder={t("tools.voiceReplace.asrAuto")}
          groups={[
            {
              options: [
                { value: "", label: t("tools.voiceReplace.asrAuto") },
                ...asrCandidates.map((m) => ({
                  value: m.id,
                  label: displayModelName(
                    m.label,
                    m.channel !== undefined ? m.channel : m.provider,
                  ),
                  channel: m.channel !== undefined ? m.channel : m.provider,
                })),
              ],
            },
          ]}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {selectedAsr
            ? t("tools.voiceReplace.asrHintSelected")
            : t("tools.voiceReplace.asrHint")}
        </p>
      </div>
      {/* [aipdd fork delta] 显式选中的候选凭证/渠道未就绪时如实提醒去设置页
          配置；自动选择只会挑已就绪的候选，不触发该提醒。 */}
      {selectedAsr?.ready === false ? (
        <p className="flex items-start gap-1.5 rounded-md border border-amber-400/40 bg-amber-400/10 px-2.5 py-2 text-xs leading-relaxed text-amber-200">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>{t("tools.voiceReplace.asrNotConfigured")}</span>
        </p>
      ) : null}
      <SubmitButton busy={busy} disabled={!canDub} onClick={onSubmit}>
        {t("tools.videoDubbing.submit")}
      </SubmitButton>
    </div>
  );
}

// [aipdd fork delta req46] 音乐创作：MiniMax Music 3.0 的五种创作模式，直连厂商
// 音乐接口（不经网关——网关没有音乐生成协议可转发）。模式 → 上游字段的收口点在
// 后端 minimax_music.build_music_payload，这里只做「用户选了什么」的表单约束：
// 提交前按同一组区间拦一次，越界当场提示，不派一个注定失败的任务（延续「失败
// 提示必须是详细诊断」——能在前端说清的不要等上游报错）。
type MusicMode = FreezoneMusicMode;

const MUSIC_MODES: MusicMode[] = [
  "one_sentence",
  "lyrics",
  "instrumental",
  "cover",
  "humming",
];

// 输出参数三个下拉取上游枚举原值（与后端 SAMPLE_RATES / BITRATES / AUDIO_FORMATS
// 同集合）：写错档位上游只会按自己的默认值出，界面显示与实际产物会不一致。
const MUSIC_SAMPLE_RATES = [16000, 24000, 32000, 44100];
const MUSIC_BITRATES = [32000, 64000, 128000, 256000];
const MUSIC_FORMATS: Array<"mp3" | "wav" | "pcm"> = ["mp3", "wav", "pcm"];

// 区间与后端一致：描述 ≤2000 字（翻唱/哼唱的目标风格描述另有下限 10 字）、
// 歌词 ≤3500 字、带翻唱特征 id 的改后歌词 10-1000 字。
const MUSIC_PROMPT_MAX = 2000;
const MUSIC_COVER_PROMPT_MIN = 10;
const MUSIC_LYRICS_MAX = 3500;
const MUSIC_EXTRACTED_LYRICS_MIN = 10;
const MUSIC_EXTRACTED_LYRICS_MAX = 1000;

// 翻唱 / 哼唱要的是翻唱模型（music-cover 一族）。目录条目除了模型名没有任何
// 字段能表明「能翻唱」，所以按上游命名特征筛这一条；后端 is_cover_model 用的是
// 同一个判定，两处不会各说一套。
const MUSIC_COVER_MODEL_FILTER = (model: ModelOption) =>
  /cover/i.test(`${model.apiModel} ${model.label}`);

const MUSIC_FIELD_SELECT_CLASS =
  "h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary";

function MusicCreatePanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [mode, setMode] = useState<MusicMode>("one_sentence");
  const [model, setModel] = useState<ModelOption | null>(null);
  const [prompt, setPrompt] = useState("");
  const [lyrics, setLyrics] = useState("");
  // 参考音频（翻唱原曲 / 哼唱录音）：单条，沿用工具页「上传 + 素材库」入口。
  const [items, setItems] = useState<BatchItem[]>([]);
  const [coverFeatureId, setCoverFeatureId] = useState("");
  const [preprocessing, setPreprocessing] = useState(false);
  const [sampleRate, setSampleRate] = useState(44100);
  const [bitrate, setBitrate] = useState(256000);
  const [audioFormat, setAudioFormat] = useState<"mp3" | "wav" | "pcm">("mp3");
  const [provider, setProvider] = useState("");

  const isCoverMode = mode === "cover" || mode === "humming";
  const showLyrics = mode === "lyrics" || isCoverMode;
  const reference = items[0] ?? null;
  const cleanPrompt = prompt.trim();
  const cleanLyrics = lyrics.trim();

  // 站点切换只在目录里同时出现两个站点渠道的音乐模型时出现：两边 API Key 不
  // 通用，只有一个站点时给切换器只会误导。留空 = 跟随所选模型条目所属渠道，
  // 由后端从目录条目上取（前端不猜渠道）。
  const { models: musicModels } = useFreezoneMusicModels(project);
  const providerChoices = useMemo(() => {
    const out: string[] = [];
    for (const entry of musicModels) {
      const name = String(entry.channel ?? entry.providerId ?? "").trim();
      if (name && !out.includes(name)) out.push(name);
    }
    return out;
  }, [musicModels]);

  const fail = (key: string, options?: Record<string, unknown>) => {
    toast.error(t(key, options));
    return false;
  };

  const validate = (): boolean => {
    // 歌词模式只有描述没有词也行（词由上游按描述生成），其余模式描述必填。
    if (mode !== "lyrics" && !cleanPrompt) return fail("tools.musicCreate.needPrompt");
    if (cleanPrompt.length > MUSIC_PROMPT_MAX) {
      return fail("tools.musicCreate.promptTooLong", { max: MUSIC_PROMPT_MAX });
    }
    if (isCoverMode && cleanPrompt.length < MUSIC_COVER_PROMPT_MIN) {
      return fail("tools.musicCreate.promptTooShort", { min: MUSIC_COVER_PROMPT_MIN });
    }
    if (mode === "lyrics" && !cleanLyrics) return fail("tools.musicCreate.needLyrics");
    if (cleanLyrics.length > MUSIC_LYRICS_MAX) {
      return fail("tools.musicCreate.lyricsTooLong", { max: MUSIC_LYRICS_MAX });
    }
    // 已抽到翻唱特征 = 两步流程的第二步，歌词必须落在这个区间（上游约束）。
    if (
      coverFeatureId &&
      (cleanLyrics.length < MUSIC_EXTRACTED_LYRICS_MIN ||
        cleanLyrics.length > MUSIC_EXTRACTED_LYRICS_MAX)
    ) {
      return fail("tools.musicCreate.extractedLyricsLength", {
        min: MUSIC_EXTRACTED_LYRICS_MIN,
        max: MUSIC_EXTRACTED_LYRICS_MAX,
      });
    }
    if (isCoverMode && !coverFeatureId && !reference) {
      return fail("tools.musicCreate.needReference");
    }
    return true;
  };

  const onSubmit = () => {
    if (!validate()) return;
    void run(1, () =>
      submitFreezoneMusicCreate(project, {
        mode,
        ...(cleanPrompt ? { prompt: cleanPrompt } : {}),
        ...(cleanLyrics ? { lyrics: cleanLyrics } : {}),
        model: model?.apiModel || undefined,
        provider: provider || undefined,
        // 特征 id 与参考音频上游只认一个：抽过词就按两步走，别再重复交原曲。
        ...(coverFeatureId
          ? { coverFeatureId }
          : reference
            ? { referenceAudio: reference.url }
            : {}),
        sampleRate,
        bitrate,
        audioFormat,
        ...(reference?.name ? { sourceName: reference.name } : {}),
        archiveToLibrary: true,
        sourceTool: "music-create",
      }),
    ).then((ok) => {
      if (!ok) return;
      setPrompt("");
      setLyrics("");
      setItems([]);
      setCoverFeatureId("");
    });
  };

  // 两步翻唱第一步（哼唱模式的主路径）：拿特征 id + ASR 抽取的词，用户在界面
  // 上改完词再提交生成。同步返回、不建任务。
  const onPreprocess = async () => {
    if (!reference) {
      fail("tools.musicCreate.needReference");
      return;
    }
    setPreprocessing(true);
    try {
      const result = await submitFreezoneMusicCoverPreprocess(project, {
        model: model?.apiModel || undefined,
        provider: provider || undefined,
        referenceAudio: reference.url,
      });
      setCoverFeatureId(result.cover_feature_id || "");
      // 用户已经写好的词不覆盖；没写才回填上游抽出来的词。
      setLyrics((prev) => (prev.trim() ? prev : (result.formatted_lyrics ?? "")));
      toast.success(t("tools.musicCreate.preprocessDone"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setPreprocessing(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.musicCreate.modeLabel")}</Label>
        <div
          className="inline-flex flex-wrap rounded-lg border border-border bg-white/[0.02] p-0.5"
          role="tablist"
          aria-label={t("tools.musicCreate.modeLabel")}
        >
          {MUSIC_MODES.map((item) => (
            <button
              key={item}
              type="button"
              role="tab"
              aria-selected={mode === item}
              onClick={() => {
                // 换模式 = 上一段参考音频抽出的翻唱特征不再适用（上游按内容
                // MD5 复用特征 id），带着旧 id 提交只会生成一首无关的歌。
                setMode(item);
                setCoverFeatureId("");
              }}
              className={cn(
                "rounded-[7px] px-3 py-1.5 text-xs font-medium transition-colors",
                mode === item
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(`tools.musicCreate.mode.${item}`)}
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.model")}</Label>
        <ModelPicker
          project={project}
          kind="music"
          value={model?.id ?? null}
          onChange={setModel}
          filter={isCoverMode ? MUSIC_COVER_MODEL_FILTER : undefined}
          ariaLabel={t("tools.musicCreate.modelLabel")}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.musicCreate.modelHint")}
        </p>
      </div>

      {providerChoices.length >= 2 ? (
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.musicCreate.providerLabel")}</Label>
          <select
            className={MUSIC_FIELD_SELECT_CLASS}
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            aria-label={t("tools.musicCreate.providerLabel")}
          >
            <option value="">{t("tools.musicCreate.providerFollow")}</option>
            {providerChoices.map((name) => (
              <option key={name} value={name}>
                {providerChannelLabel(name)}
              </option>
            ))}
          </select>
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.musicCreate.providerHint")}
          </p>
        </div>
      ) : null}

      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t(`tools.musicCreate.promptLabel.${mode}`)}</Label>
        <Textarea
          rows={mode === "lyrics" ? 2 : 4}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t(`tools.musicCreate.promptPlaceholder.${mode}`)}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.musicCreate.charCount", {
            n: cleanPrompt.length,
            max: MUSIC_PROMPT_MAX,
          })}
        </p>
      </div>

      {showLyrics ? (
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.musicCreate.lyrics")}</Label>
          <Textarea
            rows={6}
            value={lyrics}
            onChange={(e) => setLyrics(e.target.value)}
            placeholder={t("tools.musicCreate.lyricsPlaceholder")}
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.musicCreate.lyricsHint", {
              n: cleanLyrics.length,
              max: MUSIC_LYRICS_MAX,
            })}
          </p>
        </div>
      ) : null}

      {isCoverMode ? (
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.musicCreate.reference")}</Label>
          <BatchInputArea
            project={project}
            accept="audio"
            value={items}
            // 参考音频只取一条（上游一次只处理一首原曲）：新选的顶掉旧的，
            // 同时清掉上一次抽出的翻唱特征（换了素材，旧 id 对不上新内容）。
            onChange={(next) => {
              setCoverFeatureId("");
              setItems(next.slice(-1));
            }}
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.musicCreate.referenceHint")}
          </p>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={!reference || preprocessing}
              onClick={() => void onPreprocess()}
            >
              {preprocessing ? <Loader2 className="size-3.5 animate-spin" /> : null}
              {t("tools.musicCreate.preprocess")}
            </Button>
            {coverFeatureId ? (
              <span className="text-xs leading-relaxed text-primary">
                {t("tools.musicCreate.featureHint")}
              </span>
            ) : (
              <span className="text-[11px] leading-relaxed text-muted-foreground">
                {t("tools.musicCreate.preprocessHint")}
              </span>
            )}
          </div>
        </div>
      ) : null}

      <div className="grid gap-2 sm:grid-cols-3">
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.musicCreate.sampleRate")}</Label>
          <select
            className={MUSIC_FIELD_SELECT_CLASS}
            value={sampleRate}
            onChange={(e) => setSampleRate(Number(e.target.value))}
            aria-label={t("tools.musicCreate.sampleRate")}
          >
            {MUSIC_SAMPLE_RATES.map((value) => (
              <option key={value} value={value}>
                {value} Hz
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.musicCreate.bitrate")}</Label>
          <select
            className={MUSIC_FIELD_SELECT_CLASS}
            value={bitrate}
            onChange={(e) => setBitrate(Number(e.target.value))}
            aria-label={t("tools.musicCreate.bitrate")}
          >
            {MUSIC_BITRATES.map((value) => (
              <option key={value} value={value}>
                {Math.round(value / 1000)} kbps
              </option>
            ))}
          </select>
        </div>
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.musicCreate.format")}</Label>
          <select
            className={MUSIC_FIELD_SELECT_CLASS}
            value={audioFormat}
            onChange={(e) => setAudioFormat(e.target.value as "mp3" | "wav" | "pcm")}
            aria-label={t("tools.musicCreate.format")}
          >
            {MUSIC_FORMATS.map((value) => (
              <option key={value} value={value}>
                {value.toUpperCase()}
              </option>
            ))}
          </select>
        </div>
      </div>

      <SubmitButton busy={busy} onClick={onSubmit}>
        {t("tools.musicCreate.submit")}
      </SubmitButton>
    </div>
  );
}

// [aipdd fork delta] 视频去水印三引擎（2026-09-28 引擎 review 后定型）：
// 1. 本地引擎        —— 本机 ffmpeg delogo（不出网、无额度消耗）；
// 2. 火山 AI-MediaKit —— 云端智能擦除（未配置 / 失败自动回退本地）；
// 3. AI 视频编辑     —— 目录模型生成式重绘（video 目录按 video_edit 过滤；内部代号 seedance）。
// 前两者共用 freezone_video_erase（后端 engine 三态），后者走 freezone_video_gen。
// [aipdd fork delta] 需求 6（2026-10-01）：下拉移除「Seedance 系列」分组标题，
// 目录模型与本地下拉同列平铺 —— 网关拉出来是什么模型就展示什么模型，不再分类。
type WatermarkEngine = "local" | "mediakit" | "seedance";

function VideoWatermarkPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [items, setItems] = useState<BatchItem[]>([]);
  const [engine, setEngine] = useState<WatermarkEngine>("local");
  const [aiModel, setAiModel] = useState<ModelOption | null>(null);
  const [prompt, setPrompt] = useState("");
  const [mode, setMode] = useState<"smart_subtitle" | "box">("box");
  const [box, setBox] = useState<NormalizedBox>({ x: 0.05, y: 0.85, width: 0.9, height: 0.12 });
  // [aipdd fork delta] 框选预览取第一个视频；同一框 applies 到全部待处理视频。
  const previewUrl = items.find((it) => it.kind === "video")?.url ?? null;

  // AI 去水印候选：video 目录中声明 video_edit 能力的模型（与后端门禁同源）。
  const { models: videoModels } = useFreezoneVideoModels(project);
  const editModels = useMemo(
    () => videoModels.filter(VIDEO_EDIT_MODEL_FILTER),
    [videoModels],
  );
  // MediaKit 已配置状态：未配置时选「火山 AI-MediaKit」会回退本地，界面上如实提示。
  const gatewayConfig = useModelGatewayConfig();
  const mediakitConfigured = Boolean(gatewayConfig.data?.data?.videoEnhance?.configured);

  // 目录刷新导致所选 AI 模型下线时回落本地引擎，避免下拉值失配显示空白。
  useEffect(() => {
    if (engine !== "seedance") return;
    if (aiModel && editModels.some((m) => m.id === aiModel.id)) return;
    setEngine("local");
    setAiModel(null);
  }, [aiModel, editModels, engine]);

  // 选定目录模型 = AI 视频编辑路径（生成式去水印）；其余 = 擦除引擎。
  const aiMode = engine === "seedance";
  // 仅擦除引擎 + 框选模式下拉框可选；AI 模式只需预览播放定位水印。
  const boxEditable = !aiMode && mode === "box";

  const engineHint = aiMode
    ? t("tools.videoWatermark.engineHintSeedance")
    : engine === "mediakit"
      ? mediakitConfigured
        ? t("tools.videoWatermark.engineHintMediakit")
        : t("tools.videoWatermark.engineHintMediakitUnconfigured")
      : t("tools.videoWatermark.engineHintLocal");

  const onSubmit = () => {
    if (items.length === 0) {
      toast.error(t("tools.common.needInput"));
      return;
    }
    if (aiMode && !aiModel) {
      toast.error(t("tools.common.needModel"));
      return;
    }
    // 非 AI 编辑路径明确二选一传给后端（local = 强制本机；mediakit = 云端优先）。
    const eraseEngine: "local" | "mediakit" | null =
      engine === "seedance" ? null : engine;
    void run(items.length, async (i) => {
      if (eraseEngine == null) {
        await submitFreezoneVideoEdit(project, {
          videoUrl: items[i].url,
          prompt: prompt.trim() || t("tools.videoWatermark.defaultPrompt"),
          // origin = 保留原声；优先目录条目 catalogId，退目录 id（后端 identifiers 都认）。
          audioSetting: "origin",
          model: aiModel?.catalogId ?? aiModel?.id ?? undefined,
          genMode: "videoEdit",
          // 工具页批量任务：产物完成后自动存入资产库「待分类资产」。
          archiveToLibrary: true,
          sourceTool: "video-watermark",
        });
        return;
      }
      await submitFreezoneVideoErase(project, {
        sourceUrl: items[i].url,
        mode,
        box: mode === "box" ? box : null,
        // 显式引擎：local = 强制本机 ffmpeg；mediakit = 优先火山 AI MediaKit。
        engine: eraseEngine,
        // 工具页批量任务：产物完成后自动存入资产库「待分类资产」。
        archiveToLibrary: true,
        sourceTool: "video-watermark",
      });
    }).then((ok) => ok && setItems([]));
  };

  const selectValue = aiMode ? `model:${aiModel?.id ?? ""}` : engine;

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.model")}</Label>
        <ModelSelect
          value={selectValue}
          onChange={(next) => {
            if (next.startsWith("model:")) {
              const id = next.slice("model:".length);
              setAiModel(editModels.find((m) => m.id === id) ?? null);
              setEngine("seedance");
              return;
            }
            setEngine(next as "local" | "mediakit");
          }}
          ariaLabel={t("tools.common.model")}
          // [aipdd fork delta] 需求 6：单组平铺 —— 本地引擎 / MediaKit / 目录 AI 模型
          // 同列展示，无分组标题（原先「Seedance 系列」归类已移除）。
          groups={[
            {
              options: [
                { value: "local", label: t("tools.videoWatermark.engineLocal") },
                {
                  value: "mediakit",
                  label: t("tools.videoWatermark.engineMediakit"),
                  // [aipdd fork delta] 渠道胶囊展示子产品线名「火山 AI MediaKit」：渠道归属
                  // VolcEngine，但作为子产品线单独标注，不混进 VolcEngine 的分类标注。
                  channel: "mediakit",
                },
                ...editModels.map((m) => ({
                  value: `model:${m.id}`,
                  label: displayModelName(m.label, channelForDisplay(m)),
                  channel: channelForDisplay(m),
                })),
              ],
            },
          ]}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{engineHint}</p>
      </div>
      <BatchInputArea project={project} accept="video" value={items} onChange={setItems} />
      {aiMode ? (
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoWatermark.prompt")}</Label>
          <Textarea
            rows={2}
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder={t("tools.videoWatermark.defaultPrompt")}
          />
        </div>
      ) : (
        <div className="flex items-center gap-3 text-sm">
          <label className="flex items-center gap-1.5">
            <input type="radio" name="erase-mode" checked={mode === "smart_subtitle"} onChange={() => setMode("smart_subtitle")} />
            {t("tools.videoWatermark.modeSmart")}
          </label>
          <label className="flex items-center gap-1.5">
            <input type="radio" name="erase-mode" checked={mode === "box"} onChange={() => setMode("box")} />
            {t("tools.videoWatermark.modeBox")}
          </label>
        </div>
      )}
      <div className="space-y-2">
        {previewUrl ? (
          <BoxRegionEditor
            videoUrl={previewUrl}
            box={box}
            onBoxChange={setBox}
            selectionEnabled={boxEditable}
          />
        ) : (
          <div className="flex h-24 items-center justify-center rounded-lg border border-dashed border-border text-xs text-muted-foreground">
            {t("tools.videoWatermark.needVideoPreview")}
          </div>
        )}
        {boxEditable && items.length > 1 ? (
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.videoWatermark.applyAll", { n: items.length })}
          </p>
        ) : null}
        {boxEditable ? (
          <div className="grid grid-cols-4 gap-2">
            {(["x", "y", "width", "height"] as const).map((key) => (
              <label key={key} className="block space-y-1 text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
                <span>{t(`tools.videoWatermark.box.${key}`)}</span>
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={Number(box[key].toFixed(3))}
                  onChange={(e) =>
                    setBox((prev) => ({ ...prev, [key]: Number(e.target.value) }))
                  }
                  className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
                />
              </label>
            ))}
          </div>
        ) : null}
      </div>
      <SubmitButton busy={busy} disabled={items.length === 0} onClick={onSubmit}>
        {t("tools.common.submit", { n: items.length })}
      </SubmitButton>
    </div>
  );
}

function ImageWatermarkPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [items, setItems] = useState<BatchItem[]>([]);
  const [model, setModel] = useState<ModelOption | null>(null);
  const [prompt, setPrompt] = useState("");

  const onSubmit = () => {
    if (items.length === 0) {
      toast.error(t("tools.common.needInput"));
      return;
    }
    void run(items.length, (i) =>
      submitFreezoneEdit(project, {
        baseUrl: items[i].url,
        prompt: prompt.trim() || t("tools.imageWatermark.defaultPrompt"),
        aspectRatio: "original",
        // 目录条目 -> 后端认的 apiModel + 目录 id；provider 交由后端按目录条目校正，
        // 避免目录 providerId 与 FreezoneProvider 联合类型不一致时的误传。
        model: model?.apiModel ?? null,
        modelId: model?.catalogId ?? model?.id ?? null,
        // 工具页批量任务：产物完成后自动存入资产库「待分类资产」。
        archiveToLibrary: true,
        sourceTool: "image-watermark",
      }),
    ).then((ok) => ok && setItems([]));
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.model")}</Label>
        <ModelPicker project={project} kind="image" value={model?.id ?? null} onChange={setModel} />
        <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.imageWatermark.modelHint")}</p>
      </div>
      <BatchInputArea project={project} accept="image" value={items} onChange={setItems} />
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.imageWatermark.prompt")}</Label>
        <Textarea
          rows={2}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("tools.imageWatermark.defaultPrompt")}
        />
      </div>
      <SubmitButton busy={busy} disabled={items.length === 0 || !model} onClick={onSubmit}>
        {t("tools.common.submit", { n: items.length })}
      </SubmitButton>
    </div>
  );
}

// [aipdd fork delta] req21 音视频提取文案（由抖音文案提取泛化）：三种来源
// 模式——链接下载（link，统一 yt-dlp、抖音 yt-dlp↔宿主 Chrome CDP 双通路）、上传
// 视频（upload_video）、上传音频（upload_audio）——→ 按用户选定的 ASR 模型
// 转写口播文案。模型候选来自网关拉取的模型 list（过滤 ASR 同类）+ 平台内置
// 直连通道（FunASR / 豆包语音）。不设默认模型：所有模型均需显式选择（含内置
// FunASR 候选，由后端候选清单下发）。一次提交解析一条媒体；任务结果带文案
// 全文，面板内直接展示最近一次提取结果。下载的视频与分离的音频由后端自动入资产库。
type MediaSourceType = "link" | "upload_video" | "upload_audio";

const MEDIA_SOURCE_TYPES: MediaSourceType[] = ["link", "upload_video", "upload_audio"];

// 链接平台白名单（与后端 _MEDIA_COPYWRITE_PLATFORMS 逐字对应）：仅作用 yt-dlp
// 提示与抖音双通路开关；"other" = 交 yt-dlp 自识别。枚举值不本地化（后端契约），
// 展示名走 i18n。
const MEDIA_PLATFORMS = [
  "douyin",
  "bilibili",
  "kuaishou",
  "xiaohongshu",
  "tiktok",
  "youtube",
  "other",
] as const;

function MediaCopywritePanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const [sourceType, setSourceType] = useState<MediaSourceType>("link");
  const [link, setLink] = useState("");
  const [platform, setPlatform] = useState<string>("douyin");
  // 上传视频 / 音频：先传 /freezone/upload 拿同源静态地址，提交时反解为本地文件。
  const [upload, setUpload] = useState<{ url: string; name: string } | null>(null);
  const [modelId, setModelId] = useState("");

  // [aipdd fork delta] ASR 候选目录走统一目录 store（与 Canvas 侧七个目录同语义）：
  // 一次拉取共享、失败回落空列表；空串 project 静默不拉（原 enabled 语义）。
  const { models, isLoading: modelsLoading } = useFreezoneMediaCopywriteModels(project);

  // 最近一次提取结果：任务中心同一份缓存里取本工具最新完成任务的结果文案，
  // 与下方任务列表数据源一致（不另起请求）。一并兼容旧抖音任务的历史结果。
  const { data: tasksData } = useTasks(project ? { project } : undefined);
  const latest = useMemo(() => {
    const list = (tasksData?.data ?? []).filter(
      (task) =>
        (task.task_type === "freezone_media_copywrite" ||
          task.task_type === "freezone_douyin_copywrite") &&
        task.status === "completed",
    );
    return (
      [...list].sort(
        (a, b) => (Date.parse(b.created_at ?? "") || 0) - (Date.parse(a.created_at ?? "") || 0),
      )[0] ?? null
    );
  }, [tasksData]);

  const latestResult =
    latest?.result && typeof latest.result === "object"
      ? (latest.result as Record<string, unknown>)
      : null;
  const latestText = typeof latestResult?.text === "string" ? latestResult.text : "";
  const latestTitle = typeof latestResult?.title === "string" ? latestResult.title : "";
  const latestAuthor = typeof latestResult?.author === "string" ? latestResult.author : "";
  const latestTxtUrl = typeof latestResult?.text_url === "string" ? latestResult.text_url : "";

  const selectedModel = models.find((m) => m.id === modelId) ?? null;

  // 当前来源要提交的 url：link 取文本框；upload_* 取上传返回的静态地址。
  const effectiveUrl = sourceType === "link" ? link.trim() : upload?.url ?? "";
  const needUrlKey =
    sourceType === "link"
      ? t("tools.mediaCopywrite.needLink")
      : sourceType === "upload_video"
        ? t("tools.mediaCopywrite.needVideoFile")
        : t("tools.mediaCopywrite.needAudioFile");

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(latestText);
      toast.success(t("tools.mediaCopywrite.copied"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  const onSubmit = () => {
    if (!effectiveUrl) {
      toast.error(needUrlKey);
      return;
    }
    // 需求 7：ASR 不设默认模型——必须显式选择后才能提交。
    if (!modelId) {
      toast.error(t("tools.mediaCopywrite.needModel"));
      return;
    }
    // 一条媒体 -> 一次解析：run(1) 只包一层提交通知。
    void run(
      1,
      () =>
        submitFreezoneMediaCopywrite(project, {
          sourceType,
          url: effectiveUrl,
          ...(sourceType === "link" ? { platform } : {}),
          model: modelId,
          sourceTool: "media-copywrite",
        }),
      "tools.mediaCopywrite.submittedHint",
    ).then((ok) => {
      if (!ok) return;
      if (sourceType === "link") setLink("");
      else setUpload(null);
    });
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.mediaCopywrite.sourceLabel")}</Label>
        <div className="inline-flex rounded-lg border border-border bg-white/[0.02] p-0.5">
          {MEDIA_SOURCE_TYPES.map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => {
                setSourceType(mode);
                setUpload(null);
              }}
              className={cn(
                "rounded-[7px] px-3 py-1.5 text-xs font-medium transition-colors",
                sourceType === mode
                  ? "bg-primary/15 text-primary"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {t(`tools.mediaCopywrite.source.${mode}`)}
            </button>
          ))}
        </div>
      </div>
      {sourceType === "link" ? (
        <>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.mediaCopywrite.link")}</Label>
            <Textarea
              rows={3}
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder={t("tools.mediaCopywrite.linkPlaceholder")}
            />
            <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.mediaCopywrite.linkHint")}</p>
          </div>
          <div className="space-y-1">
            <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.mediaCopywrite.platform")}</Label>
            <select
              className="h-9 w-full rounded-lg border border-border bg-background/40 px-2 text-sm text-foreground outline-none focus:border-primary"
              value={platform}
              onChange={(e) => setPlatform(e.target.value)}
              aria-label={t("tools.mediaCopywrite.platform")}
            >
              {MEDIA_PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {t(`tools.mediaCopywrite.platformOptions.${p}`)}
                </option>
              ))}
            </select>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              {t("tools.mediaCopywrite.platformHint")}
            </p>
          </div>
        </>
      ) : (
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">
            {sourceType === "upload_video"
              ? t("tools.mediaCopywrite.uploadVideo")
              : t("tools.mediaCopywrite.uploadAudio")}
          </Label>
          <SingleRefInput
            project={project}
            accept={sourceType === "upload_video" ? "video/*" : "audio/*"}
            value={upload?.url ?? null}
            onChange={(url, name) => setUpload(url && name ? { url, name } : null)}
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {sourceType === "upload_video"
              ? t("tools.mediaCopywrite.uploadVideoHint")
              : t("tools.mediaCopywrite.uploadAudioHint")}
          </p>
        </div>
      )}
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.mediaCopywrite.model")}</Label>
        <ModelSelect
          value={modelId}
          onChange={setModelId}
          disabled={modelsLoading}
          ariaLabel={t("tools.mediaCopywrite.model")}
          placeholder={t("tools.mediaCopywrite.modelPlaceholder")}
          groups={[
            {
              options: [
                ...models.map((m) => ({
                  value: m.id,
                  label: displayModelName(
                    m.label,
                    m.channel !== undefined ? m.channel : m.provider,
                  ),
                  // [aipdd fork delta] 渠道胶囊独立于 provider 渲染（后端随候选下发
                  // channel）；老响应缺失时回退 provider。
                  channel: m.channel !== undefined ? m.channel : m.provider,
                })),
              ],
            },
          ]}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {selectedModel
            ? selectedModel.acceptsLocalAudio === false
              ? t("tools.mediaCopywrite.modelHintPublicUrl", {
                  channel: selectedModel.channel ?? selectedModel.provider,
                })
              : t("tools.mediaCopywrite.modelHint")
            : t("tools.mediaCopywrite.modelHintChoose")}
        </p>
      </div>
      {/* [aipdd fork delta] 选中的 ASR 候选凭证/渠道未就绪时，在提交按钮附近
          明确提醒去设置页配置（火山语音 ASR 凭证与网关 API Key 不通用）。 */}
      {selectedModel && selectedModel.ready === false ? (
        <p className="flex items-start gap-1.5 rounded-md border border-amber-400/40 bg-amber-400/10 px-2.5 py-2 text-xs leading-relaxed text-amber-200">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>
            {selectedModel.kind === "funasr"
              ? t("tools.mediaCopywrite.notConfiguredFunasr")
              : t("tools.mediaCopywrite.notConfiguredDoubao")}
          </span>
        </p>
      ) : null}
      <SubmitButton busy={busy} disabled={!effectiveUrl || modelsLoading} onClick={onSubmit}>
        {t("tools.mediaCopywrite.submit")}
      </SubmitButton>
      <div className="space-y-2">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.mediaCopywrite.result")}</Label>
        {latestText ? (
          <div className="space-y-2 rounded-lg border border-border bg-white/[0.02] p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm text-foreground">
                  {latestTitle || t("tools.mediaCopywrite.result")}
                </div>
                <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  {[latestAuthor, t("tools.mediaCopywrite.charCount", { n: latestText.length })]
                    .filter(Boolean)
                    .join(" · ")}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Button type="button" size="sm" variant="outline" onClick={onCopy}>
                  {t("tools.mediaCopywrite.copy")}
                </Button>
                {latestTxtUrl ? (
                  <a
                    href={latestTxtUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  >
                    <ExternalLink className="size-3.5" />
                    {t("tools.mediaCopywrite.openTxt")}
                  </a>
                ) : null}
              </div>
            </div>
            <p className="max-h-64 overflow-auto whitespace-pre-wrap text-sm leading-relaxed text-foreground">
              {latestText}
            </p>
          </div>
        ) : (
          <p className="text-[11px] leading-relaxed text-muted-foreground">{t("tools.mediaCopywrite.resultEmpty")}</p>
        )}
      </div>
    </div>
  );
}

// [aipdd fork delta] 「视频反推剧本」：上传视频 → 抽帧 → 用户可选视觉模型
// 反推纯文本剧本。产物 txt（不覆盖故事脚本 JSON）；模型留空 = 后端默认视觉
// 链。视觉候选来自 /model-gateway/vision-models，候选为空不阻断提交。
// 结果展示与 media-copywrite 面板同源：任务中心缓存里取本工具最新完成
// 任务的 result.text / result.output_url，不另起请求。
function VideoReverseScriptPanel({ project }: { project: string }) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  // 先传 /freezone/upload 拿同源静态地址，提交时后端反解为本地文件。
  const [upload, setUpload] = useState<{ url: string; name: string } | null>(null);
  const [prompt, setPrompt] = useState("");
  // "" = 跟随系统默认（后端默认视觉链）。
  const [modelId, setModelId] = useState("");

  const { models, isLoading: modelsLoading } = useFreezoneVisionModels();

  const { data: tasksData } = useTasks(project ? { project } : undefined);
  const latest = useMemo(() => {
    const list = (tasksData?.data ?? []).filter(
      (task) =>
        task.task_type === "freezone_video_script_reverse" &&
        task.status === "completed",
    );
    return (
      [...list].sort(
        (a, b) => (Date.parse(b.created_at ?? "") || 0) - (Date.parse(a.created_at ?? "") || 0),
      )[0] ?? null
    );
  }, [tasksData]);

  const latestResult =
    latest?.result && typeof latest.result === "object"
      ? (latest.result as Record<string, unknown>)
      : null;
  const latestText = typeof latestResult?.text === "string" ? latestResult.text : "";
  const latestTitle = typeof latestResult?.title === "string" ? latestResult.title : "";
  const latestTxtUrl =
    typeof latestResult?.output_url === "string" ? latestResult.output_url : "";

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(latestText);
      toast.success(t("tools.videoReverseScript.copied"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  const onSubmit = () => {
    if (!upload?.url) {
      toast.error(t("tools.videoReverseScript.needVideoFile"));
      return;
    }
    // 一条视频 -> 一次反推：run(1) 只包一层提交通知。
    void run(
      1,
      () =>
        submitFreezoneVideoScriptReverse(project, {
          videoUrl: upload.url,
          ...(prompt.trim() ? { prompt: prompt.trim() } : {}),
          ...(modelId ? { model: modelId } : {}),
        }),
      "tools.videoReverseScript.submittedHint",
    ).then((ok) => {
      if (!ok) return;
      setUpload(null);
      setPrompt("");
    });
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoReverseScript.video")}</Label>
        <SingleRefInput
          project={project}
          accept="video/*"
          value={upload?.url ?? null}
          onChange={(url, name) => setUpload(url && name ? { url, name } : null)}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.videoReverseScript.uploadHint")}
        </p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoReverseScript.model")}</Label>
        <ModelSelect
          value={modelId}
          onChange={setModelId}
          disabled={modelsLoading}
          ariaLabel={t("tools.videoReverseScript.model")}
          placeholder={t("tools.videoReverseScript.modelDefault")}
          groups={[
            {
              options: [
                { value: "", label: t("tools.videoReverseScript.modelDefault") },
                ...models.map((m) => ({
                  value: m.id,
                  label: displayModelName(m.label, channelForDisplay(m)),
                  channel: channelForDisplay(m),
                })),
              ],
            },
          ]}
        />
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {t("tools.videoReverseScript.modelHint")}
        </p>
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoReverseScript.prompt")}</Label>
        <Textarea
          rows={2}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("tools.videoReverseScript.promptPlaceholder")}
        />
      </div>
      <SubmitButton busy={busy} disabled={!upload || modelsLoading} onClick={onSubmit}>
        {t("tools.videoReverseScript.submit")}
      </SubmitButton>
      <div className="space-y-2">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.videoReverseScript.result")}</Label>
        {latestText ? (
          <div className="space-y-2 rounded-lg border border-border bg-white/[0.02] p-3">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-sm text-foreground">
                  {latestTitle || t("tools.videoReverseScript.result")}
                </div>
                <div className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  {t("tools.videoReverseScript.charCount", { n: latestText.length })}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Button type="button" size="sm" variant="outline" onClick={onCopy}>
                  {t("tools.videoReverseScript.copy")}
                </Button>
                {latestTxtUrl ? (
                  <a
                    href={latestTxtUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  >
                    <ExternalLink className="size-3.5" />
                    {t("tools.videoReverseScript.openTxt")}
                  </a>
                ) : null}
              </div>
            </div>
            <p className="max-h-64 overflow-auto whitespace-pre-wrap text-sm leading-relaxed text-foreground">
              {latestText}
            </p>
          </div>
        ) : (
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {t("tools.videoReverseScript.resultEmpty")}
          </p>
        )}
      </div>
    </div>
  );
}

function OmniPanel({
  project,
  tool,
}: {
  project: string;
  tool: ToolKind;
}) {
  const { t } = useTranslation();
  const { busy, run } = useSubmitting();
  const def = toolByKind(tool);
  const [items, setItems] = useState<BatchItem[]>([]);
  const [model, setModel] = useState<ModelOption | null>(null);
  const [driverUrl, setDriverUrl] = useState<string | null>(null);
  const [prompt, setPrompt] = useState("");

  const isLipSync = tool === "lip-sync";
  const driverAccept = isLipSync ? "audio/*" : "image/*";
  // 三工具共用 omni 面板：文案按工具分派（对口型 = 驱动音频；换人 / 换产品 = 参考图）。
  const copy = isLipSync
    ? {
        needRef: t("tools.lipSync.needAudio"),
        modelHint: t("tools.lipSync.modelHint"),
        sources: t("tools.lipSync.bases"),
        driver: t("tools.lipSync.driverAudio"),
      }
    : tool === "replace-product"
      ? {
          needRef: t("tools.replaceProduct.needImage"),
          modelHint: t("tools.replaceProduct.modelHint"),
          sources: t("tools.replaceProduct.sources"),
          driver: t("tools.replaceProduct.driverImage"),
        }
      : {
          needRef: t("tools.replacePerson.needImage"),
          modelHint: t("tools.replacePerson.modelHint"),
          sources: t("tools.replacePerson.sources"),
          driver: t("tools.replacePerson.driverImage"),
        };

  const onSubmit = () => {
    if (items.length === 0) {
      toast.error(t("tools.common.needInput"));
      return;
    }
    if (!driverUrl) {
      toast.error(copy.needRef);
      return;
    }
    if (!model) {
      toast.error(t("tools.common.needModel"));
      return;
    }
    void run(items.length, (i) => {
      const base = items[i];
      const references = isLipSync
        ? [
            { type: base.kind === "video" ? ("video" as const) : ("image" as const), url: base.url },
            { type: "audio" as const, url: driverUrl },
          ]
        : [
            { type: "video" as const, url: base.url },
            { type: "image" as const, url: driverUrl },
          ];
      return submitFreezoneVideoOmniGen(project, {
        prompt: prompt.trim(),
        references,
        // [aipdd fork delta req42] 换人/换产品/对口型的画幅跟随源视频：面板没有画幅
        // 控件，留默认值会让竖屏素材被强行出横屏片（后端按素材探测 + 目录档位）。
        aspectRatio: "auto",
        // 画布同口径：优先目录条目 catalogId，退目录 id（后端 identifiers 都认）。
        model: model?.catalogId ?? model?.id ?? undefined,
        genMode: "allReference",
        // 工具页批量任务：产物完成后自动存入资产库「待分类资产」。
        archiveToLibrary: true,
        sourceTool: tool,
      });
    }).then((ok) => {
      if (ok) {
        setItems([]);
        setDriverUrl(null);
      }
    });
  };

  return (
    <div className="space-y-3">
      {def.modelKind === "video" ? (
        <div className="space-y-1">
          <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.model")}</Label>
          <ModelPicker
            project={project}
            kind="video"
            value={model?.id ?? null}
            onChange={setModel}
            filter={OMNI_MODEL_FILTER}
          />
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {copy.modelHint}
          </p>
        </div>
      ) : null}
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{copy.sources}</Label>
        <BatchInputArea
          project={project}
          accept={def.accept === "video-or-image" ? "video-or-image" : "video"}
          value={items}
          onChange={setItems}
        />
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{copy.driver}</Label>
        <SingleRefInput project={project} accept={driverAccept} value={driverUrl} onChange={(url) => setDriverUrl(url)} />
      </div>
      <div className="space-y-1">
        <Label className="justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase">{t("tools.common.prompt")}</Label>
        <Textarea
          rows={2}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder={t("tools.common.promptPlaceholder")}
        />
      </div>
      <SubmitButton busy={busy} disabled={items.length === 0 || !model} onClick={onSubmit}>
        {t("tools.common.submit", { n: items.length })}
      </SubmitButton>
    </div>
  );
}

export function ToolsPage({ project }: { project: string }) {
  const { t } = useTranslation();
  // 绑定取词桥给 useSubmitting 里的 toast 文案用。
  _tRef = t;
  const [active, setActive] = useState<ToolKind>("video-clone");
  // 分组入口记住各自上次选的类型：切走菜单再点回来不该被弹回默认项。
  const [lastMemberByGroup, setLastMemberByGroup] = useState<
    Partial<Record<ToolGroupId, ToolKind>>
  >({});
  const def = toolByKind(active);
  const group = def.group ? TOOL_GROUPS[def.group] : null;

  const openEntry = (kind: ToolKind) => setActive(kind);
  const pickMember = (groupId: ToolGroupId, kind: ToolKind) => {
    setLastMemberByGroup((prev) => ({ ...prev, [groupId]: kind }));
    setActive(kind);
  };

  return (
    <div className="-m-6 flex h-[calc(100%+3rem)] min-h-0 flex-col overflow-hidden bg-background">
      <header className="flex shrink-0 flex-col gap-3 border-b border-border/30 bg-background px-9 py-5">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Wrench className="size-[18px]" />
          </span>
          <div className="min-w-0">
            <h1 className="truncate py-1 font-heading text-2xl font-semibold tracking-tight text-foreground">
              {t("tools.page.title")}
            </h1>
            <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
              {t("tools.page.description")}
            </p>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-9 py-6">
        <div className="mx-auto grid max-w-6xl gap-4 md:grid-cols-[220px_1fr]">
          <nav className="flex flex-wrap gap-1 self-start rounded-[10px] border border-border bg-white/[0.02] p-1.5 md:flex-col md:flex-nowrap">
            {TOOL_NAV.map((entry) => {
              // 分组只要当前激活的类型属于它就亮着，回退到组内另一个类型不算切换入口。
              const entryActive =
                entry.type === "tool"
                  ? active === entry.kind
                  : def.group === entry.id;
              return (
                <button
                  key={entry.type === "tool" ? entry.kind : entry.id}
                  type="button"
                  onClick={() =>
                    openEntry(
                      entry.type === "tool"
                        ? entry.kind
                        : lastMemberByGroup[entry.id] ?? TOOL_GROUPS[entry.id].defaultKind,
                    )
                  }
                  className={cn(
                    "rounded-[7px] px-2.5 py-1.5 text-left text-xs font-medium transition-colors",
                    entryActive
                      ? "bg-primary/15 text-foreground"
                      : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                  )}
                >
                  {t(entry.titleKey)}
                </button>
              );
            })}
          </nav>

          <div className="space-y-4">
            <section className="rounded-[10px] border border-border bg-white/[0.02] p-4">
            <div className="mb-3">
              <h2 className="font-heading text-sm font-medium text-foreground">
                {t(group ? group.titleKey : def.titleKey)}
              </h2>
              {group ? (
                <>
                  <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                    {t(group.descKey)}
                  </p>
                  {/* 同组类型切换：入口合一，表单 / 任务过滤仍按各自的 kind 走。 */}
                  <div
                    className="mt-3 inline-flex rounded-lg border border-border bg-white/[0.02] p-0.5"
                    role="tablist"
                    aria-label={t(group.typeLabelKey)}
                  >
                    {toolGroupMembers(group.id).map((member) => (
                      <button
                        key={member}
                        type="button"
                        role="tab"
                        aria-selected={active === member}
                        onClick={() => pickMember(group.id, member)}
                        className={cn(
                          "rounded-[7px] px-3 py-1.5 text-xs font-medium transition-colors",
                          active === member
                            ? "bg-primary/15 text-primary"
                            : "text-muted-foreground hover:text-foreground",
                        )}
                      >
                        {t(toolByKind(member).titleKey)}
                      </button>
                    ))}
                  </div>
                  <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
                    {t(def.descKey)}
                  </p>
                </>
              ) : (
                <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                  {t(def.descKey)}
                </p>
              )}
            </div>
            {active === "video-clone" ? <VideoClonePanel project={project} /> : null}
            {active === "video-mix" ? <VideoMixPanel project={project} /> : null}
            {active === "whiteboard-animation" ? (
              <WhiteboardAnimationPanel project={project} />
            ) : null}
            {active === "explainer-video" ? (
              <ExplainerVideoPanel project={project} />
            ) : null}
            {active === "video-idea" ? <IdeaVideoPanel project={project} /> : null}
            {active === "voice-clone" ? <VoiceClonePanel project={project} /> : null}
            {/* [aipdd fork delta] 工具页「视频配音」。 */}
            {active === "video-dubbing" ? <VideoDubbingPanel project={project} /> : null}
            {/* [aipdd fork delta req46] 音乐创作：五模式同面板内切换。 */}
            {active === "music-create" ? <MusicCreatePanel project={project} /> : null}
            {active === "lip-sync" ? <OmniPanel project={project} tool="lip-sync" /> : null}
            {active === "replace-person" ? <OmniPanel project={project} tool="replace-person" /> : null}
            {active === "replace-product" ? <OmniPanel project={project} tool="replace-product" /> : null}
            {active === "video-watermark" ? <VideoWatermarkPanel project={project} /> : null}
            {active === "image-watermark" ? <ImageWatermarkPanel project={project} /> : null}
            {active === "media-copywrite" ? <MediaCopywritePanel project={project} /> : null}
            {active === "video-reverse-script" ? (
              <VideoReverseScriptPanel project={project} />
            ) : null}
          </section>

          <section className="rounded-[10px] border border-border bg-white/[0.02] p-4">
            <h2 className="mb-3 font-heading text-sm font-medium text-foreground">{t("tools.common.tasks")}</h2>
            <BatchTaskList project={project} kind={def.kind} />
          </section>
        </div>
        </div>
      </div>
    </div>
  );
}
