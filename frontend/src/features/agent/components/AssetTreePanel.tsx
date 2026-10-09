// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「资产」左栏面板：把本项目的资产库渲染成文件树（文件夹 → 素材）。
// 素材行可拖拽到对话框作为参考素材（载荷见 core 的 AGENT_ASSET_DRAG_MIME，
// 由 Composer 的 drop 收口），也可点击直接加入；剪映草稿（draft）不是可引用的
// 素材（与选材弹窗同口径）：行照常显示但不可拖、不可点。
//
// 数据链路与画布资产库同源：条目走 useFreezoneAssetLibrary（归一化后按用途类目/
// 自建文件夹落位），文件夹结构复用 buildAssetFolders——树的分组规则与画布侧
// 只有一份，避免两边漂移。
import { useMemo, useState, type DragEvent as ReactDragEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronDown,
  ChevronRight,
  Clapperboard,
  Folder,
  Image as ImageIcon,
  Loader2,
  Music,
  Video,
  type LucideIcon,
} from "lucide-react";

import {
  buildAssetFolders,
  type AssetFolder,
  type LibraryItem,
} from "@/features/canvas/ui/assetLibraryItems";
import type {
  AgentTurnReference,
  AgentTurnReferenceMedia,
} from "@/lib/queries/agent";
import {
  useFreezoneAssetLibrary,
  useFreezoneAssetLibraryFolders,
} from "@/lib/queries/freezone";
import { cn } from "@/lib/utils";

import { AGENT_ASSET_DRAG_MIME } from "../core";

/** 素材行 → 可引用的参考条目；剪映草稿与坏 URL 返回 null（不可引用）。 */
function referenceOf(item: LibraryItem): AgentTurnReference | null {
  if (item.media === "draft" || !item.url) return null;
  return { name: item.name || item.url, url: item.url, media: item.media };
}

const MEDIA_ICONS: Record<AgentTurnReferenceMedia | "draft", LucideIcon> = {
  image: ImageIcon,
  video: Video,
  audio: Music,
  draft: Clapperboard,
};

export function AssetTreePanel({
  project,
  onAddReference,
}: {
  project: string;
  /** 点击素材直接加入参考素材（拖拽之外的便捷通道）。 */
  onAddReference: (reference: AgentTurnReference) => void;
}) {
  const { t } = useTranslation();
  const libraryQuery = useFreezoneAssetLibrary(project);
  const foldersQuery = useFreezoneAssetLibraryFolders(project);
  // 反向记录「被折叠」的文件夹：默认全展开（拖拽目标立即可见），用户点了才收起来。
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());

  // 只显示有素材的文件夹：空库给一句空态，避免一列空壳文件夹占屏。
  const folders = useMemo(
    () =>
      buildAssetFolders(libraryQuery.data ?? [], t, foldersQuery.data ?? []).filter(
        (folder) => folder.items.length > 0,
      ),
    [libraryQuery.data, foldersQuery.data, t],
  );

  const toggle = (key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleDragStart = (event: ReactDragEvent<HTMLDivElement>, item: LibraryItem) => {
    const reference = referenceOf(item);
    if (!reference) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.setData(AGENT_ASSET_DRAG_MIME, JSON.stringify(reference));
    event.dataTransfer.effectAllowed = "copy";
  };

  if (libraryQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 px-3 py-3 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        {t("agent.assets.loading")}
      </div>
    );
  }

  if (folders.length === 0) {
    return (
      <p className="px-3 py-3 text-xs leading-relaxed text-muted-foreground">
        {t("agent.assets.empty")}
      </p>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-2">
      <p className="px-2 pb-1.5 text-[10px] leading-relaxed text-muted-foreground/80">
        {t("agent.assets.hint")}
      </p>
      {folders.map((folder) => (
        <FolderNode
          key={folder.key}
          folder={folder}
          collapsed={collapsed.has(folder.key)}
          onToggle={() => toggle(folder.key)}
          onAddReference={onAddReference}
          onDragStart={handleDragStart}
        />
      ))}
    </div>
  );
}

function FolderNode({
  folder,
  collapsed,
  onToggle,
  onAddReference,
  onDragStart,
}: {
  folder: AssetFolder;
  collapsed: boolean;
  onToggle: () => void;
  onAddReference: (reference: AgentTurnReference) => void;
  onDragStart: (event: ReactDragEvent<HTMLDivElement>, item: LibraryItem) => void;
}) {
  const Chevron = collapsed ? ChevronRight : ChevronDown;
  return (
    <div className="mb-0.5">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-center gap-1 rounded-lg px-2 py-1.5 text-left transition-colors hover:bg-white/[0.05]"
      >
        <Chevron className="size-3.5 shrink-0 text-muted-foreground" />
        <Folder className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-xs text-foreground">{folder.label}</span>
        <span className="shrink-0 text-[10px] text-muted-foreground">{folder.items.length}</span>
      </button>
      {collapsed ? null : (
        <div className="mt-0.5 space-y-0.5 pl-4">
          {folder.items.map((item, index) => (
            <ItemRow
              key={item.id ?? `${folder.key}-${index}`}
              item={item}
              onAddReference={onAddReference}
              onDragStart={onDragStart}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ItemRow({
  item,
  onAddReference,
  onDragStart,
}: {
  item: LibraryItem;
  onAddReference: (reference: AgentTurnReference) => void;
  onDragStart: (event: ReactDragEvent<HTMLDivElement>, item: LibraryItem) => void;
}) {
  const { t } = useTranslation();
  const reference = referenceOf(item);
  const Icon = MEDIA_ICONS[item.media] ?? ImageIcon;
  const label = item.name || item.url;
  const hint = reference ? t("agent.assets.addReference") : t("agent.assets.draftDisabled");

  return (
    <div
      role="button"
      tabIndex={reference ? 0 : -1}
      draggable={Boolean(reference)}
      aria-disabled={reference ? undefined : true}
      title={hint}
      onDragStart={(event) => onDragStart(event, item)}
      onClick={() => reference && onAddReference(reference)}
      onKeyDown={(event) => {
        if (!reference) return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onAddReference(reference);
        }
      }}
      className={cn(
        "flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors",
        reference
          ? "cursor-grab text-muted-foreground hover:bg-white/[0.05] hover:text-foreground active:cursor-grabbing"
          : "cursor-not-allowed text-muted-foreground/50",
      )}
    >
      <Icon className="size-3 shrink-0" />
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </div>
  );
}
