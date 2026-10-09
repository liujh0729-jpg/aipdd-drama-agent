// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { RefObject } from 'react';
import { ChevronDown, Download, Loader2, Upload as UploadIcon, Video as VideoIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';

interface VideoNodeAlbumDeckProps {
  albumTotalSlots: number;
  handleToggleAlbumExpanded: () => void;
}

/** 收拢态的叠卡卡片边（N-1 张，最多 3 张）：点卡边展开画册。 */
export function VideoNodeAlbumDeck({
  albumTotalSlots,
  handleToggleAlbumExpanded,
}: VideoNodeAlbumDeckProps) {
  const { t } = useTranslation();
  return (
    <>
      {Array.from({ length: Math.min(albumTotalSlots - 1, 3) }, (_, index) => {
        const step = index + 1;
        return (
          <div
            key={`album-deck-${index}`}
            role="button"
            tabIndex={-1}
            title={t("node.videoNode.album.expand")}
            onClick={(event) => {
              event.stopPropagation();
              handleToggleAlbumExpanded();
            }}
            className="absolute cursor-pointer rounded-[var(--node-radius)] border border-white/[0.18] bg-gradient-to-b from-[#48484d] to-[#2d2d31] shadow-[0_4px_14px_rgba(0,0,0,0.4)]"
            style={{
              top: step * 7,
              bottom: step * 7,
              left: step * 6,
              right: -step * 7,
              transform: `rotate(${step * 1.1}deg)`,
              transformOrigin: 'center right',
              opacity: 1 - step * 0.18,
            }}
          />
        );
      })}
    </>
  );
}

interface VideoNodeAlbumExpandBadgeProps {
  albumUrls: string[];
  albumPendingTotal: number;
  albumPendingCount: number;
  albumTotalSlots: number;
  albumExpanded: boolean;
  handleToggleAlbumExpanded: () => void;
}

/** 画册数量徽标：hover 节点出现，hover 徽标箭头下探，点击展开画册。 */
export function VideoNodeAlbumExpandBadge({
  albumUrls,
  albumPendingTotal,
  albumPendingCount,
  albumTotalSlots,
  albumExpanded,
  handleToggleAlbumExpanded,
}: VideoNodeAlbumExpandBadgeProps) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        handleToggleAlbumExpanded();
      }}
      onPointerDown={(event) => event.stopPropagation()}
      title={t("node.videoNode.album.expandCount", { count: albumTotalSlots })}
      className="nodrag group/albumpill absolute right-2 top-2 z-10 hidden items-center gap-1 rounded-full bg-black/65 px-2.5 py-1 text-[12px] font-medium tabular-nums text-white shadow-lg backdrop-blur-sm transition-colors hover:bg-black/85 group-hover:inline-flex"
    >
      {albumPendingCount > 0
        ? `${albumUrls.length}/${albumPendingTotal}`
        : albumUrls.length}
      <ChevronDown
        className={`h-3.5 w-3.5 transition-transform duration-200 ${
          albumExpanded
            ? 'rotate-180 group-hover/albumpill:-translate-y-[2px]'
            : 'group-hover/albumpill:translate-y-[2px]'
        }`}
      />
    </button>
  );
}

interface VideoNodeAlbumGridProps {
  albumUrls: string[];
  videoUrl: string | null;
  resolvedWidth: number;
  resolvedHeight: number;
  albumPendingCount: number;
  albumTotalSlots: number;
  albumPointerDownPosRef: RefObject<{ x: number; y: number } | null>;
  handleSetAlbumMainVideo: (url: string) => void;
  handleApplyAlbumVideoToCanvas: (url: string) => void;
  handleDownloadAlbumVideo: (url: string, index: number) => void;
}

/** 展开态的宫格画册：点视频设主视频并收拢；hover 出现「应用到画布」+ 下载。 */
export function VideoNodeAlbumGrid({
  albumUrls,
  videoUrl,
  resolvedWidth,
  resolvedHeight,
  albumPendingCount,
  albumTotalSlots,
  albumPointerDownPosRef,
  handleSetAlbumMainVideo,
  handleApplyAlbumVideoToCanvas,
  handleDownloadAlbumVideo,
}: VideoNodeAlbumGridProps) {
  const { t } = useTranslation();
  return (
    <div
      className="nowheel absolute -left-3 -top-3 z-[80] cursor-grab rounded-2xl border border-white/15 bg-white/[0.045] p-3 shadow-[0_16px_48px_rgba(0,0,0,0.4)] backdrop-blur-[2px] active:cursor-grabbing"
      style={{ width: resolvedWidth * 2 + 12 + 24 }}
      onClick={(event) => event.stopPropagation()}
      onPointerDownCapture={(event) => {
        albumPointerDownPosRef.current = { x: event.clientX, y: event.clientY };
      }}
    >
      <div className="mb-2 flex items-center gap-1.5 px-1 text-[12px] font-medium text-white/60">
        <VideoIcon className="h-3.5 w-3.5 text-white/45" />
        {t("node.videoNode.album.heading", { count: albumTotalSlots })}
      </div>
      <div className="grid grid-cols-2 gap-3">
        {albumUrls.map((url, index) => {
          const isMain = url === videoUrl;
          return (
            <div
              key={`album-cell-${index}`}
              role="button"
              tabIndex={-1}
              title={t("node.videoNode.album.setAsMain")}
              onClick={(event) => {
                event.stopPropagation();
                // 拖动画册（移动节点）后松手补发的 click 不算选主视频。
                const start = albumPointerDownPosRef.current;
                if (
                  start
                  && Math.hypot(event.clientX - start.x, event.clientY - start.y) > 5
                ) {
                  return;
                }
                handleSetAlbumMainVideo(url);
              }}
              className={`group/albumcell relative cursor-pointer overflow-hidden rounded-[var(--node-radius)] border bg-[#1b1b1d] shadow-[0_12px_32px_rgba(0,0,0,0.45)] transition-colors ${
                isMain
                  ? 'border-accent/80 ring-2 ring-accent/40'
                  : 'border-white/12 hover:border-white/35'
              }`}
              style={{ width: resolvedWidth, height: resolvedHeight }}
            >
              <video
                src={resolveImageDisplayUrl(url)}
                muted
                playsInline
                preload="metadata"
                className="h-full w-full object-cover"
                onMouseEnter={(event) => {
                  void event.currentTarget.play().catch(() => undefined);
                }}
                onMouseLeave={(event) => {
                  event.currentTarget.pause();
                  event.currentTarget.currentTime = 0;
                }}
              />
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  handleApplyAlbumVideoToCanvas(url);
                }}
                title={t("node.videoNode.album.applyToCanvas")}
                className="nodrag absolute left-2 top-2 z-10 hidden h-7 items-center gap-1 rounded-md bg-black/70 px-2.5 text-[12px] font-medium text-white backdrop-blur-sm transition-colors hover:bg-black/90 group-hover/albumcell:inline-flex"
              >
                <UploadIcon className="h-3.5 w-3.5" />
                {t("node.videoNode.album.applyToCanvasLabel")}
              </button>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  void handleDownloadAlbumVideo(url, index);
                }}
                title={t("node.videoNode.album.download")}
                className="nodrag absolute right-2 top-2 z-10 hidden h-7 w-7 items-center justify-center rounded-full bg-black/70 text-white backdrop-blur-sm transition-colors hover:bg-black/90 group-hover/albumcell:inline-flex"
              >
                <Download className="h-3.5 w-3.5" />
              </button>
              {isMain && (
                <span className="absolute bottom-2 left-2 z-10 rounded-md bg-black/65 px-2 py-0.5 text-[11px] font-medium text-white backdrop-blur-sm">
                  {t("node.videoNode.album.mainVideo")}
                </span>
              )}
            </div>
          );
        })}
        {/* 还在生成中的槽位：占位骨架，完成一条替换一条。 */}
        {Array.from({ length: albumPendingCount }, (_, index) => (
          <div
            key={`album-pending-${index}`}
            className="relative flex items-center justify-center overflow-hidden rounded-[var(--node-radius)] border border-white/10 bg-[#1b1b1d] shadow-[0_12px_32px_rgba(0,0,0,0.45)]"
            style={{ width: resolvedWidth, height: resolvedHeight }}
          >
            <div className="flex flex-col items-center gap-2 text-text-muted/70">
              <Loader2 className="h-6 w-6 animate-spin" />
              <span className="text-[12px]">{t("node.videoNode.album.generating")}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
