// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] L1「故事」栏目（剧集菜单第一项，位于「剧本」之前）：
// 导入整本故事（粘贴 / 上传）→ AI 诊断优化 → 一键生成多集标准剧本。
// 生成产物写入项目 uploads/（「剧本」模块的剧本库），可到「剧本」页继续预览与导入。
// 「全自动成片」（整链流水线入口）就在本卡「生成整本剧本」右侧：它是从故事起跑的
// 全流程，因此入口跟着故事走、不放在分镜页；页面上改的风格 / 集数就是整链的入参，
// 未保存的草稿在发射前自动落盘。
// 职责边界：「故事」是未处理的创作底稿；「剧本」是处理后的成稿，两模块不混。
import { type ReactNode, useEffect, useRef, useState } from "react";
import { createLazyFileRoute, useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ArrowRightLeft,
  BookOpenText,
  CheckCircle2,
  ChevronRight,
  FileText,
  Film,
  Loader2,
  RefreshCw,
  Save,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { PROJECT_SECTION_ROUTES } from "@/components/layout/project-navigation-routes";
import { PipelineControlBar } from "@/components/pipeline-control-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import { localizeFormatCheck } from "@/lib/format-check-copy";
import {
  STORY_STYLE_VALUES,
  useGenerateProjectScript,
  useGeneratedScriptEpisode,
  useGeneratedScriptResult,
  useImportProjectStory,
  useOptimizeProjectStory,
  useProjectStorySource,
  useSaveGeneratedScriptEpisode,
  useSyncGeneratedScript,
  type GeneratedScriptResult,
} from "@/lib/queries/story-project";
import { cn } from "@/lib/utils";

const MAX_EPISODES = 60;
const DEFAULT_EPISODES = 10;

// 任务状态词表与 ingest 页保持一致：活跃 / 终态。
const ACTIVE_TASK_STATUSES = new Set(["running", "queued", "submitting"]);
const FAILED_TASK_STATUSES = new Set(["failed", "stopped"]);

// 「自动推荐」在前端用哨兵值承载（base-ui Select 对空串 value 不友好），发后端时转回空串。
const STYLE_AUTO = "auto";
const STYLE_CHOICES: { value: string; i18nKey: string }[] = [
  { value: STYLE_AUTO, i18nKey: "storyPage.style.auto" },
  ...STORY_STYLE_VALUES.filter((v) => v !== "").map((v) => ({
    value: v,
    i18nKey: `storyPage.style.${v}`,
  })),
];

function StoryPage() {
  const { t } = useTranslation();
  const { project } = Route.useParams();
  const navigate = useNavigate();

  const sourceQuery = useProjectStorySource(project);
  const importStory = useImportProjectStory(project);
  const optimizeStory = useOptimizeProjectStory(project);
  const generateScript = useGenerateProjectScript(project);
  const syncScript = useSyncGeneratedScript(project);

  // null = 与服务端一致；字符串 = 本地未保存的草稿。
  const [draft, setDraft] = useState<string | null>(null);
  const [styleKey, setStyleKey] = useState<string>(STYLE_AUTO);
  const [episodeCount, setEpisodeCount] = useState<number>(DEFAULT_EPISODES);
  // 触发生成后置 true 开启轮询；看到任务终态后关闭（页面重进时按活跃状态恢复）。
  const [awaiting, setAwaiting] = useState(false);
  const [optNotes, setOptNotes] = useState<string[] | null>(null);
  // 分集预览点开的集号（null = 抽屉关闭）。
  const [selectedEpisode, setSelectedEpisode] = useState<number | null>(null);

  const source = sourceQuery.data?.data;
  const hasStory = !!source?.text;
  const textValue = draft ?? source?.text ?? "";
  const dirty = draft !== null && draft !== (source?.text ?? "");

  const resultQuery = useGeneratedScriptResult(project, true, awaiting);
  const taskData = resultQuery.data?.data;
  const taskStatus = taskData?.status ?? null;
  const taskActive = taskStatus !== null && ACTIVE_TASK_STATUSES.has(taskStatus);
  const taskFailed = taskStatus !== null && FAILED_TASK_STATUSES.has(taskStatus);
  const taskRunning = generateScript.isPending || taskActive || awaiting;

  // 任务终态 → 停轮询；进入页面时任务仍在活跃 → 恢复轮询。
  useEffect(() => {
    if (!taskStatus) return;
    if (ACTIVE_TASK_STATUSES.has(taskStatus)) {
      setAwaiting(true);
      return;
    }
    if (taskStatus === "completed" || FAILED_TASK_STATUSES.has(taskStatus)) {
      setAwaiting(false);
    }
  }, [taskStatus]);

  const handleImportFile = (file: File) => {
    importStory.mutate(
      { file },
      {
        onSuccess: () => {
          setDraft(null);
          toast.success(t("storyPage.import.success"));
        },
        onError: (error) => {
          toast.error(
            error instanceof Error && error.message
              ? error.message
              : t("storyPage.import.failed"),
          );
        },
      },
    );
  };

  const handleSaveDraft = () => {
    const text = textValue.trim();
    if (!text) {
      toast.error(t("storyPage.import.empty"));
      return;
    }
    importStory.mutate(
      { text },
      {
        onSuccess: () => {
          setDraft(null);
          toast.success(t("storyPage.source.saved"));
        },
        onError: (error) => {
          toast.error(
            error instanceof Error && error.message
              ? error.message
              : t("storyPage.source.saveFailed"),
          );
        },
      },
    );
  };

  const handleOptimize = () => {
    const text = textValue.trim();
    if (!text) {
      toast.error(t("storyPage.import.empty"));
      return;
    }
    setOptNotes(null);
    optimizeStory.mutate(
      { text, story_style: styleKey === STYLE_AUTO ? "" : styleKey },
      {
        onSuccess: (response) => {
          setDraft(response.data.story);
          setOptNotes(response.data.notes ?? []);
          toast.success(t("storyPage.optimize.done"));
        },
        onError: (error) => {
          toast.error(
            error instanceof Error && error.message
              ? error.message
              : t("storyPage.optimize.failed"),
          );
        },
      },
    );
  };

  const handleGenerate = async () => {
    const text = textValue.trim();
    if (!text) {
      toast.error(t("storyPage.import.empty"));
      return;
    }
    try {
      // 先把未保存的草稿落盘，保证生成基于最新故事内容。
      if (dirty) {
        await importStory.mutateAsync({ text });
        setDraft(null);
      }
      await generateScript.mutateAsync({
        episode_count: episodeCount,
        story_style: styleKey === STYLE_AUTO ? "" : styleKey,
      });
      setAwaiting(true);
      toast.success(t("storyPage.generate.started"));
    } catch (error) {
      toast.error(
        error instanceof Error && error.message
          ? error.message
          : t("storyPage.generate.failed"),
      );
    }
  };

  const handleSyncToScript = () => {
    syncScript.mutate(undefined, {
      onSuccess: () => {
        toast.success(t("storyPage.result.syncDone"));
        navigate({ to: PROJECT_SECTION_ROUTES.ingest, params: { project } });
      },
      onError: (error) => {
        toast.error(
          error instanceof Error && error.message
            ? error.message
            : t("storyPage.result.syncFailed"),
        );
      },
    });
  };

  // 全自动成片与本页联动：发射前先把页面上未保存的故事草稿落盘，保证整链读到的
  // 就是用户刚看到的这一份；保存失败就中止，不用旧故事静默跑一轮。
  const prepareStoryForPipeline = async (): Promise<boolean> => {
    if (!dirty) return true;
    const text = textValue.trim();
    if (!text) {
      toast.error(t("storyPage.import.empty"));
      return false;
    }
    try {
      await importStory.mutateAsync({ text });
      setDraft(null);
      return true;
    } catch (error) {
      toast.error(
        error instanceof Error && error.message
          ? error.message
          : t("storyPage.source.saveFailed"),
      );
      return false;
    }
  };

  return (
    <div className="-m-6 flex h-[calc(100%+3rem)] flex-col overflow-hidden">
      <div className="flex shrink-0 flex-col gap-3 border-b border-border/30 bg-background px-9 py-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <span className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <BookOpenText className="size-[18px]" />
          </span>
          <div className="min-w-0">
            <h1 className="truncate py-1 text-2xl font-semibold tracking-tight text-foreground">
              {t("storyPage.title")}
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
              {t("storyPage.subtitle")}
            </p>
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6 pt-10">
        <div className="mx-auto w-full max-w-[1080px] space-y-5">
          {sourceQuery.isPending ? (
            <div className="flex items-center justify-center rounded-2xl border border-white/[0.06] bg-white/[0.02] py-24">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : hasStory ? (
            <>
              <StorySourceCard
                filename={source?.filename ?? ""}
                billableChars={source?.billable_chars ?? 0}
                value={textValue}
                dirty={dirty}
                saving={importStory.isPending}
                onChange={setDraft}
                onSave={handleSaveDraft}
                onReplaceFile={handleImportFile}
                optNotes={optNotes}
                onDismissNotes={() => setOptNotes(null)}
              />
              <GenerationCard
                episodeCount={episodeCount}
                onEpisodeCountChange={setEpisodeCount}
                styleKey={styleKey}
                onStyleKeyChange={setStyleKey}
                onOptimize={handleOptimize}
                optimizing={optimizeStory.isPending}
                onGenerate={handleGenerate}
                generating={taskRunning}
                taskData={taskData ?? null}
                taskFailed={taskFailed}
                onSyncToScript={handleSyncToScript}
                syncing={syncScript.isPending}
                onOpenEpisode={setSelectedEpisode}
                afterGenerate={
                  <>
                    <span
                      className="mx-1 h-5 w-px shrink-0 bg-border"
                      aria-hidden
                    />
                    <PipelineControlBar
                      project={project}
                      storyCharCount={textValue.trim().length}
                      prepareStory={prepareStoryForPipeline}
                      busy={taskRunning}
                      defaults={{
                        storyStyle: styleKey === STYLE_AUTO ? "" : styleKey,
                        episodeCount,
                      }}
                    />
                  </>
                }
              />
            </>
          ) : (
            <ImportPanel
              importing={importStory.isPending}
              onImportFile={handleImportFile}
              onImportText={(text) =>
                importStory.mutate(
                  { text },
                  {
                    onSuccess: () =>
                      toast.success(t("storyPage.import.success")),
                    onError: (error) =>
                      toast.error(
                        error instanceof Error && error.message
                          ? error.message
                          : t("storyPage.import.failed"),
                      ),
                  },
                )
              }
            />
          )}
        </div>
      </div>
      <EpisodeDetailSheet
        project={project}
        episodeNumber={selectedEpisode}
        onClose={() => setSelectedEpisode(null)}
      />
    </div>
  );
}

// ── 未导入：空状态 + 上传/粘贴导入 ────────────────────────────────────────────

function ImportPanel({
  importing,
  onImportFile,
  onImportText,
}: {
  importing: boolean;
  onImportFile: (file: File) => void;
  onImportText: (text: string) => void;
}) {
  const { t } = useTranslation();
  const [pasteText, setPasteText] = useState("");
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] p-6">
      <div className="flex flex-col items-center text-center">
        <BookOpenText className="size-8 text-muted-foreground/60" />
        <h2 className="mt-4 text-base font-semibold text-foreground">
          {t("storyPage.import.emptyTitle")}
        </h2>
        <p className="mt-2 max-w-lg text-sm leading-6 text-muted-foreground">
          {t("storyPage.import.emptyBody")}
        </p>
      </div>

      <div
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            inputRef.current?.click();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) onImportFile(file);
        }}
        className={cn(
          "mt-6 flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed px-6 py-10 text-center transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/10",
          dragging
            ? "border-primary/40 bg-white/[0.04]"
            : "border-white/[0.12] hover:border-white/[0.2] hover:bg-white/[0.02]",
        )}
      >
        <Upload className="size-5 text-muted-foreground" />
        <p className="text-sm font-medium text-foreground">
          {t("storyPage.import.dropzoneHint")}
        </p>
        <p className="text-xs text-muted-foreground">
          {t("storyPage.import.supportedFormats")}
        </p>
        {importing && (
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="size-3 animate-spin" />
            {t("storyPage.import.importing")}
          </p>
        )}
        <input
          ref={inputRef}
          type="file"
          className="hidden"
          accept=".txt,.md,.docx"
          onChange={(e) => {
            const file = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            if (file) onImportFile(file);
          }}
        />
      </div>

      <div className="mt-6 flex items-center gap-3">
        <span className="h-px flex-1 bg-white/[0.08]" />
        <span className="text-xs text-muted-foreground">
          {t("storyPage.import.orPaste")}
        </span>
        <span className="h-px flex-1 bg-white/[0.08]" />
      </div>

      <Textarea
        value={pasteText}
        onChange={(e) => setPasteText(e.target.value)}
        placeholder={t("storyPage.import.pastePlaceholder")}
        className="mt-4 min-h-[220px] resize-y bg-white/[0.02] text-sm leading-6"
      />

      <div className="mt-4 flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground tabular-nums">
          {t("storyPage.source.charCount", { count: pasteText.length })}
        </span>
        <Button
          type="button"
          size="sm"
          disabled={importing || !pasteText.trim()}
          onClick={() => onImportText(pasteText.trim())}
        >
          {importing && <Loader2 className="size-3.5 animate-spin" />}
          {importing
            ? t("storyPage.import.importing")
            : t("storyPage.import.submit")}
        </Button>
      </div>
    </div>
  );
}

// ── 已导入：故事源编辑卡片 ────────────────────────────────────────────────────

function StorySourceCard({
  filename,
  billableChars,
  value,
  dirty,
  saving,
  onChange,
  onSave,
  onReplaceFile,
  optNotes,
  onDismissNotes,
}: {
  filename: string;
  billableChars: number;
  value: string;
  dirty: boolean;
  saving: boolean;
  onChange: (value: string) => void;
  onSave: () => void;
  onReplaceFile: (file: File) => void;
  optNotes: string[] | null;
  onDismissNotes: () => void;
}) {
  const { t } = useTranslation();
  const replaceInputRef = useRef<HTMLInputElement>(null);

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02]">
      <div className="flex flex-col gap-2 border-b border-white/[0.06] px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-center gap-2.5">
          <FileText className="size-4 shrink-0 text-muted-foreground" />
          <span className="truncate text-sm font-semibold text-foreground">
            {filename || t("storyPage.source.pasted")}
          </span>
          <Badge variant="secondary" className="tabular-nums">
            {t("storyPage.source.charCount", { count: billableChars })}
          </Badge>
          {dirty && (
            <Badge variant="outline" className="text-muted-foreground">
              {t("storyPage.source.unsaved")}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={saving}
            onClick={() => replaceInputRef.current?.click()}
          >
            <Upload className="size-3.5" />
            {t("storyPage.source.replace")}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={saving || !dirty || !value.trim()}
            onClick={onSave}
          >
            {saving ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Save className="size-3.5" />
            )}
            {saving ? t("storyPage.source.saving") : t("storyPage.source.save")}
          </Button>
          <input
            ref={replaceInputRef}
            type="file"
            className="hidden"
            accept=".txt,.md,.docx"
            onChange={(e) => {
              const file = e.currentTarget.files?.[0];
              e.currentTarget.value = "";
              if (file) onReplaceFile(file);
            }}
          />
        </div>
      </div>

      {optNotes !== null && (
        <div className="mx-5 mt-4 flex items-start gap-2 rounded-lg border border-primary/20 bg-primary/[0.05] px-3 py-2.5">
          <Sparkles className="mt-px size-3.5 shrink-0 text-primary" />
          <div className="min-w-0 flex-1 text-xs leading-5 text-foreground/80">
            <span className="font-medium">{t("storyPage.optimize.notesTitle")}</span>
            {optNotes.length > 0 ? `：${optNotes.join("；")}` : ""}
          </div>
          <button
            type="button"
            className="shrink-0 rounded p-0.5 text-muted-foreground transition-colors hover:text-foreground"
            onClick={onDismissNotes}
            aria-label={t("storyPage.optimize.dismiss")}
          >
            <X className="size-3.5" />
          </button>
        </div>
      )}

      <div className="p-5">
        <Textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="min-h-[420px] resize-y bg-white/[0.01] text-sm leading-6"
          placeholder={t("storyPage.source.placeholder")}
        />
      </div>
    </div>
  );
}

// ── 已导入：风格 / 集数 / 优化 / 生成 + 进度 + 结果 ──────────────────────────

function GenerationCard({
  episodeCount,
  onEpisodeCountChange,
  styleKey,
  onStyleKeyChange,
  onOptimize,
  optimizing,
  onGenerate,
  generating,
  taskData,
  taskFailed,
  onSyncToScript,
  syncing,
  onOpenEpisode,
  afterGenerate,
}: {
  episodeCount: number;
  onEpisodeCountChange: (value: number) => void;
  styleKey: string;
  onStyleKeyChange: (value: string) => void;
  onOptimize: () => void;
  optimizing: boolean;
  onGenerate: () => void;
  generating: boolean;
  taskData: GeneratedScriptResult | null;
  taskFailed: boolean;
  onSyncToScript: () => void;
  syncing: boolean;
  onOpenEpisode: (episodeNumber: number) => void;
  /** 「生成整本剧本」右侧的插槽：全自动成片入口住在这里（与本页风格/集数共用输入）。 */
  afterGenerate?: ReactNode;
}) {
  const { t } = useTranslation();
  const progressPercent = Math.round((taskData?.progress ?? 0) * 100);
  const taskCompleted = taskData?.status === "completed" && !!taskData.filename;
  const formatCheck = localizeFormatCheck(taskData?.format_check, t);

  return (
    <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02]">
      <div className="flex flex-col gap-4 border-b border-white/[0.06] px-5 py-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">
              {t("storyPage.generate.episodesLabel")}
            </Label>
            <Input
              type="number"
              min={1}
              max={MAX_EPISODES}
              value={episodeCount}
              disabled={generating}
              onChange={(e) => {
                const parsed = Number.parseInt(e.currentTarget.value, 10);
                onEpisodeCountChange(
                  Number.isFinite(parsed)
                    ? Math.min(MAX_EPISODES, Math.max(1, parsed))
                    : DEFAULT_EPISODES,
                );
              }}
              className="h-8 w-24 tabular-nums"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">
              {t("storyPage.styleLabel")}
            </Label>
            <Select
              value={styleKey}
              onValueChange={(value) => onStyleKeyChange(value ?? STYLE_AUTO)}
            >
              <SelectTrigger size="sm" className="min-w-36">
                <SelectValue>
                  {(value: string) =>
                    t(
                      STYLE_CHOICES.find((c) => c.value === (value || STYLE_AUTO))
                        ?.i18nKey ?? "storyPage.style.auto",
                    )
                  }
                </SelectValue>
              </SelectTrigger>
              <SelectContent alignItemWithTrigger={false} sideOffset={8}>
                {STYLE_CHOICES.map((choice) => (
                  <SelectItem key={choice.value} value={choice.value}>
                    {t(choice.i18nKey)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={optimizing || generating}
            onClick={onOptimize}
          >
            {optimizing ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Sparkles className="size-3.5" />
            )}
            {optimizing
              ? t("storyPage.optimize.running")
              : t("storyPage.optimize.label")}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={generating || optimizing}
            onClick={onGenerate}
          >
            {generating ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : (
              <Film className="size-3.5" />
            )}
            {generating
              ? t("storyPage.generate.running")
              : t("storyPage.generate.label")}
          </Button>
          {afterGenerate}
        </div>
      </div>

      <div className="px-5 py-4">
        {generating && (
          <div className="rounded-xl border border-white/[0.06] bg-white/[0.01] px-4 py-3.5">
            <div className="flex items-center justify-between gap-3 text-xs">
              <span className="flex min-w-0 items-center gap-2 text-foreground/80">
                <Loader2 className="size-3.5 shrink-0 animate-spin text-primary" />
                <span className="truncate">
                  {taskData?.current_task || t("storyPage.generate.preparing")}
                </span>
              </span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {progressPercent}%
              </span>
            </div>
            <Progress value={progressPercent} className="mt-3" />
          </div>
        )}

        {!generating && taskFailed && (
          <div className="flex items-start gap-2 rounded-xl border border-destructive/35 bg-destructive/10 px-4 py-3">
            <AlertTriangle className="mt-px size-3.5 shrink-0 text-destructive" />
            <div className="min-w-0 text-xs leading-5 text-foreground/80">
              <span className="font-medium">
                {t("storyPage.result.failedTitle")}
              </span>
              {taskData?.error ? `：${taskData.error}` : ""}
            </div>
          </div>
        )}

        {!generating && !taskFailed && taskCompleted && taskData && (
          <div className="space-y-4">
            <div className="flex flex-col gap-3 rounded-xl border border-success/30 bg-success/10 px-4 py-3.5 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex min-w-0 items-center gap-2.5">
                <CheckCircle2 className="size-4 shrink-0 text-success" />
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-foreground">
                    {t("storyPage.result.title")}
                  </p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    <span className="truncate font-mono">
                      {taskData.filename}
                    </span>
                    {taskData.episodes !== null && (
                      <Badge variant="secondary" className="tabular-nums">
                        {t("storyPage.result.episodes", {
                          count: taskData.episodes,
                        })}
                      </Badge>
                    )}
                    <span className="tabular-nums">
                      {t("storyPage.source.charCount", {
                        count: taskData.billable_chars || taskData.total_chars,
                      })}
                    </span>
                  </p>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Button type="button" variant="outline" size="sm" onClick={onGenerate}>
                  <RefreshCw className="size-3.5" />
                  {t("storyPage.result.regenerate")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  disabled={syncing}
                  onClick={onSyncToScript}
                >
                  {syncing ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <ArrowRightLeft className="size-3.5" />
                  )}
                  {syncing
                    ? t("storyPage.result.syncing")
                    : t("storyPage.result.syncToScript")}
                </Button>
              </div>
            </div>

            {formatCheck && (
              <div
                className={cn(
                  "flex items-start gap-2 rounded-lg border px-3 py-2",
                  formatCheck.level === "blocking"
                    ? "border-destructive/35 bg-destructive/10"
                    : formatCheck.level === "warning"
                      ? "border-white/10 bg-white/[0.04]"
                      : "border-success/20 bg-success/[0.06]",
                )}
              >
                {formatCheck.level === "blocking" ? (
                  <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-destructive" />
                ) : (
                  <CheckCircle2
                    className={cn(
                      "mt-0.5 size-3.5 shrink-0",
                      formatCheck.level === "warning"
                        ? "text-foreground/45"
                        : "text-success",
                    )}
                  />
                )}
                <span className="min-w-0 text-xs leading-5 text-foreground/70">
                  {formatCheck.summary}
                </span>
              </div>
            )}

            {taskData.chapter_summaries.length > 0 && (
              <div>
                <p className="mb-2 text-xs font-medium text-muted-foreground">
                  {t("storyPage.result.chapters")}
                </p>
                <ul className="max-h-72 space-y-1.5 overflow-y-auto pr-1">
                  {taskData.chapter_summaries.map((chapter) => (
                    <li key={chapter.number}>
                      <button
                        type="button"
                        onClick={() => onOpenEpisode(chapter.number)}
                        aria-label={t("storyPage.episodeDetail.open", {
                          number: chapter.number,
                        })}
                        className="w-full rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-left transition-colors hover:border-white/[0.14] hover:bg-white/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/10"
                      >
                        <div className="flex items-center gap-2">
                          <Badge
                            variant="secondary"
                            className="shrink-0 tabular-nums"
                          >
                            {t("storyPage.result.episodeNo", {
                              number: chapter.number,
                            })}
                          </Badge>
                          <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                            {chapter.title}
                          </span>
                          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                            {t("storyPage.source.charCount", {
                              count: chapter.word_count,
                            })}
                          </span>
                          <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
                        </div>
                        {chapter.preview && (
                          <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted-foreground/80">
                            {chapter.preview}
                          </p>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {!generating && !taskFailed && !taskCompleted && (
          <p className="text-xs leading-5 text-muted-foreground">
            {t("storyPage.generate.hint", { max: MAX_EPISODES })}
          </p>
        )}
      </div>
    </div>
  );
}

// ── 分集详情抽屉：查看整集正文 + 编辑保存 ─────────────────────────────────────

function EpisodeDetailSheet({
  project,
  episodeNumber,
  onClose,
}: {
  project: string;
  episodeNumber: number | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const detailQuery = useGeneratedScriptEpisode(project, episodeNumber);
  const saveEpisode = useSaveGeneratedScriptEpisode(project);
  // null = 展示服务端正文；字符串 = 本地未保存的编辑草稿。
  const [draft, setDraft] = useState<string | null>(null);

  const detail = detailQuery.data?.data;

  // 切换集号 / 关闭抽屉时复位草稿。
  useEffect(() => {
    setDraft(null);
  }, [episodeNumber]);

  const bodyValue = draft ?? detail?.body ?? "";
  const dirty = draft !== null && detail != null && draft !== detail.body;

  const handleSave = () => {
    if (episodeNumber === null) return;
    const text = bodyValue.trim();
    if (!text) {
      toast.error(t("storyPage.episodeDetail.empty"));
      return;
    }
    saveEpisode.mutate(
      { episodeNumber, body: text },
      {
        onSuccess: () => {
          setDraft(null);
          toast.success(t("storyPage.episodeDetail.saved"));
        },
        onError: (error) => {
          toast.error(
            error instanceof Error && error.message
              ? error.message
              : t("storyPage.episodeDetail.saveFailed"),
          );
        },
      },
    );
  };

  return (
    <Sheet
      open={episodeNumber !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <SheetContent
        side="right"
        className="data-[side=right]:w-full border-border/50 sm:!max-w-[640px]"
      >
        <SheetHeader className="border-b border-border/50 px-6 py-5">
          <SheetTitle>
            {t("storyPage.episodeDetail.title", { number: episodeNumber ?? 0 })}
          </SheetTitle>
          <SheetDescription className="truncate">
            {detail?.title || t("storyPage.episodeDetail.subtitle")}
          </SheetDescription>
        </SheetHeader>
        <ScrollArea className="min-h-0 flex-1">
          <div className="px-6 pb-6 pt-2">
            {detailQuery.isPending ? (
              <div className="flex items-center justify-center py-24">
                <Loader2 className="size-5 animate-spin text-muted-foreground" />
              </div>
            ) : !detail ? (
              <div className="flex items-start gap-2 rounded-lg border border-destructive/35 bg-destructive/10 px-4 py-3 text-xs leading-5 text-foreground/80">
                <AlertTriangle className="mt-px size-3.5 shrink-0 text-destructive" />
                <span>
                  {t("storyPage.episodeDetail.loadFailed")}
                  {detailQuery.error instanceof Error &&
                  detailQuery.error.message
                    ? `：${detailQuery.error.message}`
                    : ""}
                </span>
              </div>
            ) : (
              <>
                <p className="mb-2 text-xs text-muted-foreground">
                  {t("storyPage.episodeDetail.editHint")}
                </p>
                <Textarea
                  value={bodyValue}
                  disabled={saveEpisode.isPending}
                  onChange={(e) => setDraft(e.target.value)}
                  className="min-h-[420px] resize-y bg-white/[0.01] text-sm leading-6"
                />
              </>
            )}
          </div>
        </ScrollArea>
        <div className="flex items-center justify-between gap-3 border-t border-border/50 px-6 py-4">
          <span className="text-xs tabular-nums text-muted-foreground">
            {detail ? t("storyPage.source.charCount", { count: bodyValue.length }) : ""}
          </span>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" onClick={onClose}>
              {t("storyPage.episodeDetail.close")}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={saveEpisode.isPending || !dirty || !bodyValue.trim()}
              onClick={handleSave}
            >
              {saveEpisode.isPending ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Save className="size-3.5" />
              )}
              {saveEpisode.isPending
                ? t("storyPage.source.saving")
                : t("storyPage.episodeDetail.save")}
            </Button>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

export const Route = createLazyFileRoute("/_app/projects/$project/story")({
  component: StoryPage,
});