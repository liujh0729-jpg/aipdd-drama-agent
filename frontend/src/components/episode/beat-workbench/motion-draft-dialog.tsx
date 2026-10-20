// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";

import {
  listFreezoneMotionRenders,
  submitFreezoneMotionDraft,
  type FreezoneMotionDraftClip,
  type FreezoneMotionDraftSubtitle,
  type FreezoneMotionRenderClip,
} from "@/api/ops";
import { awaitTaskCompletion } from "@/api/tasks";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { GLASS_DIALOG_CONTENT_CLASS, TRANSPARENT_DIALOG_FOOTER_CLASS } from "@/lib/dialog-styles";
import { backendErrorToastMessage } from "@/lib/api-errors";
import { useFreezoneAssetLibrary } from "@/lib/queries/freezone";
import { useProjectAspectRatio } from "@/stores/aspect-ratio-store";
import { cn } from "@/lib/utils";
import type { Beat } from "@/types/episode";

/**
 * [aipdd fork delta] 动效大片交付：把本集已渲染的 MG 成片导出成一盘剪映 10.2 草稿。
 *
 * 镜头清单来自磁盘上的渲染目录（不是任务记录）：任务会过期被清、资产库条目名还能改，
 * 只有落盘的成片 + 同名 sidecar 是稳定事实。旁白的源头是分镜自己的配音（逐镜）或
 * 资产库音频（整片），两者后端互斥，这里就用一个开关把它们做成二选一，不给提交出
 * 一条剪映打不开的时间轴的机会。
 */

const NONE_SENTINEL = "__none__";
/** 有旁白时给旁白让位的 BGM 音量——后端字段说明里的常用档位（1 为原音量）。 */
const BGM_GAIN_UNDER_NARRATION = 0.2;

type NarrationMode = "none" | "per_beat" | "film";
type CaptionMode = "none" | "per_beat" | "rows";
type CaptionPosition = "top" | "middle" | "bottom";

/**
 * 字幕位置档：写的是「字幕中心距画布顶部占多高」，后端换算成剪映的 transform.y。
 * 0.75 是竖屏下三分之一安全区——再往下会被手机底部的进度条压住。
 */
const CAPTION_Y: Record<CaptionPosition, number> = { top: 0.2, middle: 0.5, bottom: 0.75 };
const CAPTION_SIZE_MIN = 1;
const CAPTION_SIZE_MAX = 40;
const DEFAULT_CAPTION_SIZE = 7;

/** 一条手写字幕：数字先用字符串存着，空输入框不该被强制成 0。 */
interface CaptionRowDraft {
  id: string;
  text: string;
  start: string;
  duration: string;
}

interface MotionDraftDialogProps {
  open: boolean;
  onOpenChange: (value: boolean) => void;
  project: string;
  episode: number;
  beats: Beat[];
  /**
   * 草稿默认名。分镜台按集号自动起名，工具页的成片不属于某一集（列的是整个项目的
   * 渲染目录），由宿主给一个不带集号的默认名，免得写出「第 0 集」这种看不懂的标题。
   */
  defaultName?: string;
}

function clipKey(clip: FreezoneMotionRenderClip): string {
  return clip.url;
}

/** 同一镜重渲过多次：默认只选最新那一条（清单已按落盘时间升序返回）。 */
function defaultSelection(clips: FreezoneMotionRenderClip[]): string[] {
  const newest = new Map<string, string>();
  for (const clip of clips) {
    // 缺时长（sidecar 没了）的成片落轨会变成 0 长度，后端会直接拒，默认不选它。
    if (!clip.durationSeconds || clip.durationSeconds <= 0) continue;
    const group = clip.beatNumber > 0 ? `beat-${clip.beatNumber}` : clip.url;
    newest.set(group, clipKey(clip));
  }
  return [...newest.values()];
}

export function MotionDraftDialog({
  open,
  onOpenChange,
  project,
  episode,
  beats,
  defaultName: defaultNameProp,
}: MotionDraftDialogProps) {
  const { t } = useTranslation();
  const { orientation } = useProjectAspectRatio(project);
  const [clips, setClips] = useState<FreezoneMotionRenderClip[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [reloadTick, setReloadTick] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [narrationMode, setNarrationMode] = useState<NarrationMode>("none");
  const [filmNarrationId, setFilmNarrationId] = useState(NONE_SENTINEL);
  const [bgmId, setBgmId] = useState(NONE_SENTINEL);
  const [captionMode, setCaptionMode] = useState<CaptionMode>("none");
  const [captionRows, setCaptionRows] = useState<CaptionRowDraft[]>([]);
  const [captionSize, setCaptionSize] = useState(String(DEFAULT_CAPTION_SIZE));
  const [captionPosition, setCaptionPosition] = useState<CaptionPosition>("bottom");
  const [deploy, setDeploy] = useState(true);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const audioAssets = useFreezoneAssetLibrary(project, open);
  const audioItems = useMemo(
    () => (audioAssets.data ?? []).filter((item) => item.media === "audio" && item.url),
    [audioAssets.data],
  );
  // 老条目可能没有 id（资产库列表允许），所以键兜到序号，保证 Select 有稳定 value。
  const audioEntries = useMemo(
    () => audioItems.map((item, index) => ({ key: item.id ?? `index-${index}`, item })),
    [audioItems],
  );
  const audioOf = (key: string) => audioEntries.find((entry) => entry.key === key)?.item;

  const beatByNumber = useMemo(() => {
    const map = new Map<number, Beat>();
    for (const beat of beats) map.set(beat.beat_number, beat);
    return map;
  }, [beats]);

  const ordered = useMemo(
    () => [...clips].sort((a, b) => (a.beatNumber || 1e9) - (b.beatNumber || 1e9)),
    [clips],
  );

  // 打开时读清单：渲染目录里有什么就是什么，不靠前端记住刚才渲了哪几镜。
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setIsLoading(true);
    setLoadError("");
    listFreezoneMotionRenders(project, episode)
      .then((items) => {
        if (cancelled) return;
        setClips(items);
        setSelected(defaultSelection(items));
      })
      .catch((error) => {
        if (cancelled) return;
        setClips([]);
        setSelected([]);
        setLoadError(backendErrorToastMessage(error, t));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, project, episode, reloadTick, t]);

  const defaultName =
    defaultNameProp?.trim() || t("episode.beat.motion.draft.defaultName", { episode });
  useEffect(() => {
    if (open) setName((current) => current || defaultName);
  }, [open, defaultName]);

  const narrationOf = (clip: FreezoneMotionRenderClip): string => {
    const beat = beatByNumber.get(clip.beatNumber);
    return (beat?.audio_url ?? "").trim();
  };
  const hasPerBeatNarration = ordered.some((clip) => Boolean(narrationOf(clip)));

  const narrationOptions = useMemo(() => {
    const options: NarrationMode[] = ["none"];
    if (hasPerBeatNarration) options.push("per_beat");
    if (audioItems.length > 0) options.push("film");
    return options;
  }, [hasPerBeatNarration, audioItems.length]);

  // 只剩一种可选时不把开关摆出来（否则是一排只有一个选项的切换器），直接用它。
  const activeNarrationMode: NarrationMode =
    narrationOptions.length === 1
      ? narrationOptions[0]
      : narrationOptions.includes(narrationMode)
        ? narrationMode
        : "none";

  useEffect(() => {
    if (open && hasPerBeatNarration) setNarrationMode("per_beat");
  }, [open, hasPerBeatNarration]);

  /**
   * 字幕三档：不给、用每一镜自己的台词（旁白那段文本，不再另开输入框让用户重打一遍）、
   * 或整片逐条手写。后端只认「逐镜 caption」与「整片 subtitles」其中一种，同给会被拒——
   * 这里用一档开关把它们做成互斥，不给提交出一条剪映打不开的文字轨的机会。
   */
  const captionOf = (clip: FreezoneMotionRenderClip): string =>
    (beatByNumber.get(clip.beatNumber)?.narration_segment ?? "").trim();
  const hasPerBeatCaption = ordered.some((clip) => Boolean(captionOf(clip)));

  const captionOptions = useMemo(() => {
    const options: CaptionMode[] = ["none"];
    if (hasPerBeatCaption) options.push("per_beat");
    options.push("rows");
    return options;
  }, [hasPerBeatCaption]);

  const activeCaptionMode: CaptionMode = captionOptions.includes(captionMode)
    ? captionMode
    : "none";

  const rowSeqRef = useRef(0);
  const addCaptionRow = () => {
    setCaptionRows((current) => {
      const last = current[current.length - 1];
      // 新行默认接在上一条后面：写字幕时只想写文字，不想每加一条都重算起点。
      const start = last
        ? String((Number(last.start) || 0) + (Number(last.duration) || 0))
        : "0";
      rowSeqRef.current += 1;
      return [...current, { id: `row-${rowSeqRef.current}`, text: "", start, duration: "" }];
    });
  };
  const patchCaptionRow = (id: string, patch: Partial<CaptionRowDraft>) => {
    setCaptionRows((current) =>
      current.map((row) => (row.id === id ? { ...row, ...patch } : row)),
    );
  };
  const removeCaptionRow = (id: string) => {
    setCaptionRows((current) => current.filter((row) => row.id !== id));
  };

  const captionSizeValue = (() => {
    const parsed = Number(captionSize);
    if (!Number.isFinite(parsed) || parsed <= 0) return DEFAULT_CAPTION_SIZE;
    return Math.min(CAPTION_SIZE_MAX, Math.max(CAPTION_SIZE_MIN, parsed));
  })();
  const captionRowsOf = (): FreezoneMotionDraftSubtitle[] =>
    captionRows
      .filter((row) => row.text.trim())
      .map((row) => {
        const duration = Number(row.duration);
        return {
          text: row.text.trim(),
          startS: Number.isFinite(Number(row.start)) && Number(row.start) > 0 ? Number(row.start) : 0,
          durationS: Number.isFinite(duration) && duration > 0 ? duration : null,
        };
      });

  const chosen = useMemo(
    () => ordered.filter((clip) => selected.includes(clipKey(clip))),
    [ordered, selected],
  );
  const totalSeconds = chosen.reduce((sum, clip) => sum + (clip.durationSeconds ?? 0), 0);
  const totalBeats = chosen.reduce((sum, clip) => sum + clip.beats.length, 0);
  const leader = chosen[0];
  const aspectRatio =
    leader?.aspectRatio || (orientation === "portrait" ? "9:16" : "16:9");
  const canvasResolution =
    leader && Math.min(leader.width || 0, leader.height || 0) >= 1080 ? "1080p" : "720p";
  const canvasFps = leader?.fps && leader.fps > 0 ? leader.fps : 30;

  // 选了「整片旁白」却没落到具体素材时，用资产库第一条——开关已经表达了意图，
  // 不该再有一个「不选」让提交出来一条没有旁白的时间轴。
  const activeFilmKey = audioOf(filmNarrationId)
    ? filmNarrationId
    : (audioEntries[0]?.key ?? NONE_SENTINEL);
  const activeFilmNarrationUrl =
    activeNarrationMode === "film" ? (audioOf(activeFilmKey)?.url ?? "") : "";
  const bgmUrl = bgmId === NONE_SENTINEL ? "" : (audioOf(bgmId)?.url ?? "");
  const hasNarration =
    activeNarrationMode === "per_beat" || Boolean(activeFilmNarrationUrl);

  const toggle = (key: string) => {
    setSelected((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  };

  const handleSubmit = async () => {
    if (chosen.length === 0) return;
    setIsSubmitting(true);
    try {
      const clipPayload: FreezoneMotionDraftClip[] = chosen.map((clip) => {
        const vo = activeNarrationMode === "per_beat" ? narrationOf(clip) : "";
        return {
          source: clip.url,
          name: clip.name,
          durationS: clip.durationSeconds,
          beats: clip.beats,
          // 不传时长：后端按该镜时长对齐，这正是「逐镜旁白不会压到下一镜」的保证。
          narration: vo ? { source: vo } : null,
          // 逐镜字幕只在这档开时给，否则后端会当成「两种给法同给」拒掉整单。
          caption: activeCaptionMode === "per_beat" ? captionOf(clip) : "",
        };
      });
      const ref = await submitFreezoneMotionDraft(project, {
        title: name.trim() || defaultName,
        clips: clipPayload,
        narration: activeFilmNarrationUrl ? { source: activeFilmNarrationUrl } : null,
        bgm: bgmUrl
          ? { source: bgmUrl, gain: hasNarration ? BGM_GAIN_UNDER_NARRATION : 1.0 }
          : null,
        subtitles: activeCaptionMode === "rows" ? captionRowsOf() : [],
        textStyle:
          activeCaptionMode === "none"
            ? null
            : { size: captionSizeValue, bold: true, yRatio: CAPTION_Y[captionPosition] },
        aspectRatio,
        resolution: canvasResolution,
        fps: canvasFps,
        deploy,
      });
      const runningId = toast.info(
        t("episode.beat.motion.draft.running", { name: name.trim() || defaultName }),
      );
      const completed = await awaitTaskCompletion(ref.task_key, project, {
        taskType: ref.task_type,
      });
      if (completed.status === "failed") {
        toast.error(
          completed.error || t("episode.beat.motion.draft.failed"),
          { id: runningId },
        );
        return;
      }
      const result = (completed.result ?? {}) as Record<string, unknown>;
      const draftDir = typeof result.draft_dir === "string" ? result.draft_dir : "";
      const deployedDir = typeof result.deployed_dir === "string" ? result.deployed_dir : "";
      toast.success(
        t("episode.beat.motion.draft.done", { name: name.trim() || defaultName }),
        {
          id: runningId,
          duration: 15_000,
          description: deployedDir
            ? t("episode.beat.motion.draft.deployed", { dir: deployedDir })
            : t("episode.beat.motion.draft.saved", { dir: draftDir }),
        },
      );
      onOpenChange(false);
    } catch (error) {
      toast.error(backendErrorToastMessage(error, t));
    } finally {
      setIsSubmitting(false);
    }
  };

  const empty = !isLoading && !loadError && ordered.length === 0;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn(GLASS_DIALOG_CONTENT_CLASS, "ring-white/10 sm:max-w-3xl")}>
        <DialogHeader>
          <DialogTitle>{t("episode.beat.motion.draft.title")}</DialogTitle>
        </DialogHeader>

        <div className="grid max-h-[70vh] min-w-0 gap-5 overflow-y-auto pr-1">
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("episode.beat.motion.draft.subtitle")}
          </p>

          {isLoading ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              {t("episode.beat.motion.draft.loading")}
            </div>
          ) : loadError ? (
            <div className="grid gap-2 py-4">
              <p className="text-xs leading-relaxed text-destructive">{loadError}</p>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() => setReloadTick((tick) => tick + 1)}
              >
                <RefreshCw className="size-3.5" />
                {t("episode.beat.motion.draft.retry")}
              </Button>
            </div>
          ) : empty ? (
            <p className="py-6 text-sm leading-relaxed text-muted-foreground">
              {t("episode.beat.motion.draft.empty")}
            </p>
          ) : (
            <>
              <div className="min-w-0 overflow-hidden rounded-[12px] border border-white/10">
                <div className="grid grid-cols-[28px_44px_minmax(0,1fr)_64px_52px_72px] items-center gap-2 border-b border-white/10 bg-black/20 px-3 py-2 text-xs font-medium text-foreground">
                  <span aria-hidden />
                  <span>{t("episode.beat.motion.draft.colBeat")}</span>
                  <span>{t("episode.beat.motion.draft.colClip")}</span>
                  <span>{t("episode.beat.motion.draft.colDuration")}</span>
                  <span>{t("episode.beat.motion.draft.colBeats")}</span>
                  <span>{t("episode.beat.motion.draft.colNarration")}</span>
                </div>
                {ordered.map((clip) => {
                  const key = clipKey(clip);
                  const checked = selected.includes(key);
                  const narration = narrationOf(clip);
                  const beat = beatByNumber.get(clip.beatNumber);
                  const audioSeconds =
                    typeof beat?.audio_duration_seconds === "number"
                      ? beat.audio_duration_seconds
                      : null;
                  const tooLong =
                    activeNarrationMode === "per_beat" &&
                    Boolean(narration) &&
                    audioSeconds !== null &&
                    clip.durationSeconds !== null &&
                    audioSeconds > clip.durationSeconds + 0.2;
                  const missingMeta = !clip.durationSeconds || clip.durationSeconds <= 0;
                  return (
                    <label
                      key={key}
                      className="grid cursor-pointer grid-cols-[28px_44px_minmax(0,1fr)_64px_52px_72px] items-center gap-2 border-b border-white/[0.06] px-3 py-2 text-xs last:border-b-0 hover:bg-white/[0.03]"
                    >
                      <Checkbox
                        checked={checked}
                        onCheckedChange={() => toggle(key)}
                      />
                      <span className="tabular-nums text-muted-foreground">
                        {clip.beatNumber > 0 ? clip.beatNumber : "—"}
                      </span>
                      <span className="min-w-0 truncate text-foreground">{clip.name}</span>
                      {missingMeta ? (
                        <span className="text-warning">{t("episode.beat.motion.draft.noMeta")}</span>
                      ) : (
                        <span className="tabular-nums text-muted-foreground">
                          {clip.durationSeconds?.toFixed(1)}s
                        </span>
                      )}
                      <span className="tabular-nums text-muted-foreground">
                        {clip.beats.length || "—"}
                      </span>
                      <span
                        className={cn(
                          "truncate",
                          tooLong ? "text-warning" : "text-muted-foreground",
                        )}
                        title={
                          tooLong
                            ? t("episode.beat.motion.draft.narrationLong", {
                                audio: audioSeconds?.toFixed(1),
                                clip: clip.durationSeconds?.toFixed(1),
                              })
                            : undefined
                        }
                      >
                        {tooLong
                          ? t("episode.beat.motion.draft.narrationLongShort")
                          : narration
                            ? t("episode.beat.motion.draft.narrationReady")
                            : t("episode.beat.motion.draft.narrationNone")}
                      </span>
                    </label>
                  );
                })}
              </div>

              <p className="text-xs leading-relaxed text-muted-foreground">
                {/* 变量名不叫 count：i18next 见到 count 会去找 summary_one/summary_other
                    的复数形式，只写 summary 时英文等语言会直接吐原始 key。 */}
                {/* 两段各占一行：合成的口径不同（选了什么 vs 画布按领头镜推断），
                    挤在一行里读起来像一长串数字。 */}
                <span>
                  {t("episode.beat.motion.draft.summary", {
                    shots: chosen.length,
                    seconds: totalSeconds.toFixed(1),
                    beatMarks: totalBeats,
                  })}
                </span>
                {chosen.length > 0 ? (
                  <span className="block">
                    {t("episode.beat.motion.draft.canvas", {
                      ratio: aspectRatio,
                      resolution: canvasResolution,
                      fps: canvasFps,
                    })}
                  </span>
                ) : null}
              </p>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="min-w-0 space-y-2">
                  <Label htmlFor="motion-draft-name">
                    {t("episode.beat.motion.draft.fieldName")}
                  </Label>
                  <Input
                    id="motion-draft-name"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder={defaultName}
                    className="h-8 !rounded-[8px] !border-white/10 bg-white/[0.025] text-sm focus-visible:!border-primary/45 focus-visible:!ring-0"
                  />
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    {t("episode.beat.motion.draft.fieldNameHint")}
                  </p>
                </div>

                {narrationOptions.length > 1 ? (
                  <div className="min-w-0 space-y-2">
                    <Label>{t("episode.beat.motion.draft.fieldNarration")}</Label>
                    <div
                      className="grid grid-cols-3 gap-1 rounded-[8px] border border-white/10 bg-white/[0.025] p-1"
                      role="group"
                      aria-label={t("episode.beat.motion.draft.fieldNarration")}
                    >
                      {narrationOptions.map((value) => (
                        <Button
                          key={value}
                          type="button"
                          variant={activeNarrationMode === value ? "secondary" : "ghost"}
                          size="sm"
                          className="h-7 rounded-[6px] px-1 text-xs"
                          onClick={() => setNarrationMode(value)}
                        >
                          {t(`episode.beat.motion.draft.narration.${value}`)}
                        </Button>
                      ))}
                    </div>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {t(`episode.beat.motion.draft.narrationHint.${activeNarrationMode}`)}
                    </p>
                  </div>
                ) : null}
              </div>

              {activeNarrationMode === "film" && audioEntries.length > 0 ? (
                <div className="min-w-0 space-y-2">
                  <Label>{t("episode.beat.motion.draft.fieldFilmNarrationAsset")}</Label>
                  <Select
                    value={activeFilmKey}
                    onValueChange={(value) => setFilmNarrationId(value ?? NONE_SENTINEL)}
                  >
                    <SelectTrigger
                      aria-label={t("episode.beat.motion.draft.fieldFilmNarrationAsset")}
                      className="h-8 w-full text-sm !rounded-[8px] !border-white/10 bg-white/[0.025]"
                    >
                      <SelectValue>{audioOf(activeFilmKey)?.name ?? ""}</SelectValue>
                    </SelectTrigger>
                    <SelectContent align="start" alignItemWithTrigger={false} className="max-h-72 p-1">
                      {audioEntries.map((entry) => (
                        <SelectItem key={entry.key} value={entry.key} className="py-1.5 text-sm">
                          {entry.item.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              ) : null}

              {captionOptions.length > 1 ? (
                <div className="min-w-0 space-y-2">
                  <Label>{t("episode.beat.motion.draft.fieldCaption")}</Label>
                  <div
                    className="grid grid-cols-3 gap-1 rounded-[8px] border border-white/10 bg-white/[0.025] p-1"
                    role="group"
                    aria-label={t("episode.beat.motion.draft.fieldCaption")}
                  >
                    {captionOptions.map((value) => (
                      <Button
                        key={value}
                        type="button"
                        variant={activeCaptionMode === value ? "secondary" : "ghost"}
                        size="sm"
                        className="h-7 rounded-[6px] px-1 text-xs"
                        onClick={() => setCaptionMode(value)}
                      >
                        {t(`episode.beat.motion.draft.caption.${value}`)}
                      </Button>
                    ))}
                  </div>
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    {t(`episode.beat.motion.draft.captionHint.${activeCaptionMode}`)}
                  </p>
                </div>
              ) : null}

              {activeCaptionMode === "rows" ? (
                <div className="min-w-0 space-y-2">
                  <div className="grid grid-cols-[minmax(0,1fr)_88px_88px_28px] items-center gap-2 px-0.5 text-xs font-medium text-foreground">
                    <span>{t("episode.beat.motion.draft.colCaptionText")}</span>
                    <span>{t("episode.beat.motion.draft.colCaptionStart")}</span>
                    <span>{t("episode.beat.motion.draft.colCaptionDuration")}</span>
                    <span aria-hidden />
                  </div>
                  {captionRows.map((row) => (
                    <div
                      key={row.id}
                      className="grid grid-cols-[minmax(0,1fr)_88px_88px_28px] items-center gap-2"
                    >
                      <Input
                        value={row.text}
                        onChange={(event) => patchCaptionRow(row.id, { text: event.target.value })}
                        aria-label={t("episode.beat.motion.draft.colCaptionText")}
                        className="h-8 !rounded-[8px] !border-white/10 bg-white/[0.025] text-sm focus-visible:!border-primary/45 focus-visible:!ring-0"
                      />
                      <Input
                        value={row.start}
                        inputMode="decimal"
                        onChange={(event) => patchCaptionRow(row.id, { start: event.target.value })}
                        aria-label={t("episode.beat.motion.draft.colCaptionStart")}
                        className="h-8 !rounded-[8px] !border-white/10 bg-white/[0.025] text-right text-sm tabular-nums focus-visible:!border-primary/45 focus-visible:!ring-0"
                      />
                      <Input
                        value={row.duration}
                        inputMode="decimal"
                        placeholder={t("episode.beat.motion.draft.captionDurationAuto")}
                        onChange={(event) =>
                          patchCaptionRow(row.id, { duration: event.target.value })
                        }
                        aria-label={t("episode.beat.motion.draft.colCaptionDuration")}
                        className="h-8 !rounded-[8px] !border-white/10 bg-white/[0.025] text-right text-sm tabular-nums placeholder:text-muted-foreground/60 focus-visible:!border-primary/45 focus-visible:!ring-0"
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t("episode.beat.motion.draft.captionRemove")}
                        className="text-muted-foreground hover:text-destructive"
                        onClick={() => removeCaptionRow(row.id)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  ))}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="w-fit"
                    onClick={addCaptionRow}
                  >
                    <Plus className="size-3.5" />
                    {t("episode.beat.motion.draft.captionAdd")}
                  </Button>
                </div>
              ) : null}

              {activeCaptionMode !== "none" ? (
                <div className="grid gap-4 md:grid-cols-2">
                  <div className="min-w-0 space-y-2">
                    <Label htmlFor="motion-draft-caption-size">
                      {t("episode.beat.motion.draft.fieldCaptionSize")}
                    </Label>
                    <Input
                      id="motion-draft-caption-size"
                      value={captionSize}
                      inputMode="decimal"
                      onChange={(event) => setCaptionSize(event.target.value)}
                      className="h-8 !rounded-[8px] !border-white/10 bg-white/[0.025] text-sm focus-visible:!border-primary/45 focus-visible:!ring-0"
                    />
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {t("episode.beat.motion.draft.captionSizeHint", {
                        min: CAPTION_SIZE_MIN,
                        max: CAPTION_SIZE_MAX,
                        size: DEFAULT_CAPTION_SIZE,
                      })}
                    </p>
                  </div>
                  <div className="min-w-0 space-y-2">
                    <Label>{t("episode.beat.motion.draft.fieldCaptionPosition")}</Label>
                    <div
                      className="grid grid-cols-3 gap-1 rounded-[8px] border border-white/10 bg-white/[0.025] p-1"
                      role="group"
                      aria-label={t("episode.beat.motion.draft.fieldCaptionPosition")}
                    >
                      {(["top", "middle", "bottom"] as const).map((value) => (
                        <Button
                          key={value}
                          type="button"
                          variant={captionPosition === value ? "secondary" : "ghost"}
                          size="sm"
                          className="h-7 rounded-[6px] px-1 text-xs"
                          onClick={() => setCaptionPosition(value)}
                        >
                          {t(`episode.beat.motion.draft.captionPosition.${value}`)}
                        </Button>
                      ))}
                    </div>
                    <p className="text-xs leading-relaxed text-muted-foreground">
                      {t("episode.beat.motion.draft.captionPositionHint")}
                    </p>
                  </div>
                </div>
              ) : null}

              {audioEntries.length > 0 ? (
                <div className="min-w-0 space-y-2">
                  <Label>{t("episode.beat.motion.draft.fieldBgm")}</Label>
                  <Select
                    value={bgmId}
                    onValueChange={(value) => setBgmId(value ?? NONE_SENTINEL)}
                  >
                    <SelectTrigger
                      aria-label={t("episode.beat.motion.draft.fieldBgm")}
                      className="h-8 w-full text-sm !rounded-[8px] !border-white/10 bg-white/[0.025]"
                    >
                      <SelectValue>
                        {bgmId === NONE_SENTINEL
                          ? t("episode.beat.motion.draft.bgmNone")
                          : (audioOf(bgmId)?.name ?? t("episode.beat.motion.draft.bgmNone"))}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent align="start" alignItemWithTrigger={false} className="max-h-72 p-1">
                      <SelectItem value={NONE_SENTINEL} className="py-1.5 text-sm">
                        {t("episode.beat.motion.draft.bgmNone")}
                      </SelectItem>
                      {audioEntries.map((entry) => (
                        <SelectItem key={entry.key} value={entry.key} className="py-1.5 text-sm">
                          {entry.item.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs leading-relaxed text-muted-foreground">
                    {t("episode.beat.motion.draft.bgmHint", {
                      gain: BGM_GAIN_UNDER_NARRATION,
                    })}
                  </p>
                </div>
              ) : null}

              <label className="flex w-fit cursor-pointer items-start gap-2">
                <Checkbox
                  checked={deploy}
                  onCheckedChange={(value) => setDeploy(value === true)}
                  className="mt-0.5"
                />
                <span className="min-w-0">
                  <span className="text-sm font-medium text-foreground">
                    {t("episode.beat.motion.draft.fieldDeploy")}
                  </span>
                  <span className="block text-xs leading-relaxed text-muted-foreground">
                    {t("episode.beat.motion.draft.deployHint")}
                  </span>
                </span>
              </label>
            </>
          )}
        </div>

        <DialogFooter className={TRANSPARENT_DIALOG_FOOTER_CLASS}>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
            {t("common.cancel")}
          </Button>
          <Button onClick={() => void handleSubmit()} disabled={isSubmitting || chosen.length === 0}>
            {isSubmitting ? <Loader2 className="mr-1.5 size-3.5 animate-spin" /> : null}
            {t("episode.beat.motion.draft.submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
