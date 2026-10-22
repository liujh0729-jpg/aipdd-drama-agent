// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ArrowLeft, Clapperboard, Copy, Loader2, Play, RefreshCw, WandSparkles, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import {
  fetchFreezoneMotionPrompt,
  fetchFreezoneMotionStylePrompt,
  type FreezoneMotionCase,
  type FreezoneMotionMethod,
  type FreezoneMotionStyle,
} from '@/api/ops';
import {
  motionPreviewUrl,
  motionVideoUrl,
  resolveMotionAssetUrl,
} from '@/features/canvas/hooks/useFreezoneMotionCases';
import { VideoViewerModal } from '@/features/canvas/ui/VideoViewerModal';

const MOTION_MODAL_CLASS =
  'relative flex w-[min(1120px,92vw)] flex-col overflow-hidden rounded-[var(--ui-radius-lg)] border border-[var(--ui-border-strong)] bg-[rgb(var(--surface-rgb)/0.96)] shadow-[var(--ui-shadow-panel)] backdrop-blur-md';
const MOTION_LIST_SIZE_CLASS = 'h-[min(720px,82vh)]';
const MOTION_DETAIL_SIZE_CLASS = 'max-h-[82vh]';

const ALL_CATEGORIES = '__all__';

/** 图册里的两个页签对应两份名录：案例给分镜结构，画风给画面观感。 */
type GalleryTab = 'cases' | 'styles' | 'methods';
type SourceKind = 'case' | 'style';
/** 打开的条目：kind 决定取哪份名录与走哪个提示词接口，不能只存 id。 */
type DetailRef = { kind: SourceKind; id: string };

/**
 * 交给宿主的出处。案例与风格都带这四个字段，所以「填入」只有一处实现；
 * `kind` 必带：后端把 case_id 与 style_id 当互斥项，宿主据此决定填哪一栏。
 */
export interface MotionGallerySource {
  id: string;
  label: string;
  category: string;
  kind: SourceKind;
}

function toSource(
  item: FreezoneMotionCase | FreezoneMotionStyle,
  kind: SourceKind,
): MotionGallerySource {
  return { id: item.id, label: item.label, category: item.category, kind };
}

/** 海报随包、原片按需，两条链路都可能取不到 —— 取不到就占位块，不留碎图标。 */
function MotionPoster({
  item,
  assetBase,
  alt,
  className = '',
}: {
  item: FreezoneMotionCase;
  assetBase: string;
  alt: string;
  className?: string;
}) {
  const src = item.posterReady ? resolveMotionAssetUrl(item.cover, assetBase) : '';
  if (!src) {
    return <div role="img" aria-label={alt} className={`bg-[rgb(var(--text-rgb)/0.06)] ${className}`} />;
  }
  return <PosterImage alt={alt} className={className} src={src} />;
}

function PosterImage({
  alt,
  className,
  src,
}: {
  alt: string;
  className: string;
  src: string;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return <div role="img" aria-label={alt} className={`bg-[rgb(var(--text-rgb)/0.06)] ${className}`} />;
  }
  return (
    <img
      alt={alt}
      src={src}
      loading="lazy"
      onError={() => setFailed(true)}
      className={className}
    />
  );
}

/**
 * 随包小样片：卡片悬停、详情首屏与工具面板共用。只在 active 时播，暂停后回到
 * 首帧；片源加载出第一帧前保持透明，底下那张海报继续兜底，避免黑屏一闪。
 */
function MotionPreviewVideo({
  item,
  active,
  className = '',
  label,
}: {
  item: FreezoneMotionCase;
  active: boolean;
  className?: string;
  label?: string;
}) {
  const source = motionPreviewUrl(item);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setReady(false);
  }, [source]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (active) {
      const played = video.play();
      if (played && typeof played.catch === 'function') {
        played.catch(() => undefined);
      }
      return;
    }
    video.pause();
    try {
      video.currentTime = 0;
    } catch {
      // 元数据还没到；下次播放自然会从片头开始。
    }
  }, [active, source]);

  if (!source || failed) return null;
  const visible = active && ready;
  return (
    <video
      ref={videoRef}
      data-motion-preview="1"
      src={source}
      muted
      loop
      playsInline
      preload="none"
      aria-hidden={label ? undefined : true}
      aria-label={label}
      onPlaying={() => setReady(true)}
      onError={() => setFailed(true)}
      className={`${className} transition-opacity duration-300 ${
        visible ? 'opacity-100' : 'opacity-0'
      }`}
    />
  );
}

/** 案例卡片/详情的主画面：海报打底，样片加载好后覆盖上去。 */
function MotionCaseMedia({
  item,
  assetBase,
  alt,
  active,
  badgeLabel,
  className = '',
}: {
  item: FreezoneMotionCase;
  assetBase: string;
  alt: string;
  active: boolean;
  badgeLabel: string;
  className?: string;
}) {
  return (
    <div className={`relative block overflow-hidden bg-black/45 ${className}`}>
      <MotionPoster
        alt={alt}
        assetBase={assetBase}
        className="absolute inset-0 h-full w-full object-contain"
        item={item}
      />
      <MotionPreviewVideo
        item={item}
        active={active}
        className="absolute inset-0 h-full w-full object-contain"
      />
      {item.previewReady ? (
        <span
          className={`pointer-events-none absolute left-2 top-2 inline-flex items-center gap-1 rounded-[6px] bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white/88 transition-opacity duration-200 ${
            active ? 'opacity-0' : 'opacity-100'
          }`}
        >
          <Play className="size-2.5 fill-current" />
          {badgeLabel}
        </span>
      ) : null}
    </div>
  );
}

/**
 * 风格样张：这条名录一张上游素材都不随包，卡片就用它自己登记的色板画出来。
 * 等宽色带按清单顺序排，所以「同一风格」在任何地方看起来都一样，不需要联网取图。
 */
export function MotionStyleSwatch({
  palette,
  alt,
  className = '',
}: {
  palette: string[];
  alt: string;
  className?: string;
}) {
  const colors = palette.length > 0 ? palette : ['rgb(var(--text-rgb)/0.06)'];
  return (
    <div role="img" aria-label={alt} className={`flex ${className}`}>
      {colors.map((color, index) => (
        <div
          key={`${color}-${index}`}
          aria-hidden="true"
          className="h-full flex-1"
          style={{ background: color }}
        />
      ))}
    </div>
  );
}

export interface MotionGalleryModalProps {
  cases: FreezoneMotionCase[];
  categories: string[];
  /** 画风图册（66 条）：与案例同一套动作，但没有海报与原片。 */
  styles: FreezoneMotionStyle[];
  styleCategories: string[];
  methods: FreezoneMotionMethod[];
  renderContract: string;
  assetBase: string;
  /** 取完整提示词（图册规格 + 渲染契约）要走项目接口，拼法只在后端定一次。 */
  project: string | null;
  /** 宿主决定「填进」的目标是什么：画布视频节点写节点提示词，分镜台写分镜提示词。 */
  applyLabelKey: string;
  onApply: (prompt: string, source: MotionGallerySource) => void;
  /**
   * 「按这条案例渲染」：只有能发起渲染的宿主才传这个回调，按钮随之出现。
   * 任务按 case_id 在服务端取提示词，前端不经手拼装；成片回写目标由宿主决定
   * （画布带 node_id/canvas_id 写回节点历史）。
   */
  onRenderCase?: (item: FreezoneMotionCase) => void | Promise<void>;
  /** 「按这条画风出片」：与案例同一个渲染链路，只是规格换成画风图册那条。 */
  onRenderStyle?: (item: FreezoneMotionStyle) => void | Promise<void>;
  isLoading?: boolean;
  styleLoading?: boolean;
  /** 清单拉取失败与「没有案例」是两件事：失败要给可执行的出路，不能只显示空墙。 */
  error?: Error | null;
  styleError?: Error | null;
  onRetry?: () => void;
  onStyleRetry?: () => void;
  onClose: () => void;
}

export function MotionGalleryModal({
  cases,
  categories,
  styles,
  styleCategories,
  methods,
  renderContract,
  assetBase,
  project,
  applyLabelKey,
  onApply,
  onRenderCase,
  onRenderStyle,
  isLoading = false,
  styleLoading = false,
  error = null,
  styleError = null,
  onRetry,
  onStyleRetry,
  onClose,
}: MotionGalleryModalProps) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<GalleryTab>('cases');
  const [category, setCategory] = useState<string>(ALL_CATEGORIES);
  const [detail, setDetail] = useState<DetailRef | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [hoveredCaseId, setHoveredCaseId] = useState<string | null>(null);
  const [playing, setPlaying] = useState<{ case: FreezoneMotionCase; url: string } | null>(
    null,
  );
  // 一次图册会话里同一条目的提示词只取一次：复制、填入、再看第二条都各自命中后端，
  // 但来回点同一条不该反复打接口。案例与风格分开缓存，key 带 kind。
  const promptCache = useRef(new Map<string, string>());

  const detailCase =
    detail?.kind === 'case' ? cases.find((item) => item.id === detail.id) ?? null : null;
  const detailStyle =
    detail?.kind === 'style' ? styles.find((item) => item.id === detail.id) ?? null : null;
  // 详情正文、复制与填入对两种条目走同一套：只有左边那块样张与「播放原片」是分叉的。
  const entry = detailCase ?? detailStyle;
  const entryKind: SourceKind = detailCase ? 'case' : 'style';
  // 详情里正文显示的就是「复制/填入」要给出的那段文字：由服务端拼装并读提示词库
  // 生效值，避免看到规格、复制到的却是另一份。清单自带的规格先占位。
  const [effectivePrompt, setEffectivePrompt] = useState<string | null>(null);
  const isStyleTab = tab === 'styles';
  const byCategory = <T extends { category: string }>(list: T[]): T[] =>
    category === ALL_CATEGORIES ? list : list.filter((item) => item.category === category);
  const visibleCases = useMemo(
    () => (isStyleTab ? [] : byCategory(cases)),
    [cases, category, isStyleTab],
  );
  const visibleStyles = useMemo(
    () => (isStyleTab ? byCategory(styles) : []),
    [isStyleTab, styles, category],
  );
  // 页签决定读哪一份名录：加载态、失败出路、空目录文案都跟着页签换，
  // 否则画风清单挂了会显示成「案例为空」。
  const listLoading = isStyleTab ? styleLoading : isLoading;
  const listError = isStyleTab ? styleError : error;
  const listRetry = isStyleTab ? onStyleRetry : onRetry;
  const emptyLabelKey = isStyleTab
    ? 'canvas.motionGallery.emptyStyles'
    : 'canvas.motionGallery.empty';
  const failedLabelKey = isStyleTab
    ? 'canvas.motionGallery.loadFailedStyles'
    : 'canvas.motionGallery.loadFailed';
  const shownCategories = useMemo(() => {
    // 顺序以服务端清单为准；清单没登记到的分类（外部清单新加的值）补在后面，
    // 否则那几条在任何筛选下都点不到。
    const listed = (isStyleTab ? styleCategories : categories).filter((name) =>
      (isStyleTab ? styles : cases).some((item) => item.category === name),
    );
    const extra: string[] = [];
    for (const item of isStyleTab ? styles : cases) {
      const name = item.category;
      if (name && !listed.includes(name) && !extra.includes(name)) extra.push(name);
    }
    return [...listed, ...extra];
  }, [cases, categories, isStyleTab, styleCategories, styles]);

  // 两份名录的分类集合互不相关：带着案例页签选中的分类切到画风页，会筛出一片空白。
  useEffect(() => {
    setCategory(ALL_CATEGORIES);
  }, [tab]);

  const takePrompt = useCallback(
    async (kind: SourceKind, id: string): Promise<string> => {
      const cacheKey = `${kind}:${id}`;
      const cached = promptCache.current.get(cacheKey);
      if (cached) return cached;
      if (!project) return '';
      const text =
        kind === 'style'
          ? await fetchFreezoneMotionStylePrompt(project, id)
          : await fetchFreezoneMotionPrompt(project, id);
      if (text) promptCache.current.set(cacheKey, text);
      return text;
    },
    [project],
  );

  useEffect(() => {
    setEffectivePrompt(null);
    if (!detail || !entry) return;
    let alive = true;
    void takePrompt(detail.kind, detail.id)
      .then((text) => {
        if (alive && text) setEffectivePrompt(text);
      })
      // 预览取不到就留在清单里的规格上：正文只是预览，不该挡住浏览与播放。
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [detail, entry, takePrompt]);

  const handleCopy = async () => {
    if (!detail || !entry || busy) return;
    setBusy(`copy:${detail.id}`);
    try {
      const text = await takePrompt(detail.kind, detail.id);
      if (!text) {
        toast.error(t('canvas.motionGallery.applyFailed'));
        return;
      }
      await navigator.clipboard.writeText(text);
      toast.success(t('canvas.motionGallery.copied'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('canvas.motionGallery.copyFailed'));
    } finally {
      setBusy(null);
    }
  };

  const handleApply = async () => {
    if (!detail || !entry || busy) return;
    setBusy(`apply:${detail.id}`);
    try {
      const text = await takePrompt(detail.kind, detail.id);
      if (!text) {
        toast.error(t('canvas.motionGallery.applyFailed'));
        return;
      }
      onApply(text, toSource(entry, detail.kind));
      onClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('canvas.motionGallery.applyFailed'));
    } finally {
      setBusy(null);
    }
  };

  // 渲染是长任务：图册只负责把这条案例/画风交给宿主既有的渲染链路，随即关闭自己，
  // 进度与成片由宿主那边的 toast 跟进——不隔一层弹层去等逐帧渲染。
  const handleRender = () => {
    if (!detail || !entry || busy) return;
    if (detail.kind === 'case') {
      if (!onRenderCase || !detailCase) return;
      onRenderCase(detailCase);
    } else {
      if (!onRenderStyle || !detailStyle) return;
      onRenderStyle(detailStyle);
    }
    onClose();
  };

  // 「按这条出片」按钮的条件：条目本身在名录里，且宿主确实接了这个图册的渲染。
  const renderTarget: FreezoneMotionCase | FreezoneMotionStyle | null =
    entryKind === 'case' ? (onRenderCase ? detailCase : null) : onRenderStyle ? detailStyle : null;

  useEffect(() => {
    // 与画风图墙同一个 Esc 口径：冒泡阶段监听，详情先吃掉一次退回列表，第二次才关整个弹层。
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.isComposing) return;
      if (playing) {
        setPlaying(null);
        return;
      }
      if (detail) setDetail(null);
      else onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [detail, onClose, playing]);

  // 三个页签共用一份状态块：加载/失败的出路是同一条（都来自案例清单接口），只有
  // 「清单里真的没有条目」的空目录文案按页签换。方法页签以前直接 map，后端清单
  // 挂了会显示成一片空白，看起来像没配数据。
  const statusBlock = (labelKey: string) => (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 text-xs text-text-muted">
      <span>
        {listLoading ? t('canvas.motionGallery.loading') : listError ? t(failedLabelKey) : t(labelKey)}
      </span>
      {!listLoading && listRetry ? (
        <button
          type="button"
          onClick={listRetry}
          className="inline-flex h-7 items-center gap-1.5 rounded-[6px] bg-[rgb(var(--text-rgb)/0.08)] px-2.5 text-[11px] font-medium text-text-dark transition-colors hover:bg-[rgb(var(--text-rgb)/0.14)]"
        >
          <RefreshCw className="size-3" />
          {t('canvas.motionGallery.retry')}
        </button>
      ) : null}
    </div>
  );

  const renderCard = (item: FreezoneMotionCase) => (
    <div
      key={item.id}
      onMouseEnter={() => setHoveredCaseId(item.id)}
      onMouseLeave={() =>
        setHoveredCaseId((current) => (current === item.id ? null : current))
      }
      onFocus={() => setHoveredCaseId(item.id)}
      onBlur={() =>
        setHoveredCaseId((current) => (current === item.id ? null : current))
      }
      className="group relative overflow-hidden rounded-[var(--ui-radius-lg)] border border-[rgb(var(--text-rgb)/0.10)] bg-[rgb(var(--text-rgb)/0.04)] shadow-[var(--ui-shadow-panel)] transition-[border-color,box-shadow] duration-300 ease-out hover:border-[rgb(var(--accent-rgb)/0.40)] hover:shadow-[var(--ui-shadow-panel),var(--ui-glow-accent)] focus-within:border-[rgb(var(--accent-rgb)/0.40)]"
    >
      <button
        type="button"
        onClick={() => {
          setHoveredCaseId(null);
          setDetail({ id: item.id, kind: 'case' });
        }}
        aria-label={t('canvas.motionGallery.detailAria', { label: item.label })}
        className="block w-full cursor-pointer text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--accent-rgb))]"
      >
        <MotionCaseMedia
          active={hoveredCaseId === item.id}
          alt={t('canvas.motionGallery.posterAlt', { label: item.label })}
          assetBase={assetBase}
          badgeLabel={t('canvas.motionGallery.previewBadge')}
          className="aspect-video w-full"
          item={item}
        />
      </button>
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/85 via-black/20 to-transparent opacity-80 transition-opacity duration-300 group-hover:opacity-100 group-focus-within:opacity-100" />
      <div className="pointer-events-none absolute bottom-2.5 left-3 right-3 truncate text-xs font-medium text-white/92">
        {item.label}
      </div>
      {item.durationSeconds ? (
        <div className="pointer-events-none absolute right-2 top-2 rounded-[6px] bg-black/60 px-1.5 py-0.5 text-[11px] tabular-nums text-white/85">
          {t('canvas.motionGallery.duration', { seconds: Math.round(item.durationSeconds) })}
        </div>
      ) : null}
    </div>
  );

  // 画风卡片没有随包素材，样张就是清单里那串色板本身：同一风格在任何地方同色同序。
  const renderStyleCard = (item: FreezoneMotionStyle) => (
    <div
      key={`style:${item.id}`}
      className="group relative overflow-hidden rounded-[var(--ui-radius-lg)] border border-[rgb(var(--text-rgb)/0.10)] bg-[rgb(var(--text-rgb)/0.04)] shadow-[var(--ui-shadow-panel)] transition-[border-color,box-shadow] duration-300 ease-out hover:border-[rgb(var(--accent-rgb)/0.40)] hover:shadow-[var(--ui-shadow-panel),var(--ui-glow-accent)] focus-within:border-[rgb(var(--accent-rgb)/0.40)]"
    >
      <button
        type="button"
        onClick={() => setDetail({ id: item.id, kind: 'style' })}
        aria-label={t('canvas.motionGallery.detailAria', { label: item.label })}
        className="block w-full cursor-pointer text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[rgb(var(--accent-rgb))]"
      >
        <MotionStyleSwatch
          alt={t('canvas.motionGallery.paletteAlt', { label: item.label })}
          className="aspect-video w-full"
          palette={item.palette}
        />
      </button>
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/85 via-black/20 to-transparent opacity-80 transition-opacity duration-300 group-hover:opacity-100 group-focus-within:opacity-100" />
      <div className="pointer-events-none absolute bottom-2.5 left-3 right-3 truncate text-xs font-medium text-white/92">
        {item.label}
      </div>
      {item.category ? (
        <div className="pointer-events-none absolute right-2 top-2 max-w-[60%] truncate rounded-[6px] bg-black/60 px-1.5 py-0.5 text-[11px] text-white/85">
          {item.category}
        </div>
      ) : null}
    </div>
  );

  return createPortal(
    <div
      className="fixed inset-0 z-[300] flex items-center justify-center bg-black/55"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className={`${MOTION_MODAL_CLASS} ${
          entry ? MOTION_DETAIL_SIZE_CLASS : MOTION_LIST_SIZE_CLASS
        }`}
        role="dialog"
        aria-modal="true"
        aria-label={
          entry
            ? t('canvas.motionGallery.detailAria', { label: entry.label })
            : t('canvas.motionGallery.title')
        }
        onMouseDown={(event) => event.stopPropagation()}
      >
        {entry ? (
          <div className="flex h-12 shrink-0 items-center justify-between px-4">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setDetail(null)}
                aria-label={t('common.back')}
                className="flex size-7 items-center justify-center rounded-md text-text-muted/90 transition-colors hover:bg-[rgb(var(--text-rgb)/0.08)] hover:text-text-dark"
              >
                <ArrowLeft className="size-4" />
              </button>
              <span className="truncate text-sm font-medium text-text-dark">{entry.label}</span>
            </div>
            <button
              type="button"
              onClick={onClose}
              aria-label={t('common.close')}
              className="flex size-7 items-center justify-center rounded-md text-text-muted/90 transition-colors hover:bg-[rgb(var(--text-rgb)/0.08)] hover:text-text-dark"
            >
              <X className="size-4" />
            </button>
          </div>
        ) : (
          <div className="flex shrink-0 items-center gap-2 px-4 pb-2 pr-12 pt-4">
            <div className="flex shrink-0 items-center gap-1.5">
              {([
                { key: 'cases', label: t('canvas.motionGallery.tabCases') },
                { key: 'styles', label: t('canvas.motionGallery.tabStyles') },
                { key: 'methods', label: t('canvas.motionGallery.tabMethods') },
              ] as const).map((tabItem) => (
                <button
                  key={tabItem.key}
                  type="button"
                  onClick={() => setTab(tabItem.key)}
                  className={`h-7 rounded-[6px] px-2.5 text-xs font-medium transition-colors ${
                    tab === tabItem.key
                      ? 'bg-[rgb(var(--text-rgb)/0.14)] text-text-dark'
                      : 'text-text-dark/62 hover:bg-[rgb(var(--text-rgb)/0.08)] hover:text-text-dark'
                  }`}
                >
                  {tabItem.label}
                </button>
              ))}
            </div>
            {tab !== 'methods' && shownCategories.length > 1 ? (
              <div className="ui-scrollbar flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto pl-2">
                {[{ key: ALL_CATEGORIES, label: t('canvas.motionGallery.all') }, ...shownCategories.map((name) => ({ key: name, label: name }))].map(
                  (chip) => (
                    <button
                      key={chip.key}
                      type="button"
                      onClick={() => setCategory(chip.key)}
                      className={`h-7 shrink-0 rounded-[6px] px-2.5 text-xs font-medium transition-colors ${
                        chip.key === category
                          ? 'bg-[rgb(var(--text-rgb)/0.14)] text-text-dark'
                          : 'text-text-dark/62 hover:bg-[rgb(var(--text-rgb)/0.08)] hover:text-text-dark'
                      }`}
                    >
                      {chip.label}
                    </button>
                  ),
                )}
              </div>
            ) : (
              <div className="flex-1" />
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label={t('common.close')}
              className="absolute right-4 top-2.5 flex size-7 items-center justify-center rounded-md text-text-muted/90 transition-colors hover:bg-[rgb(var(--text-rgb)/0.08)] hover:text-text-dark"
            >
              <X className="size-4" />
            </button>
          </div>
        )}

        {entry ? (
          <div className="flex min-h-0 gap-4 overflow-hidden p-4">
            <div className="flex min-h-0 flex-1 flex-col gap-3">
              <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden rounded-[var(--ui-radius-lg)] border border-[rgb(var(--text-rgb)/0.08)] bg-black/55">
                {detailCase ? (
                  <>
                    <MotionPoster
                      alt={t('canvas.motionGallery.posterAlt', { label: detailCase.label })}
                      assetBase={assetBase}
                      className="max-h-full w-full object-contain"
                      item={detailCase}
                    />
                    <MotionPreviewVideo
                      active
                      className="absolute inset-0 h-full w-full object-contain"
                      item={detailCase}
                      label={t('canvas.motionGallery.previewAlt', {
                        label: detailCase.label,
                      })}
                    />
                    {detailCase.previewReady ? (
                      <span className="pointer-events-none absolute left-3 top-3 rounded-[6px] bg-black/60 px-1.5 py-0.5 text-[10px] font-medium text-white/88">
                        {t('canvas.motionGallery.previewBadge')}
                      </span>
                    ) : null}
                  </>
                ) : (
                  <MotionStyleSwatch
                    alt={t('canvas.motionGallery.paletteAlt', { label: detailStyle?.label ?? '' })}
                    className="h-full w-full"
                    palette={detailStyle?.palette ?? []}
                  />
                )}
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-muted">
                {entry.category ? <span>{entry.category}</span> : null}
                {detailCase?.resolution ? <span>{detailCase.resolution}</span> : null}
                {detailCase?.durationSeconds ? (
                  <span>
                    {t('canvas.motionGallery.duration', {
                      seconds: Math.round(detailCase.durationSeconds),
                    })}
                  </span>
                ) : null}
                {detailCase?.author ? <span>{detailCase.author}</span> : null}
                {detailStyle?.labelEn ? <span>{detailStyle.labelEn}</span> : null}
              </div>
              {detailCase && detailCase.videoReady && detailCase.mediaFile ? (
                <button
                  type="button"
                  onClick={() =>
                    setPlaying({
                      case: detailCase,
                      url: motionVideoUrl(detailCase, assetBase),
                    })
                  }
                  className="inline-flex h-8 w-fit items-center gap-1.5 rounded-[6px] bg-[rgb(var(--text-rgb)/0.08)] px-3 text-xs font-medium text-text-dark transition-colors hover:bg-[rgb(var(--text-rgb)/0.14)]"
                >
                  <Play className="size-3.5" />
                  {t('canvas.motionGallery.playVideo')}
                </button>
              ) : null}
            </div>
            <div className="w-[360px] shrink-0">
              <div className="flex max-h-[min(460px,calc(82vh-96px))] flex-col gap-3 overflow-hidden rounded-[8px] border border-[rgb(var(--text-rgb)/0.08)] bg-[rgb(var(--text-rgb)/0.03)] p-3">
                {entry.brief ? (
                  <p className="shrink-0 whitespace-pre-line text-xs leading-relaxed text-text-dark/80">
                    {entry.brief}
                  </p>
                ) : null}
                {entry.tags.length > 0 ? (
                  <div className="flex shrink-0 flex-wrap gap-1.5">
                    {entry.tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded-[6px] bg-[rgb(var(--text-rgb)/0.07)] px-1.5 py-0.5 text-[11px] text-text-dark/72"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                ) : null}
                <p className="ui-scrollbar min-h-0 flex-1 overflow-y-auto whitespace-pre-line text-[11px] leading-relaxed text-text-dark/70">
                  {effectivePrompt ?? entry.prompt_cn}
                </p>
                <div className="flex shrink-0 flex-col gap-2 border-t border-[rgb(var(--text-rgb)/0.08)] pt-3">
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={() => void handleCopy()}
                      className="inline-flex h-8 flex-1 items-center justify-center gap-1.5 rounded-[6px] bg-[rgb(var(--text-rgb)/0.08)] text-xs font-medium text-text-dark transition-colors hover:bg-[rgb(var(--text-rgb)/0.14)] disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {busy === `copy:${entry.id}` ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Copy className="size-3.5" />
                      )}
                      {t('canvas.motionGallery.copyPrompt')}
                    </button>
                    {/* 原片是案例才有的东西；画风只有规格，不谎报「视频取不到」。 */}
                    {detailCase && !detailCase.videoReady ? (
                      <span className="min-w-0 flex-1 text-[11px] leading-tight text-text-muted">
                        {t('canvas.motionGallery.videoUnavailable')}
                      </span>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void handleApply()}
                    className="inline-flex h-8 items-center justify-center gap-1.5 rounded-[6px] bg-[rgb(var(--text-rgb)/0.92)] text-sm font-medium text-bg-dark transition-colors hover:bg-[rgb(var(--text-rgb))] disabled:cursor-not-allowed disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent-rgb))]"
                  >
                    {busy === `apply:${entry.id}` ? (
                      <Loader2 className="size-3.5 animate-spin" />
                    ) : (
                      <WandSparkles className="size-3.5" />
                    )}
                    {t(applyLabelKey)}
                  </button>
                  {/* 需求 §2.1 的第二个图册动作；只有把渲染回调传进来的宿主才出现。
                      案例与画风是同一条渲染链路，只是规格换一个图册。 */}
                  {renderTarget ? (
                    <button
                      type="button"
                      disabled={busy !== null}
                      onClick={handleRender}
                      className="inline-flex h-8 items-center justify-center gap-1.5 rounded-[6px] bg-[rgb(var(--text-rgb)/0.08)] text-sm font-medium text-text-dark transition-colors hover:bg-[rgb(var(--text-rgb)/0.14)] disabled:cursor-not-allowed disabled:opacity-60 focus:outline-none focus-visible:ring-2 focus-visible:ring-[rgb(var(--accent-rgb))]"
                    >
                      <Clapperboard className="size-3.5" />
                      {t(
                        entryKind === 'case'
                          ? 'canvas.motionGallery.renderCase'
                          : 'canvas.motionGallery.renderStyle',
                      )}
                    </button>
                  ) : null}
                </div>
              </div>
            </div>
          </div>
        ) : tab === 'methods' ? (
          <div className="ui-scrollbar flex-1 overflow-y-auto p-4 [scrollbar-gutter:stable]">
            {methods.length === 0 ? (
              statusBlock('canvas.motionGallery.emptyMethods')
            ) : (
              <div className="grid grid-cols-3 gap-3">
                {methods.map((method) => (
                  <div
                    key={method.id}
                    className="flex flex-col gap-1.5 rounded-[var(--ui-radius-lg)] border border-[rgb(var(--text-rgb)/0.08)] bg-[rgb(var(--text-rgb)/0.03)] p-3"
                  >
                    <p className="text-xs font-medium text-text-dark">{method.label}</p>
                    <p className="text-[11px] leading-relaxed text-text-dark/70">{method.summary}</p>
                    {method.where ? (
                      <p className="mt-auto text-[11px] leading-relaxed text-text-muted">
                        {t('canvas.motionGallery.methodWhere')}{method.where}
                      </p>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
            {renderContract ? (
              <div className="mt-4 rounded-[var(--ui-radius-lg)] border border-[rgb(var(--text-rgb)/0.08)] bg-[rgb(var(--text-rgb)/0.03)] p-3">
                <p className="text-xs font-medium text-text-dark">
                  {t('canvas.motionGallery.contractTitle')}
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-text-muted">
                  {t('canvas.motionGallery.contractHint')}
                </p>
                <pre className="ui-scrollbar mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-[6px] bg-black/40 p-2 text-[11px] leading-relaxed text-text-dark/80">
                  {renderContract}
                </pre>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="flex flex-1 flex-col overflow-hidden">
            {(isStyleTab ? visibleStyles : visibleCases).length === 0 ? (
              statusBlock(emptyLabelKey)
            ) : (
              <div className="ui-scrollbar flex-1 overflow-y-auto p-4 [scrollbar-gutter:stable]">
                <div className={`grid gap-3 ${isStyleTab ? 'grid-cols-3' : 'grid-cols-4'}`}>
                  {isStyleTab
                    ? visibleStyles.map(renderStyleCard)
                    : visibleCases.map(renderCard)}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      {playing ? (
        <VideoViewerModal
          key={playing.case.id}
          open
          title={playing.case.label}
          videoUrl={playing.url}
          onClose={() => setPlaying(null)}
        />
      ) : null}
    </div>,
    document.body,
  );
}
