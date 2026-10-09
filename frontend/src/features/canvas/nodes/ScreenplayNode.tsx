// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 需求：画布「剧本」组件——把剧集剧本管线（上传剧本 / 故事→剧本
// / 由剧本生成角色·场景·道具·分镜）编排到画布节点上。全部复用既有项目管线 hooks，
// 产物写回项目库后剧集页/资产页自动同步（task-center 已对这些任务类型做失效刷新）。
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from '@xyflow/react';
import {
  ArrowRightLeft,
  Box,
  CheckCircle2,
  Clapperboard,
  FileText,
  Layers,
  Loader2,
  Map as MapIcon,
  ScrollText,
  Sparkles,
  Upload,
  Users,
} from 'lucide-react';

import {
  CANVAS_NODE_TYPES,
  type ScreenplayNodeData,
  type ScreenplayNodeMode,
} from '@/features/canvas/domain/canvasNodes';
import { localizeNodeDisplayName } from '@/features/canvas/domain/nodeDisplay';
import {
  NodeHeader,
  NODE_HEADER_FLOATING_POSITION_CLASS,
} from '@/features/canvas/ui/NodeHeader';
import { NodeResizeHandle } from '@/features/canvas/ui/NodeResizeHandle';
import {
  CANVAS_NODE_PANEL_SURFACE_CLASS,
  canvasNodeFrameClass,
} from '@/features/canvas/ui/nodeFrameStyles';
import { useCanvasStore } from '@/stores/canvasStore';
import { PROJECT_SECTION_ROUTES } from '@/components/layout/project-navigation-routes';
import { STORY_STYLE_VALUES, useGeneratedScriptResult, useGenerateProjectScript, useImportProjectStory, useProjectStorySource, useSyncGeneratedScript } from '@/lib/queries/story-project';
import { useBuildCharacters } from '@/lib/queries/characters';
import { usePlanEpisodes, usePlanEpisodeProps, usePlanEpisodeScenes } from '@/lib/queries/episodes';
import { useGenerateScript } from '@/lib/queries/scripts';
import { useStartIngest, useUploadNovel } from '@/lib/queries/ingest';
import { readUrl } from '@/lib/url-params';
import { cn } from '@/lib/utils';

type ScreenplayNodeProps = NodeProps & {
  id: string;
  data: ScreenplayNodeData;
  selected?: boolean;
};

const DEFAULT_WIDTH = 480;
const DEFAULT_HEIGHT = 320;
const MIN_WIDTH = 360;
const MIN_HEIGHT = 240;
const MAX_WIDTH = 1600;
const MAX_HEIGHT = 1200;

const MAX_EPISODES = 60;
const STYLE_AUTO = 'auto';
// 任务状态词表与 ingest / story 页保持一致：活跃 / 终态。
const ACTIVE_TASK_STATUSES = new Set(['running', 'queued', 'submitting']);
const FAILED_TASK_STATUSES = new Set(['failed', 'stopped']);

const TABS: { key: ScreenplayNodeMode; labelKey: string; Icon: typeof ScrollText }[] = [
  { key: 'upload', labelKey: 'node.screenplay.tab.upload', Icon: Upload },
  { key: 'story', labelKey: 'node.screenplay.tab.story', Icon: Sparkles },
  { key: 'derive', labelKey: 'node.screenplay.tab.derive', Icon: Layers },
];

const ACTION_BUTTON_CLASS =
  'inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50';
const ACTION_PRIMARY_CLASS =
  'bg-cyan-300/15 text-cyan-100 hover:bg-cyan-300/25 border border-cyan-300/25';
const ACTION_SECONDARY_CLASS =
  'border border-white/12 bg-white/[0.04] text-white/80 hover:bg-white/[0.09]';

export const ScreenplayNode = memo(({ id, data, selected, width, height }: ScreenplayNodeProps) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const updateNodeInternals = useUpdateNodeInternals();
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const selectedNodeId = useCanvasStore((state) => state.selectedNodeId);
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);

  const project = readUrl().project ?? '';
  const fileInputRef = useRef<HTMLInputElement>(null);

  const mode: ScreenplayNodeMode = data.mode ?? 'story';
  const episodeCount =
    typeof data.episodeCount === 'number' && data.episodeCount > 0
      ? Math.min(data.episodeCount, MAX_EPISODES)
      : 10;
  const storyStyle = typeof data.storyStyle === 'string' ? data.storyStyle : '';
  const spineTemplate = data.spineTemplate === 'narrated' ? 'narrated' : 'drama';
  const deriveEpisode =
    typeof data.deriveEpisode === 'number' && data.deriveEpisode > 0
      ? data.deriveEpisode
      : 1;

  const [draft, setDraft] = useState<string | null>(null);
  const [awaiting, setAwaiting] = useState(false);
  const [pickedFile, setPickedFile] = useState<File | null>(null);

  const resolvedTitle = useMemo(
    () => localizeNodeDisplayName(CANVAS_NODE_TYPES.screenplay, data, t),
    [data, t],
  );

  const sourceQuery = useProjectStorySource(project, !!project);
  const importStory = useImportProjectStory(project);
  const generateScriptJob = useGenerateProjectScript(project);
  const syncScript = useSyncGeneratedScript(project);
  const resultQuery = useGeneratedScriptResult(project, !!project, awaiting);

  const buildChars = useBuildCharacters(project);
  const planEpisodes = usePlanEpisodes(project);
  const planScenes = usePlanEpisodeScenes(project);
  const planProps = usePlanEpisodeProps(project);
  const generateEpisodeScript = useGenerateScript(project, deriveEpisode);

  const uploadNovel = useUploadNovel(project);
  const startIngest = useStartIngest(project);

  const resolvedWidth = Math.max(MIN_WIDTH, Math.round(width ?? DEFAULT_WIDTH));
  const resolvedHeight = Math.max(MIN_HEIGHT, Math.round(height ?? DEFAULT_HEIGHT));
  const isNodeSelected = Boolean(selected) || selectedNodeId === id;

  const taskData = resultQuery.data?.data;
  const taskStatus = taskData?.status ?? null;
  const taskActive = taskStatus !== null && ACTIVE_TASK_STATUSES.has(taskStatus);
  const taskFailed = taskStatus !== null && FAILED_TASK_STATUSES.has(taskStatus);
  const taskCompleted = taskStatus === 'completed';

  // 进入节点时若项目已有活跃剧本任务 → 自动开启轮询；终态停轮询。
  useEffect(() => {
    if (!taskStatus) return;
    if (ACTIVE_TASK_STATUSES.has(taskStatus)) {
      setAwaiting(true);
      return;
    }
    if (taskStatus === 'completed' || FAILED_TASK_STATUSES.has(taskStatus)) {
      setAwaiting(false);
    }
  }, [taskStatus]);

  // 剧本生成完成 → 把章节摘要写回节点 data，供刷新后展示。
  useEffect(() => {
    if (!taskCompleted || !taskData) return;
    const chapters = taskData.episodes ?? taskData.chapter_summaries?.length ?? 0;
    if (!chapters) return;
    updateNodeData(id, {
      lastSummary: t('node.screenplay.summary', { chapters, chars: taskData.total_chars ?? 0 }),
    });
  }, [taskCompleted, taskData, id, t, updateNodeData]);

  useEffect(() => {
    updateNodeInternals(id);
  }, [id, resolvedHeight, resolvedWidth, updateNodeInternals]);

  const guardProject = useCallback((): boolean => {
    if (project) return true;
    toast.error(t('node.screenplay.noProject'));
    return false;
  }, [project, t]);

  const goSection = useCallback(
    (section: keyof typeof PROJECT_SECTION_ROUTES) => {
      if (!project) return;
      void navigate({ to: PROJECT_SECTION_ROUTES[section], params: { project } });
    },
    [navigate, project],
  );

  const handleSetMode = useCallback(
    (next: ScreenplayNodeMode) => updateNodeData(id, { mode: next }),
    [id, updateNodeData],
  );

  const storyTextValue = draft ?? sourceQuery.data?.data?.text ?? '';

  const handleGenerate = useCallback(async () => {
    if (!guardProject()) return;
    const text = storyTextValue.trim();
    if (!text) {
      toast.error(t('node.screenplay.story.empty'));
      return;
    }
    try {
      // 先把未保存草稿落盘，保证生成基于最新故事内容（与故事页一致）。
      if (draft !== null) {
        await importStory.mutateAsync({ text });
        setDraft(null);
      }
      await generateScriptJob.mutateAsync({
        episode_count: episodeCount,
        story_style: storyStyle,
      });
      setAwaiting(true);
      toast.success(t('node.screenplay.story.started'));
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t('node.screenplay.story.failed'));
    }
  }, [guardProject, storyTextValue, draft, importStory, generateScriptJob, episodeCount, storyStyle, t]);

  const handleSync = useCallback(() => {
    if (!guardProject()) return;
    syncScript.mutate(undefined, {
      onSuccess: () => {
        toast.success(t('node.screenplay.story.syncDone'));
        goSection('ingest');
      },
      onError: (error) =>
        toast.error(error instanceof Error && error.message ? error.message : t('node.screenplay.story.syncFailed')),
    });
  }, [guardProject, syncScript, t, goSection]);

  const handleUpload = useCallback(async () => {
    if (!guardProject()) return;
    if (!pickedFile) {
      toast.error(t('node.screenplay.upload.noFile'));
      return;
    }
    try {
      const uploaded = await uploadNovel.mutateAsync({ file: pickedFile, spineTemplate });
      const filename = uploaded.data?.filename;
      if (!filename) throw new Error(t('node.screenplay.upload.noFilename'));
      await startIngest.mutateAsync({ filename, spine_template: spineTemplate });
      toast.success(t('node.screenplay.upload.started'));
      setPickedFile(null);
      goSection('ingest');
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t('node.screenplay.upload.failed'));
    }
  }, [guardProject, pickedFile, uploadNovel, spineTemplate, startIngest, t, goSection]);

  // 派生动作统一走 mutateAsync；部分项目管线 mutation 对逻辑失败（ok:false）不
  // 抛错而是返回带 error 的响应，这里统一识别 ok:false 弹错。
  const fire = useCallback(
    async (fn: () => Promise<unknown>, doneKey: string) => {
      if (!guardProject()) return;
      try {
        const res = await fn();
        const failed =
          res && typeof res === 'object' && 'ok' in res && (res as { ok?: boolean }).ok === false;
        if (failed) {
          const msg = (res as { error?: string }).error;
          toast.error(msg && msg.trim() ? msg : t('node.screenplay.derive.failed'));
        } else {
          toast.success(t(doneKey));
        }
      } catch (error) {
        toast.error(
          error instanceof Error && error.message ? error.message : t('node.screenplay.derive.failed'),
        );
      }
    },
    [guardProject, t],
  );

  const cardToneClass = canvasNodeFrameClass({ selected: isNodeSelected });

  return (
    <div
      className="group relative h-full w-full overflow-visible"
      style={{ width: resolvedWidth, height: resolvedHeight }}
      onClick={() => setSelectedNode(id)}
    >
      <Handle type="target" position={Position.Left} id="target" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />
      <Handle type="source" position={Position.Right} id="source" className="!h-2 !w-2 !border-0 !bg-[rgb(148,163,184)]" />

      <NodeHeader
        className={NODE_HEADER_FLOATING_POSITION_CLASS}
        icon={<ScrollText className="h-4 w-4" />}
        titleText={resolvedTitle}
        editable
        onTitleChange={(nextTitle) => updateNodeData(id, { displayName: nextTitle })}
      />

      <NodeResizeHandle minWidth={MIN_WIDTH} minHeight={MIN_HEIGHT} maxWidth={MAX_WIDTH} maxHeight={MAX_HEIGHT} />

      <div
        className={cn(
          'relative flex h-full w-full flex-col overflow-hidden rounded-[var(--node-radius)] border',
          CANVAS_NODE_PANEL_SURFACE_CLASS,
          cardToneClass,
        )}
      >
        {!project && (
          <div className="flex flex-1 items-center justify-center px-4 text-center text-[13px] text-white/60">
            {t('node.screenplay.noProject')}
          </div>
        )}

        {project && (
          <>
            <div className="flex shrink-0 items-center gap-1 border-b border-white/8 px-2 py-2">
              {TABS.map(({ key, labelKey, Icon }) => (
                <button
                  key={key}
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    handleSetMode(key);
                  }}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[12px] transition-colors',
                    mode === key ? 'bg-white/[0.1] text-white' : 'text-white/55 hover:bg-white/[0.05]',
                  )}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {t(labelKey)}
                </button>
              ))}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-3" onClick={(event) => event.stopPropagation()}>
              {mode === 'upload' && (
                <div className="flex flex-col gap-3">
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".txt,.md,.doc,.docx"
                    className="hidden"
                    onChange={(event) => setPickedFile(event.target.files?.[0] ?? null)}
                  />
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="flex items-center justify-between rounded-lg border border-dashed border-white/18 bg-white/[0.03] px-3 py-2.5 text-left text-[13px] text-white/75 hover:bg-white/[0.06]"
                  >
                    <span className="truncate">{pickedFile?.name || t('node.screenplay.upload.pick')}</span>
                    <Upload className="h-4 w-4 shrink-0 text-white/45" />
                  </button>
                  <label className="flex items-center gap-2 text-[12px] text-white/60">
                    {t('node.screenplay.upload.spine')}
                    <select
                      value={spineTemplate}
                      onChange={(event) => updateNodeData(id, { spineTemplate: event.target.value === 'narrated' ? 'narrated' : 'drama' })}
                      className="rounded-md border border-white/12 bg-white/[0.05] px-2 py-1 text-[12px] text-white/85"
                    >
                      <option value="drama">{t('node.screenplay.upload.spineDrama')}</option>
                      <option value="narrated">{t('node.screenplay.upload.spineNarrated')}</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    disabled={uploadNovel.isPending || startIngest.isPending}
                    onClick={() => void handleUpload()}
                    className={cn(ACTION_BUTTON_CLASS, ACTION_PRIMARY_CLASS)}
                  >
                    {uploadNovel.isPending || startIngest.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileText className="h-4 w-4" />}
                    {t('node.screenplay.upload.submit')}
                  </button>
                  <p className="text-[11px] leading-5 text-white/45">{t('node.screenplay.upload.hint')}</p>
                </div>
              )}

              {mode === 'story' && (
                <div className="flex flex-col gap-3">
                  <textarea
                    value={storyTextValue}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder={t('node.screenplay.story.placeholder')}
                    rows={5}
                    className="w-full resize-none rounded-lg border border-white/12 bg-white/[0.04] px-2.5 py-2 text-[12px] leading-5 text-white/85 outline-none focus:border-cyan-300/40"
                  />
                  <div className="flex flex-wrap items-center gap-2">
                    <label className="flex items-center gap-1.5 text-[12px] text-white/60">
                      {t('node.screenplay.story.episodes')}
                      <input
                        type="number"
                        min={1}
                        max={MAX_EPISODES}
                        value={episodeCount}
                        onChange={(event) =>
                          updateNodeData(id, { episodeCount: Math.max(1, Math.min(MAX_EPISODES, Number(event.target.value) || 1)) })
                        }
                        className="w-16 rounded-md border border-white/12 bg-white/[0.05] px-2 py-1 text-[12px] text-white/85"
                      />
                    </label>
                    <label className="flex items-center gap-1.5 text-[12px] text-white/60">
                      {t('node.screenplay.story.style')}
                      <select
                        value={storyStyle || STYLE_AUTO}
                        onChange={(event) =>
                          updateNodeData(id, { storyStyle: event.target.value === STYLE_AUTO ? '' : event.target.value })
                        }
                        className="rounded-md border border-white/12 bg-white/[0.05] px-2 py-1 text-[12px] text-white/85"
                      >
                        <option value={STYLE_AUTO}>{t('node.screenplay.story.styleAuto')}</option>
                        {STORY_STYLE_VALUES.filter((v) => v !== '').map((v) => (
                          <option key={v} value={v}>
                            {t(`storyPage.style.${v}`)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      disabled={generateScriptJob.isPending || taskActive}
                      onClick={() => void handleGenerate()}
                      className={cn(ACTION_BUTTON_CLASS, ACTION_PRIMARY_CLASS)}
                    >
                      {generateScriptJob.isPending || taskActive ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
                      {t('node.screenplay.story.generate')}
                    </button>
                    <button
                      type="button"
                      disabled={!taskCompleted || syncScript.isPending}
                      onClick={handleSync}
                      className={cn(ACTION_BUTTON_CLASS, ACTION_SECONDARY_CLASS)}
                    >
                      {syncScript.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ArrowRightLeft className="h-4 w-4" />}
                      {t('node.screenplay.story.sync')}
                    </button>
                  </div>

                  {(taskActive || typeof taskData?.progress === 'number') && (
                    <div className="flex flex-col gap-1">
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
                        <div
                          className="h-full rounded-full bg-cyan-300/70 transition-all"
                          style={{ width: `${Math.round((taskData?.progress ?? 0) * 100)}%` }}
                        />
                      </div>
                      {taskData?.current_task && (
                        <span className="text-[11px] text-white/55">{taskData.current_task}</span>
                      )}
                    </div>
                  )}
                  {taskFailed && taskData?.error && (
                    <span className="text-[11px] text-red-300">{taskData.error}</span>
                  )}
                  {taskCompleted && data.lastSummary && (
                    <span className="inline-flex items-center gap-1.5 text-[12px] text-emerald-300/90">
                      <CheckCircle2 className="h-4 w-4" />
                      {data.lastSummary}
                    </span>
                  )}
                </div>
              )}

              {mode === 'derive' && (
                <div className="flex flex-col gap-3">
                  <p className="text-[11px] leading-5 text-white/50">{t('node.screenplay.derive.hint')}</p>
                  <label className="flex items-center gap-1.5 text-[12px] text-white/60">
                    {t('node.screenplay.story.episodes')}
                    <input
                      type="number"
                      min={1}
                      value={deriveEpisode}
                      onChange={(event) => updateNodeData(id, { deriveEpisode: Math.max(1, Number(event.target.value) || 1) })}
                      className="w-16 rounded-md border border-white/12 bg-white/[0.05] px-2 py-1 text-[12px] text-white/85"
                    />
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <DeriveButton icon={<Users className="h-4 w-4" />} label={t('node.screenplay.derive.characters')} busy={buildChars.isPending} onClick={() => void fire(() => buildChars.mutateAsync(), 'node.screenplay.derive.charactersDone')} />
                    <DeriveButton icon={<Layers className="h-4 w-4" />} label={t('node.screenplay.derive.episodes')} busy={planEpisodes.isPending} onClick={() => void fire(() => planEpisodes.mutateAsync({}), 'node.screenplay.derive.episodesDone')} />
                    <DeriveButton icon={<MapIcon className="h-4 w-4" />} label={t('node.screenplay.derive.scenes')} busy={planScenes.isPending} onClick={() => void fire(() => planScenes.mutateAsync(deriveEpisode), 'node.screenplay.derive.scenesDone')} />
                    <DeriveButton icon={<Box className="h-4 w-4" />} label={t('node.screenplay.derive.props')} busy={planProps.isPending} onClick={() => void fire(() => planProps.mutateAsync(deriveEpisode), 'node.screenplay.derive.propsDone')} />
                    <DeriveButton icon={<Clapperboard className="h-4 w-4" />} label={t('node.screenplay.derive.beats')} busy={generateEpisodeScript.isPending} onClick={() => void fire(() => generateEpisodeScript.mutateAsync({}), 'node.screenplay.derive.beatsDone')} />
                  </div>
                  <button
                    type="button"
                    onClick={() => goSection('episodes')}
                    className={cn(ACTION_BUTTON_CLASS, ACTION_SECONDARY_CLASS, 'self-start')}
                  >
                    {t('node.screenplay.derive.view')}
                  </button>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
});

function DeriveButton({
  icon,
  label,
  busy,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={busy}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      className={cn(ACTION_BUTTON_CLASS, ACTION_SECONDARY_CLASS, 'w-full')}
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      <span className="truncate">{label}</span>
    </button>
  );
}

ScreenplayNode.displayName = 'ScreenplayNode';
