// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
//
// 「上传资产」弹窗：左边挑文件，右边选保存位置（必填）和标签（可选）。
//
// 保存位置 = 文件夹，标签 = 类目，两者独立——同一个文件夹里可以放不同标签的素材，
// 顶部类目 tab 按标签筛。这里只负责收集参数，真正的上传仍由 AssetLibraryModal
// 执行（进度条和失败重试都在那边的卡片上）。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useTranslation } from 'react-i18next';
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  FolderOpen,
  FolderPlus,
  FolderUp,
  Music,
  Plus,
  UploadCloud,
  X,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import type { FreezoneStyleTemplate } from '@/api/ops';
import { AssetLibraryNewFolderDialog } from './AssetLibraryNewFolderDialog';
import {
  ASSET_CATEGORIES,
  DRAFT_FOLDER_KEY,
  mediaOfFile,
  type AssetCategory,
  type AssetCategoryDef,
  type AssetFolder,
  type AssetFolderKey,
  type AssetLibraryMedia,
  type UploadableMedia,
} from './assetLibraryItems';

export interface AssetLibraryUploadPick {
  file: File;
  media: UploadableMedia;
}

export interface AssetLibraryUploadDialogProps {
  open: boolean;
  /** 可作为保存位置的文件夹（主线已被调用方剔除）。 */
  folders: AssetFolder[];
  /** 打开时默认选中的保存位置，通常是当前正在看的文件夹。 */
  defaultFolderKey?: AssetFolderKey | null;
  /** 标签选项，已按 allowedMedia 过滤。 */
  categories: AssetCategoryDef[];
  /**
   * [aipdd fork delta] 画布风格清单：保存位置或标签涉及「风格」时，作为
   * 「关联风格」选项——上传后同步把首张图片设为该风格的封面，打通
   * 资产库与画布「选择风格」图墙。
   */
  styleTemplates?: FreezoneStyleTemplate[];
  /** 调用方允许的媒介，决定 accept 与提示文案。 */
  allowedMedia?: AssetLibraryMedia[];
  /** 现场新建文件夹，返回新文件夹的 key，弹窗会直接选中它。 */
  onCreateFolder: (name: string) => Promise<AssetFolderKey>;
  onSubmit: (
    picks: AssetLibraryUploadPick[],
    folder: AssetFolderKey,
    category: AssetCategory | null,
    /** [aipdd fork delta] 关联到的画布风格 id；null = 仅存入资产库。 */
    targetStyleId: string | null,
  ) => void;
  /**
   * [aipdd fork delta] 上传整份到「剪映草稿」：一个草稿 = 一个文件夹。
   * files 是平铺后的清单（path 为去掉最外层文件夹名的相对路径）。
   */
  onSubmitDraft?: (
    files: Array<{ path: string; file: File }>,
    folderName: string,
  ) => void;
  onClose: () => void;
}

interface PickedFile extends AssetLibraryUploadPick {
  id: string;
  previewUrl: string;
}

const MEDIA_ACCEPT: Record<UploadableMedia, string> = {
  image: 'image/*',
  video: 'video/*',
  audio: 'audio/*',
};
const MEDIA_HINT: Record<UploadableMedia, string> = {
  image: 'jpg/jpeg/png/webp',
  video: 'mp4',
  audio: 'mp3/wav',
};
function pickId(): string {
  return `pick_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * [aipdd fork delta] 选中的草稿文件夹：一个草稿 = 一个文件夹（VectCut 的产物形态）。
 * files 是平铺后的清单，path 为去掉最外层文件夹名的相对路径（内部子目录原样保留）。
 */
interface DraftFolderPick {
  name: string;
  files: Array<{ path: string; file: File }>;
  totalBytes: number;
}

/** macOS 元数据与打包残留不进草稿；草稿自身的 draft_content.json 等全部保留。 */
function isIgnoredDraftName(name: string): boolean {
  return name === '.DS_Store' || name === '__MACOSX' || name.startsWith('._');
}

function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${bytes} B`;
}

/** webkitdirectory 的文件清单 → 草稿文件夹拾取；没有可用文件返回 null。 */
function collectDraftFiles(files: File[]): DraftFolderPick | null {
  const entries = files
    .map((file) => {
      const relative =
        (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
      return { file, segments: relative.split('/').filter(Boolean) };
    })
    .filter(
      ({ segments }) =>
        segments.length > 0 && segments.every((s) => !isIgnoredDraftName(s)),
    );
  if (entries.length === 0) return null;
  // 最外层文件夹名 = 草稿显示名；相对路径剥掉它，内部子目录结构原样保留。
  const named = entries.find(({ segments }) => segments.length > 1);
  const name = named?.segments[0] ?? 'draft';
  const picked = entries.map(({ file, segments }) => ({
    path: segments.length > 1 ? segments.slice(1).join('/') : segments[0],
    file,
  }));
  return {
    name,
    files: picked,
    totalBytes: picked.reduce((sum, item) => sum + item.file.size, 0),
  };
}

/** Chrome 的 readEntries 每轮最多 100 条，循环读到空批次为止。 */
async function readAllEntries(
  reader: FileSystemDirectoryReader,
): Promise<FileSystemEntry[]> {
  const out: FileSystemEntry[] = [];
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    );
    if (batch.length === 0) return out;
    out.push(...batch);
  }
}

async function collectEntryFiles(
  entry: FileSystemEntry,
  path: string,
  out: Array<{ path: string; file: File }>,
): Promise<void> {
  if (entry.isFile) {
    if (isIgnoredDraftName(entry.name)) return;
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    );
    out.push({ path, file });
    return;
  }
  if (!entry.isDirectory) return;
  const children = await readAllEntries(
    (entry as FileSystemDirectoryEntry).createReader(),
  );
  for (const child of children) {
    if (isIgnoredDraftName(child.name)) continue;
    await collectEntryFiles(child, `${path}/${child.name}`, out);
  }
}

/**
 * 拖放数据 → 草稿文件夹拾取。取第一个顶层目录作为草稿本身（其名字 = 草稿名），
 * 其余顶层项忽略；没有目录（只拖了单个文件）返回 null。
 */
async function collectDraftFromDataTransfer(
  dataTransfer: DataTransfer,
): Promise<DraftFolderPick | null> {
  const entryOf = (item: DataTransferItem): FileSystemEntry | null => {
    const getter = (
      item as DataTransferItem & { webkitGetAsEntry?: () => FileSystemEntry | null }
    ).webkitGetAsEntry;
    return typeof getter === 'function' ? getter.call(item) : null;
  };
  const entries = Array.from(dataTransfer.items)
    .filter((item) => item.kind === 'file')
    .map(entryOf)
    .filter((entry): entry is FileSystemEntry => Boolean(entry));
  const root = entries.find((entry) => entry.isDirectory) ?? null;
  if (!root) return null;
  const children = await readAllEntries(
    (root as FileSystemDirectoryEntry).createReader(),
  );
  const files: Array<{ path: string; file: File }> = [];
  for (const child of children) {
    if (isIgnoredDraftName(child.name)) continue;
    await collectEntryFiles(child, child.name, files);
  }
  if (files.length === 0) return null;
  return {
    name: root.name || 'draft',
    files,
    totalBytes: files.reduce((sum, item) => sum + item.file.size, 0),
  };
}

export function AssetLibraryUploadDialog({
  open,
  folders,
  defaultFolderKey,
  categories,
  styleTemplates = [],
  allowedMedia,
  onCreateFolder,
  onSubmit,
  onSubmitDraft,
  onClose,
}: AssetLibraryUploadDialogProps) {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // [aipdd fork delta] 草稿文件夹选择器（webkitdirectory）：一个草稿 = 一整个文件夹。
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const [draftPick, setDraftPick] = useState<DraftFolderPick | null>(null);
  const [picked, setPicked] = useState<PickedFile[]>([]);
  const pickedRef = useRef<PickedFile[]>([]);
  pickedRef.current = picked;
  const [folderKey, setFolderKey] = useState<AssetFolderKey | null>(null);
  const [category, setCategory] = useState<AssetCategory | null>(null);
  // [aipdd fork delta] 标签是否被用户手动改过。没改过时标签跟随保存位置：选到
  // 系统类目同名文件夹（如「风格」）就把该文件夹的类目当标签，普通/自建文件夹
  // 则留空。改过之后一律尊重用户的选择，不再被文件夹覆盖。
  const [categoryTouched, setCategoryTouched] = useState(false);
  const [targetStyleId, setTargetStyleId] = useState<string | null>(null);
  const [folderOpen, setFolderOpen] = useState(false);
  const [categoryOpen, setCategoryOpen] = useState(false);
  const [styleOpen, setStyleOpen] = useState(false);
  const [newFolderOpen, setNewFolderOpen] = useState(false);
  const { t } = useTranslation();
  const [isDragging, setIsDragging] = useState(false);
  const [fileError, setFileError] = useState<string | null>(null);

  const media = useMemo<UploadableMedia[]>(
    // 剪映草稿不接受上传：allowedMedia 里即使带了 draft 也在这里剔掉。
    () =>
      (allowedMedia ?? ['image', 'video', 'audio']).filter(
        (item): item is UploadableMedia => item !== 'draft',
      ),
    [allowedMedia],
  );
  const accept = media.map((m) => MEDIA_ACCEPT[m]).join(',');

  // 每次打开都从干净状态起：保存位置带上当前正在看的文件夹，标签按跟随规则
  // 同步到该文件夹（系统类目同名时），关联风格留空（不替用户拍板挂到哪套风格上）。
  useEffect(() => {
    if (!open) return;
    setFolderKey(defaultFolderKey ?? null);
    setCategoryTouched(false);
    setCategory(
      defaultFolderKey && ASSET_CATEGORIES.some((c) => c.key === defaultFolderKey)
        ? (defaultFolderKey as AssetCategory)
        : null,
    );
    setTargetStyleId(null);
    setFolderOpen(false);
    setCategoryOpen(false);
    setStyleOpen(false);
    setNewFolderOpen(false);
    setIsDragging(false);
    setFileError(null);
    setDraftPick(null);
  }, [open, defaultFolderKey]);

  useEffect(() => {
    if (open) return;
    pickedRef.current.forEach((p) => URL.revokeObjectURL(p.previewUrl));
    setPicked([]);
  }, [open]);

  useEffect(
    () => () => {
      pickedRef.current.forEach((p) => URL.revokeObjectURL(p.previewUrl));
    },
    [],
  );

  useEffect(() => {
    if (!open || newFolderOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (folderOpen || categoryOpen || styleOpen) {
        setFolderOpen(false);
        setCategoryOpen(false);
        setStyleOpen(false);
      } else {
        onClose();
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [categoryOpen, folderOpen, newFolderOpen, onClose, open, styleOpen]);

  const addFiles = useCallback(
    (files: FileList | File[]) => {
      const next: PickedFile[] = [];
      let rejected = 0;
      const known = new Set(
        pickedRef.current.map(
          (pick) => `${pick.file.name}:${pick.file.size}:${pick.file.lastModified}`,
        ),
      );
      Array.from(files).forEach((file) => {
        const kind = mediaOfFile(file);
        const signature = `${file.name}:${file.size}:${file.lastModified}`;
        if (!kind || !media.includes(kind)) {
          rejected += 1;
          return;
        }
        if (known.has(signature)) return;
        known.add(signature);
        next.push({
          id: pickId(),
          file,
          media: kind,
          previewUrl: URL.createObjectURL(file),
        });
      });
      if (next.length > 0) setPicked((prev) => [...prev, ...next]);
      setFileError(
        rejected > 0
          ? t('canvas.assetLibrary.upload.unsupportedRejected', { count: rejected })
          : null,
      );
    },
    [media],
  );

  const removePicked = useCallback((id: string) => {
    setPicked((prev) => {
      const target = prev.find((p) => p.id === id);
      if (target) URL.revokeObjectURL(target.previewUrl);
      return prev.filter((p) => p.id !== id);
    });
  }, []);

  // [aipdd fork delta] 保存位置 =「剪映草稿」时进入文件夹模式：整份草稿文件夹
  // 一次上传（VectCut 的产物形态就是文件夹），不支持零散单文件。
  const draftMode = folderKey === DRAFT_FOLDER_KEY;

  const handleDraftFilesPicked = useCallback(
    (files: File[]) => {
      const pick = collectDraftFiles(files);
      if (!pick) {
        setFileError(t('canvas.assetLibrary.upload.draftEmpty'));
        return;
      }
      setDraftPick(pick);
      setFileError(null);
    },
    [t],
  );

  const handleDraftDrop = useCallback(
    (dataTransfer: DataTransfer) => {
      void (async () => {
        try {
          const pick = await collectDraftFromDataTransfer(dataTransfer);
          if (!pick) {
            setFileError(t('canvas.assetLibrary.upload.draftNeedFolder'));
            return;
          }
          setDraftPick(pick);
          setFileError(null);
        } catch (err) {
          console.warn('[asset-library] draft drop failed', err);
          setFileError(t('canvas.assetLibrary.upload.draftNeedFolder'));
        }
      })();
    },
    [t],
  );

  if (typeof document === 'undefined' || !open) return null;

  const selectedFolder = folders.find((folder) => folder.key === folderKey);
  const selectedCategory = categories.find((c) => c.key === category);
  const canSubmit = draftMode
    ? Boolean(draftPick && draftPick.files.length > 0)
    : picked.length > 0 && Boolean(folderKey);
  // [aipdd fork delta] 标签没被手动改过时跟随保存位置；提交再兜一层底，保证
  // UI 显示和写入的一致（见下方文件夹选择处的同步逻辑）。
  const followFolderCategory = (key: AssetFolderKey | null): AssetCategory | null =>
    key && ASSET_CATEGORIES.some((c) => c.key === key)
      ? (key as AssetCategory)
      : null;
  // [aipdd fork delta] 保存位置或标签一旦落在「风格」上，就给「关联风格」入口——
  // 这两个动作在用户心智里都是「给风格传图」，不是两份各自为政的数据。
  const styleLinkVisible =
    (folderKey === 'style' || category === 'style') && styleTemplates.length > 0;
  const selectedStyle = styleTemplates.find((item) => item.id === targetStyleId);

  const handleSubmit = () => {
    if (draftMode) {
      if (!draftPick || draftPick.files.length === 0) return;
      onSubmitDraft?.(draftPick.files, draftPick.name);
      onClose();
      return;
    }
    if (!canSubmit || !folderKey) return;
    onSubmit(
      picked.map(({ file, media: kind }) => ({ file, media: kind })),
      folderKey,
      category ?? followFolderCategory(folderKey),
      styleLinkVisible ? targetStyleId : null,
    );
    onClose();
  };

  const fieldButtonClass =
    'flex h-10 w-full items-center justify-between rounded-sm border border-[var(--ui-border-soft)] bg-[rgba(var(--bg-rgb)/0.5)] px-3 text-xs transition-[border-color,background-color] hover:border-[var(--ui-border-strong)] hover:bg-[rgba(var(--bg-rgb)/0.65)]';

  return createPortal(
    <div
      className="fixed inset-0 z-[310] flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={t('canvas.assetLibrary.upload.title')}
    >
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="ui-scrollbar relative flex max-h-[88vh] w-[min(860px,92vw)] flex-col overflow-y-auto rounded-xl border border-[var(--ui-border-strong)] bg-[var(--ui-surface-modal)] shadow-[0_18px_48px_rgba(0,0,0,0.5)]">
        <div className="flex items-start justify-between gap-4 px-5 pb-3 pt-5">
          <div>
            <h3 className="text-lg font-semibold text-foreground">{t('canvas.assetLibrary.upload.title')}</h3>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('canvas.assetLibrary.upload.subtitle')}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            title={t('canvas.assetLibrary.close')}
            aria-label={t('canvas.assetLibrary.close')}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-[rgba(var(--bg-rgb)/0.5)] hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid grid-cols-1 gap-4 px-5 pb-4 md:grid-cols-[minmax(0,1fr)_260px]">
          {/* ── 左：选文件 ── */}
          <div
            className={`ui-scrollbar flex h-[260px] min-w-0 flex-col overflow-y-auto rounded-lg border bg-[rgba(var(--bg-rgb)/0.36)] transition-[border-color,background-color] md:h-[340px] ${
              isDragging
                ? 'border-primary bg-primary/5'
                : 'border-[var(--ui-border-soft)]'
            }`}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={(event) => {
              if (event.currentTarget === event.target) setIsDragging(false);
            }}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragging(false);
              if (draftMode) {
                // [aipdd fork delta] 草稿只收文件夹：单文件拖入会被拒并提示。
                handleDraftDrop(event.dataTransfer);
                return;
              }
              if (event.dataTransfer?.files?.length) {
                addFiles(event.dataTransfer.files);
              }
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              aria-label={t('canvas.assetLibrary.upload.pickLocalFiles')}
              accept={accept}
              multiple
              className="hidden"
              onChange={(event) => {
                if (event.target.files) addFiles(event.target.files);
                event.target.value = '';
              }}
            />
            {/* [aipdd fork delta] 草稿文件夹选择器：webkitdirectory 让选择器只挑目录，
                选中的整个文件夹 = 一份草稿（非标准属性经 spread 透传到 DOM）。 */}
            <input
              ref={folderInputRef}
              type="file"
              multiple
              aria-label={t('canvas.assetLibrary.upload.draftPickFolder')}
              className="hidden"
              {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
              onChange={(event) => {
                if (event.target.files) {
                  handleDraftFilesPicked(Array.from(event.target.files));
                }
                event.target.value = '';
              }}
            />
            {draftMode ? (
              draftPick ? (
                <div className="flex flex-1 flex-col p-4">
                  <div className="flex items-start gap-3 rounded-lg border border-[var(--ui-border-soft)] bg-[rgba(var(--bg-rgb)/0.5)] p-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-[rgba(var(--bg-rgb)/0.55)] text-primary">
                      <FolderOpen className="h-5 w-5" />
                    </span>
                    <div className="min-w-0 flex-1">
                      <div
                        className="truncate text-sm font-medium text-foreground"
                        title={draftPick.name}
                      >
                        {draftPick.name}
                      </div>
                      <div className="mt-0.5 text-xs text-muted-foreground">
                        {t('canvas.assetLibrary.upload.draftSummary', {
                          count: draftPick.files.length,
                          size: formatBytes(draftPick.totalBytes),
                        })}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setDraftPick(null)}
                      aria-label={t('canvas.assetLibrary.upload.draftRemove')}
                      className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-[rgba(var(--bg-rgb)/0.5)] hover:text-foreground"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                  <div className="ui-scrollbar mt-3 min-h-0 flex-1 space-y-1 overflow-y-auto rounded-md border border-[var(--ui-border-soft)] bg-[rgba(var(--bg-rgb)/0.36)] p-2">
                    {draftPick.files.slice(0, 200).map((item, index) => (
                      <div
                        key={`${item.path}:${index}`}
                        className="truncate text-[11px] text-muted-foreground"
                        title={item.path}
                      >
                        {item.path}
                      </div>
                    ))}
                    {draftPick.files.length > 200 ? (
                      <div className="text-[11px] text-muted-foreground/70">
                        {t('canvas.assetLibrary.upload.draftMoreFiles', {
                          count: draftPick.files.length - 200,
                        })}
                      </div>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    onClick={() => folderInputRef.current?.click()}
                    className="mt-2 self-start text-xs text-primary transition-colors hover:underline"
                  >
                    {t('canvas.assetLibrary.upload.draftRepick')}
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => folderInputRef.current?.click()}
                  aria-label={t('canvas.assetLibrary.upload.draftPickFolder')}
                  className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center text-muted-foreground transition-colors hover:text-foreground"
                >
                  <span className="flex h-11 w-11 items-center justify-center rounded-full bg-[rgba(var(--bg-rgb)/0.55)] text-primary">
                    <FolderUp className="h-5 w-5" />
                  </span>
                  <span>
                    <span className="block text-sm font-medium text-foreground">
                      {t('canvas.assetLibrary.upload.draftDropHint')}
                    </span>
                    <span className="mt-1.5 block text-xs text-muted-foreground">
                      {t('canvas.assetLibrary.upload.draftFolderNote')}
                    </span>
                  </span>
                </button>
              )
            ) : picked.length === 0 ? (
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                aria-label={t('canvas.assetLibrary.upload.pickFiles')}
                className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center text-muted-foreground transition-colors hover:text-foreground"
              >
                <span className="flex h-11 w-11 items-center justify-center rounded-full bg-[rgba(var(--bg-rgb)/0.55)] text-primary">
                  <UploadCloud className="h-5 w-5" />
                </span>
                <span>
                  <span className="block text-sm font-medium text-foreground">
                    {t('canvas.assetLibrary.upload.dropHint')}
                  </span>
                  <span className="mt-1.5 block text-xs text-muted-foreground">
                    {t('canvas.assetLibrary.upload.supports', { list: media.map((kind) => MEDIA_HINT[kind]).join(t('canvas.assetLibrary.upload.listSeparator')) })}
                  </span>
                </span>
              </button>
            ) : (
              <>
                <div className="flex items-center justify-between gap-3 px-4 pb-2 pt-3">
                  <span className="text-xs text-muted-foreground">
                    {t('canvas.assetLibrary.upload.selectedCount', { count: picked.length })}
                  </span>
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="inline-flex h-7 items-center gap-1 rounded-full px-2 text-xs text-primary transition-colors hover:bg-secondary"
                  >
                    <Plus className="h-3.5 w-3.5" />
                    {t('canvas.assetLibrary.upload.addMore')}
                  </button>
                </div>
                <div className="flex flex-wrap gap-2.5 px-4 pb-4">
                  {picked.map((p) => (
                    <div
                      key={p.id}
                      className="group relative h-[104px] w-[104px] overflow-hidden rounded-sm border border-[var(--ui-border-soft)] bg-[rgba(var(--bg-rgb)/0.55)]"
                      title={p.file.name}
                    >
                      {p.media === 'image' ? (
                        <img
                          src={p.previewUrl}
                          alt=""
                          className="h-full w-full object-cover"
                          draggable={false}
                        />
                      ) : p.media === 'video' ? (
                        <video
                          src={p.previewUrl}
                          className="h-full w-full object-cover"
                          muted
                          playsInline
                          preload="metadata"
                        />
                      ) : (
                        <div className="flex h-full w-full items-center justify-center text-muted-foreground">
                          <Music className="h-8 w-8" />
                        </div>
                      )}
                      <div className="pointer-events-none absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/85 to-black/30 px-2 pb-1.5 pt-5 text-xs text-white/90">
                        {p.file.name}
                      </div>
                      <button
                        type="button"
                        onClick={() => removePicked(p.id)}
                        aria-label={t('canvas.assetLibrary.upload.removeFile', { name: p.file.name })}
                        className="absolute right-1.5 top-1.5 inline-flex h-6 w-6 items-center justify-center rounded-full bg-black/60 text-white opacity-0 transition-opacity hover:bg-black/80 group-hover:opacity-100 focus:opacity-100"
                      >
                        <X className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                </div>
              </>
            )}
            {fileError ? (
              <div
                role="alert"
                className="mx-4 mb-3 flex items-center gap-1.5 text-xs text-destructive"
              >
                <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                {fileError}
              </div>
            ) : null}
          </div>

          {/* ── 右：保存位置 + 标签 ── */}
          <div className="rounded-lg border border-[var(--ui-border-soft)] bg-[rgba(var(--bg-rgb)/0.28)] p-4">
            <h4 className="text-sm font-semibold text-foreground">{t('canvas.assetLibrary.upload.settings')}</h4>
            <p className="mt-1 text-xs text-muted-foreground">
              {t('canvas.assetLibrary.upload.settingsHint')}
            </p>
            <div className="mt-4 space-y-4">
              <div className="relative">
                <div className="mb-1.5 text-xs font-medium text-foreground">
                  {t('canvas.assetLibrary.upload.destination')} <span className="text-destructive">*</span>
                </div>
                <button
                  type="button"
                  aria-label={t('canvas.assetLibrary.upload.pickDestination')}
                  onClick={() => {
                    setFolderOpen((prev) => !prev);
                    setCategoryOpen(false);
                  }}
                  className={fieldButtonClass}
                >
                  <span
                    className={
                      selectedFolder
                        ? 'text-foreground'
                        : 'text-muted-foreground'
                    }
                  >
                    {selectedFolder?.label ?? t('canvas.assetLibrary.upload.chooseFolder')}
                  </span>
                  {folderOpen ? (
                    <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                  )}
                </button>
                {folderOpen && (
                  <div className="absolute z-10 mt-1 w-full overflow-hidden rounded-lg border border-[var(--ui-border-soft)] bg-[rgba(var(--surface-rgb)/0.99)] py-1 shadow-[0_12px_28px_rgba(0,0,0,0.45)]">
                    <div className="flex items-center justify-between px-3 py-2 text-xs font-medium text-foreground">
                      {t('canvas.assetLibrary.upload.projectLibrary')}
                      <button
                        type="button"
                        aria-label={t('canvas.assetLibrary.newFolder')}
                        title={t('canvas.assetLibrary.newFolder')}
                        onClick={() => setNewFolderOpen(true)}
                        className="inline-flex h-7 items-center gap-1 rounded-full px-2 text-primary transition-colors hover:bg-secondary"
                      >
                        <FolderPlus className="h-3.5 w-3.5" />
                        <span>{t('canvas.assetLibrary.upload.create')}</span>
                      </button>
                    </div>
                    <div className="ui-scrollbar max-h-[180px] overflow-y-auto">
                      {folders.map((folder) => (
                        <button
                          key={folder.key}
                          type="button"
                          onClick={() => {
                            setFolderKey(folder.key);
                            // [aipdd fork delta] 用户还没手动选过标签时让它跟随：
                            // 落到哪种系统类目文件夹，标签就是那个类目（「风格」
                            // 文件夹 → 「风格」标签），顶部同名标签下立刻能看到。
                            if (!categoryTouched) {
                              setCategory(followFolderCategory(folder.key));
                            }
                            setFolderOpen(false);
                          }}
                          className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-accent ${
                            folder.key === folderKey
                              ? 'text-foreground'
                              : 'text-muted-foreground'
                          }`}
                        >
                          <span className="truncate">{folder.label}</span>
                          {folder.key === folderKey ? (
                            <Check className="h-3.5 w-3.5 shrink-0 text-primary" />
                          ) : null}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* [aipdd fork delta] 草稿模式：不选标签、也不关联风格——一个草稿
                  就是一份整体产物，标签固定「剪映草稿」。 */}
              {draftMode ? (
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {t('canvas.assetLibrary.upload.draftNote')}
                </p>
              ) : (
              <div className="relative">
                <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-foreground">
                  {t('canvas.assetLibrary.upload.tag')}{' '}
                  <span className="font-normal text-muted-foreground">{t('canvas.assetLibrary.upload.optional')}</span>
                </div>
                <button
                  type="button"
                  aria-label={t('canvas.assetLibrary.upload.pickTag')}
                  onClick={() => {
                    setCategoryOpen((prev) => !prev);
                    setFolderOpen(false);
                  }}
                  className={fieldButtonClass}
                >
                  <span
                    className={
                      selectedCategory
                        ? 'text-foreground'
                        : 'text-muted-foreground'
                    }
                  >
                    {selectedCategory
                      ? t(selectedCategory.labelKey)
                      : t('canvas.assetLibrary.upload.choose')}
                  </span>
                  {categoryOpen ? (
                    <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                  )}
                </button>
                {categoryOpen && (
                  <div className="ui-scrollbar absolute z-10 mt-1 max-h-[180px] w-full overflow-y-auto rounded-lg border border-[var(--ui-border-soft)] bg-[rgba(var(--surface-rgb)/0.99)] py-1 shadow-[0_12px_28px_rgba(0,0,0,0.45)]">
                    {categories.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        onClick={() => {
                          setCategory(item.key);
                          setCategoryTouched(true);
                          setCategoryOpen(false);
                        }}
                        className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-accent ${
                          item.key === category
                            ? 'text-foreground'
                            : 'text-muted-foreground'
                        }`}
                      >
                        <span>{t(item.labelKey)}</span>
                        {item.key === category ? (
                          <Check className="h-3.5 w-3.5 text-primary" />
                        ) : null}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              )}

              {/* [aipdd fork delta] 关联风格：上传后把首张图片设为该风格的封面，
                  画布「选择风格」图墙立即能看到（也可在图墙里直接管理）。 */}
              {!draftMode && styleLinkVisible ? (
                <div className="relative">
                  <div className="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-foreground">
                    {t('canvas.assetLibrary.upload.linkStyle')}{' '}
                    <span className="font-normal text-muted-foreground">
                      {t('canvas.assetLibrary.upload.optional')}
                    </span>
                  </div>
                  <button
                    type="button"
                    aria-label={t('canvas.assetLibrary.upload.pickStyle')}
                    onClick={() => {
                      setStyleOpen((prev) => !prev);
                      setFolderOpen(false);
                      setCategoryOpen(false);
                    }}
                    className={fieldButtonClass}
                  >
                    <span
                      className={
                        selectedStyle ? 'truncate text-foreground' : 'text-muted-foreground'
                      }
                    >
                      {selectedStyle
                        ? `${selectedStyle.label}（${selectedStyle.category}）`
                        : t('canvas.assetLibrary.upload.linkStyleNone')}
                    </span>
                    {styleOpen ? (
                      <ChevronUp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    ) : (
                      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    )}
                  </button>
                  {styleOpen && (
                    <div className="ui-scrollbar absolute z-10 mt-1 max-h-[220px] w-full overflow-y-auto rounded-lg border border-[var(--ui-border-soft)] bg-[rgba(var(--surface-rgb)/0.99)] py-1 shadow-[0_12px_28px_rgba(0,0,0,0.45)]">
                      <button
                        type="button"
                        onClick={() => {
                          setTargetStyleId(null);
                          setStyleOpen(false);
                        }}
                        className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-accent ${
                          targetStyleId === null ? 'text-foreground' : 'text-muted-foreground'
                        }`}
                      >
                        <span>{t('canvas.assetLibrary.upload.linkStyleNone')}</span>
                        {targetStyleId === null ? (
                          <Check className="h-3.5 w-3.5 text-primary" />
                        ) : null}
                      </button>
                      {styleTemplates.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => {
                            setTargetStyleId(item.id);
                            setStyleOpen(false);
                          }}
                          className={`flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs transition-colors hover:bg-accent ${
                            item.id === targetStyleId
                              ? 'text-foreground'
                              : 'text-muted-foreground'
                          }`}
                        >
                          <span className="truncate">
                            {item.label}
                            <span className="ml-1.5 text-muted-foreground/80">
                              {item.category}
                            </span>
                          </span>
                          {item.id === targetStyleId ? (
                            <Check className="h-3.5 w-3.5 shrink-0 text-primary" />
                          ) : null}
                        </button>
                      ))}
                    </div>
                  )}
                  {selectedStyle ? (
                    <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
                      {t('canvas.assetLibrary.upload.linkStyleHint')}
                    </p>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 px-5 pb-5">
          <Button
            size="sm"
            variant="ghost"
            className="px-4 text-muted-foreground hover:text-foreground"
            onClick={onClose}
          >
            {t('common.cancel')}
          </Button>
          <Button
            size="sm"
            className="bg-primary px-4 text-primary-foreground hover:bg-primary/90"
            disabled={!canSubmit}
            onClick={handleSubmit}
          >
            {draftMode
              ? t('canvas.assetLibrary.upload.draftSubmit')
              : picked.length > 0
                ? t('canvas.assetLibrary.upload.submitCount', { count: picked.length })
                : t('canvas.assetLibrary.upload.submit')}
          </Button>
        </div>
      </div>

      <AssetLibraryNewFolderDialog
        open={newFolderOpen}
        z={330}
        onClose={() => setNewFolderOpen(false)}
        onSubmit={async (name) => {
          const key = await onCreateFolder(name);
          setFolderKey(key);
          // 自建文件夹没有类目语义：没手动选过标签就清空，回到不猜的状态。
          if (!categoryTouched) setCategory(followFolderCategory(key));
          setNewFolderOpen(false);
          setFolderOpen(false);
        }}
      />
    </div>,
    document.body,
  );
}
