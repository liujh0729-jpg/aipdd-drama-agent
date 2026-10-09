// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CANVAS_NODE_TYPES, type VideoNodeData } from '@/features/canvas/domain/canvasNodes';
import { resolveImageDisplayUrl } from '@/features/canvas/application/imageData';
import { useAlbumPendingTotal } from '@/features/canvas/nodes/shared/albumPendingTotals';
import { downloadUrlAsFile } from '@/lib/browserDownload';
import { useCanvasStore } from '@/stores/canvasStore';

/**
 * 视频节点的叠卡画册状态机：收拢时探出卡片边、hover 数量徽标、点开成宫格；
 * 展开态设主视频 / 应用到画布 / 下载。自 VideoNode 组件原样抽出（行为不变），
 * 视图区见 VideoNodeAlbum（Deck / ExpandBadge / Grid）。
 */
export function useVideoNodeAlbum(
  id: string,
  data: VideoNodeData,
  resolvedWidth: number,
) {
  const updateNodeData = useCanvasStore((state) => state.updateNodeData);
  const setSelectedNode = useCanvasStore((state) => state.setSelectedNode);
  const setActiveOverlayNodeId = useCanvasStore(
    (state) => state.setActiveOverlayNodeId,
  );
  const addNode = useCanvasStore((state) => state.addNode);
  // ── 叠卡画册（count > 1 的一组生成结果，与图片节点同构）──
  // 收拢时主视频后探出 N-1 张卡片边；hover 出现右上角数量徽标，点开展开成
  // 宫格画册。展开态点视频设为主视频、可单独「应用到画布」/ 下载。
  const albumRootRef = useRef<HTMLDivElement | null>(null);
  const albumPointerDownPosRef = useRef<{ x: number; y: number } | null>(null);
  const [albumExpanded, setAlbumExpanded] = useState(false);
  // 本次会话内"应到条数"——未完成的在画册里占位。存模块级登记表而非组件
  // state：onlyRenderVisibleElements 下平移出视口会卸载组件，state 会丢。
  const albumPendingTotal = useAlbumPendingTotal(id);
  const albumUrls = useMemo(() => {
    const raw = data.generationBatch;
    if (!Array.isArray(raw)) return [];
    return raw.filter((u): u is string => typeof u === 'string' && u.length > 0);
  }, [data.generationBatch]);
  const albumTotalSlots = Math.max(albumUrls.length, albumPendingTotal);
  const albumPendingCount = Math.max(0, albumPendingTotal - albumUrls.length);
  const hasAlbum = albumTotalSlots > 1;

  // 画册展开期间注册为本节点的 activeOverlay：外部 action 工具条 / 替换素材
  // 把手 / + 派生按钮都认它让位（拖动重新选中也压得住）。
  useEffect(() => {
    if (!albumExpanded) return;
    setActiveOverlayNodeId(id);
    return () => {
      if (useCanvasStore.getState().activeOverlayNodeId === id) {
        setActiveOverlayNodeId(null);
      }
    };
  }, [albumExpanded, id, setActiveOverlayNodeId]);

  useEffect(() => {
    if (!albumExpanded) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (albumRootRef.current?.contains(event.target as Node)) return;
      setAlbumExpanded(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setAlbumExpanded(false);
    };
    window.addEventListener('pointerdown', handlePointerDown);
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('pointerdown', handlePointerDown);
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [albumExpanded]);

  const handleSetAlbumMainVideo = useCallback(
    (url: string) => {
      updateNodeData(id, { videoUrl: url, sourceFileName: null });
      setAlbumExpanded(false);
    },
    [id, updateNodeData],
  );

  // 展开画册时取消节点激活态；必须经 onNodesChange 清 React Flow 自身的
  // selected 标志（只清 store 的 selectedNodeId 会被选中同步 effect 写回）。
  // 副作用放在 setState updater 外面：updater 必须纯（StrictMode 会双调用）。
  const handleToggleAlbumExpanded = useCallback(() => {
    if (!albumExpanded) {
      const store = useCanvasStore.getState();
      const selectionChanges = store.nodes
        .filter((node) => node.selected)
        .map((node) => ({ id: node.id, type: 'select' as const, selected: false }));
      if (selectionChanges.length > 0) {
        store.onNodesChange(selectionChanges);
      }
      setSelectedNode(null);
      // 每次展开重置「应用到画布」的落点游标。
      albumAppliedCountRef.current = 0;
    }
    setAlbumExpanded(!albumExpanded);
  }, [albumExpanded, setSelectedNode]);

  // 「应用到画布」：把这条视频作为独立视频节点放到展开宫格右侧。连续应用
  // 的落点逐次错开，避免精确叠在同一坐标上只看得见最后一个。
  const albumAppliedCountRef = useRef(0);
  const handleApplyAlbumVideoToCanvas = useCallback(
    (url: string) => {
      const self = useCanvasStore.getState().nodes.find((n) => n.id === id);
      if (!self) return;
      const applyIndex = albumAppliedCountRef.current;
      albumAppliedCountRef.current += 1;
      const position = {
        x: self.position.x + resolvedWidth * 2 + 12 + 48 + applyIndex * 36,
        y: self.position.y + applyIndex * 36,
      };
      const newNodeId = addNode(CANVAS_NODE_TYPES.video, position, {
        videoUrl: url,
        aspectRatio: data.aspectRatio,
        user_spawned: true,
      } as Partial<VideoNodeData>);
      setSelectedNode(newNodeId);
    },
    [addNode, data.aspectRatio, id, resolvedWidth, setSelectedNode],
  );

  const handleDownloadAlbumVideo = useCallback(
    async (url: string, index: number) => {
      try {
        await downloadUrlAsFile(resolveImageDisplayUrl(url), `video-gen-${id}-${index + 1}.mp4`);
      } catch (error) {
        console.error('[video-node] album download failed', error);
      }
    },
    [id],
  );

  return {
    albumExpanded,
    albumRootRef,
    albumPointerDownPosRef,
    albumUrls,
    albumPendingTotal,
    albumTotalSlots,
    albumPendingCount,
    hasAlbum,
    handleSetAlbumMainVideo,
    handleToggleAlbumExpanded,
    handleApplyAlbumVideoToCanvas,
    handleDownloadAlbumVideo,
  };
}
