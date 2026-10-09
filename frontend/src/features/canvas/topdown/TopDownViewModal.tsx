// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowUpRight,
  Camera,
  Eraser,
  Download,
  LayoutGrid,
  LogOut,
  MousePointer2,
  Save,
  Square,
  Trash2,
  Type,
  Upload,
  User,
} from "lucide-react";
import { toast } from "sonner";

import {
  submitFreezoneAddVideoCharacterLibraryItem,
  uploadFreezoneImage,
} from "@/api/ops";
import { dataUrlToBlob } from "@/features/canvas/application/imageData";
import { useViewerImmersiveBody } from "@/features/viewer-kit/useViewerImmersiveBody";
import { clientToScenePoint, TopDownCanvas } from "./TopDownCanvas";
import { exportSceneToUrl, renderSceneToPngDataUrl } from "./renderScene";
import {
  createEmptyScene,
  decodeScene,
  encodeScene,
  nextItemId,
  TOPDOWN_PALETTE,
  type TopDownItem,
  type TopDownPoint,
  type TopDownPropShape,
  type TopDownScene,
} from "./sceneModel";

type Tool = "select" | "person" | "camera" | "text" | "arrow" | "prop";

export interface TopDownViewModalProps {
  project: string;
  initialScene?: unknown;
  baseImageUrl?: string | null;
  onClose: () => void;
  /** 关闭 / 关键操作时回传当前场景，供宿主持久化为草稿。 */
  onPersistDraft: (scene: TopDownScene) => void;
  /** 手动导出成功：回传渲染图静态地址（宿主写回本节点 imageUrl）。 */
  onExported: (url: string) => void;
}

const PROP_TOOLS: Array<{ shape: TopDownPropShape; labelKey: string }> = [
  { shape: "sofa", labelKey: "topDown.prop.sofa" },
  { shape: "table", labelKey: "topDown.prop.table" },
  { shape: "tv", labelKey: "topDown.prop.tv" },
  { shape: "door", labelKey: "topDown.prop.door" },
  { shape: "window", labelKey: "topDown.prop.window" },
  { shape: "stairs", labelKey: "topDown.prop.stairs" },
  { shape: "rect", labelKey: "topDown.prop.rect" },
];

// [aipdd fork delta] 需求：放置家具/人物时自带默认名（沙发/门/窗/人物N），一放就有
// 文字标记，用户仍可在右侧属性面板改。defaultLabel 由调用方按当前工具算好传入。
function makeItem(tool: Tool, shape: TopDownPropShape, p: TopDownPoint, defaultLabel: string): TopDownItem | null {
  switch (tool) {
    case "person":
      return { id: nextItemId("p"), kind: "person", x: p.x, y: p.y, label: defaultLabel, color: TOPDOWN_PALETTE[0] };
    case "camera":
      return { id: nextItemId("c"), kind: "camera", x: p.x, y: p.y, label: defaultLabel || "Cam", angleDeg: 0 };
    case "text":
      // 新建文本元素的初始内容（用户随即可在属性面板改写，非界面文案）。
      return { id: nextItemId("t"), kind: "text", x: p.x, y: p.y, text: defaultLabel || "文字", color: TOPDOWN_PALETTE[5] }; // i18n-exempt
    case "prop":
      return { id: nextItemId("r"), kind: "prop", x: p.x - 60, y: p.y - 40, w: 120, h: 80, shape, label: defaultLabel };
    default:
      return null;
  }
}

export function TopDownViewModal({
  project,
  initialScene,
  baseImageUrl,
  onClose,
  onPersistDraft,
  onExported,
}: TopDownViewModalProps) {
  const { t } = useTranslation();
  useViewerImmersiveBody(true);

  const [scene, setScene] = useState<TopDownScene>(() => {
    const decoded = decodeScene(initialScene);
    if (decoded.background.kind === "none" && baseImageUrl) {
      return { ...decoded, background: { kind: "image", url: baseImageUrl } };
    }
    return decoded;
  });
  const [tool, setTool] = useState<Tool>("select");
  const [propShape, setPropShape] = useState<TopDownPropShape>("sofa");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draftArrow, setDraftArrow] = useState<TopDownPoint[]>([]);
  const [busy, setBusy] = useState<null | "export" | "save">(null);
  const [saveAsOpen, setSaveAsOpen] = useState(false);
  const [draftName, setDraftName] = useState("");

  const svgRef = useRef<SVGSVGElement | null>(null);
  const sceneRef = useRef(scene);
  sceneRef.current = scene;
  const draftRef = useRef(draftArrow);
  draftRef.current = draftArrow;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // 常驻最新回调，供下面的自动存草稿 effect 使用（避免把它写进依赖导致回环）。
  const persistRef = useRef(onPersistDraft);
  persistRef.current = onPersistDraft;

  const selected = useMemo(
    () => scene.items.find((it) => it.id === selectedId) ?? null,
    [scene.items, selectedId],
  );

  // 场景一变就回写节点草稿：摆放 / 改名 / 改色 / 拖拽全程都持久化，
  // 下次再进入编辑器即可看到并可继续修改（口径同视频合成的 draftTimeline）。
  useEffect(() => {
    persistRef.current(scene);
  }, [scene]);

  const persist = useCallback(
    (next: TopDownScene) => {
      setScene(next);
      onPersistDraft(next);
    },
    [onPersistDraft],
  );

  const updateItem = useCallback(
    (id: string, updater: (item: TopDownItem) => TopDownItem) => {
      setScene((prev) => ({
        ...prev,
        items: prev.items.map((it) => (it.id === id ? updater(it) : it)),
      }));
    },
    [],
  );

  // 属性面板用：把一组松散字段并入指定元素（避开可辨识联合上 Partial 的类型问题）。
  const patchItem = useCallback(
    (id: string, patch: Record<string, unknown>) => {
      setScene((prev) => ({
        ...prev,
        items: prev.items.map((it) => (it.id === id ? ({ ...it, ...patch } as TopDownItem) : it)),
      }));
    },
    [],
  );

  const getPoint = useCallback((e: { clientX: number; clientY: number }): TopDownPoint => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    return clientToScenePoint(svg, e.clientX, e.clientY, sceneRef.current.size);
  }, []);

  const commitArrow = useCallback(() => {
    const pts = draftRef.current;
    if (pts.length >= 2) {
      setScene((prev) => ({
        ...prev,
        items: [
          ...prev.items,
          { id: nextItemId("a"), kind: "arrow", points: pts, color: TOPDOWN_PALETTE[0] },
        ],
      }));
    }
    setDraftArrow([]);
    setTool("select");
  }, []);

  const onPointerDownBackground = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      const p = getPoint(e);
      if (tool === "arrow") {
        setDraftArrow((prev) => [...prev, p]);
        return;
      }
      if (tool === "select") {
        setSelectedId(null);
        return;
      }
      // [aipdd fork delta] 新建时算默认名：家具→本地化形状名，人物→「人物N」（按已有
      // 人数 +1）。
      let defaultLabel = "";
      if (tool === "prop") defaultLabel = t(`topDown.prop.${propShape}`);
      else if (tool === "person") {
        const n = sceneRef.current.items.filter((it) => it.kind === "person").length + 1;
        defaultLabel = t("topDown.personDefault", { index: n });
      }
      const item = makeItem(tool, propShape, p, defaultLabel);
      if (item) {
        setScene((prev) => ({ ...prev, items: [...prev.items, item] }));
        setSelectedId(item.id);
        setTool("select");
      }
    },
    [tool, propShape, getPoint, t],
  );

  const onDoubleClickBackground = useCallback(() => {
    if (tool === "arrow" && draftArrow.length >= 2) commitArrow();
  }, [tool, draftArrow.length, commitArrow]);

  // 拖拽（移动 / 旋转）——window 级监听，指针移出 svg 也连续。
  const startDrag = useCallback(
    (mode: "move" | "rotate", id: string, e: ReactPointerEvent) => {
      const start = getPoint(e);
      const snapshot = sceneRef.current.items.find((it) => it.id === id);
      if (!snapshot) return;

      const onMove = (ev: PointerEvent) => {
        const cur = getPoint(ev);
        if (mode === "rotate") {
          if (snapshot.kind !== "camera") return;
          const deg = (Math.atan2(cur.y - snapshot.y, cur.x - snapshot.x) * 180) / Math.PI;
          updateItem(id, (it) => (it.kind === "camera" ? { ...it, angleDeg: Math.round(deg) } : it));
          return;
        }
        const dx = cur.x - start.x;
        const dy = cur.y - start.y;
        updateItem(id, (it) => {
          if (it.kind === "arrow") {
            return { ...it, points: it.points.map((pt) => ({ x: pt.x + dx, y: pt.y + dy })) };
          }
          return { ...it, x: it.x + dx, y: it.y + dy };
        });
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        onPersistDraft(sceneRef.current);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [getPoint, updateItem, onPersistDraft],
  );

  const onPointerDownItem = useCallback(
    (id: string, e: ReactPointerEvent) => {
      setSelectedId(id);
      if (tool === "select") startDrag("move", id, e);
    },
    [tool, startDrag],
  );

  const onPointerDownRotate = useCallback(
    (id: string, e: ReactPointerEvent) => startDrag("rotate", id, e),
    [startDrag],
  );

  const deleteSelected = useCallback(() => {
    if (!selectedId) return;
    persist({ ...sceneRef.current, items: sceneRef.current.items.filter((it) => it.id !== selectedId) });
    setSelectedId(null);
  }, [selectedId, persist]);

  const clearAll = useCallback(() => {
    persist({ ...createEmptyScene(), size: sceneRef.current.size, background: sceneRef.current.background });
    setSelectedId(null);
  }, [persist]);

  const onUploadBackground = useCallback(
    async (file: File) => {
      try {
        const uploaded = await uploadFreezoneImage(project, file, file.name);
        persist({ ...sceneRef.current, background: { kind: "image", url: uploaded.url, opacity: 0.85 } });
      } catch (err) {
        console.warn("[topdown] background upload failed", err);
      }
    },
    [project, persist],
  );

  const runExport = useCallback(async () => {
    if (busy) return;
    setBusy("export");
    try {
      const url = await exportSceneToUrl(sceneRef.current, `topdown-${Date.now()}.png`);
      onExported(url);
    } catch (err) {
      console.warn("[topdown] render/export failed", err);
    } finally {
      setBusy(null);
    }
  }, [busy, onExported]);

  // 另存为草稿：渲染当前场景作预览图上传，连可编辑场景一起登记到资产库「俯视图」类目。
  const runSaveAsDraft = useCallback(async () => {
    if (busy) return;
    const name = draftName.trim();
    if (!name) return;
    setBusy("save");
    try {
      const dataUrl = await renderSceneToPngDataUrl(sceneRef.current);
      const uploaded = await uploadFreezoneImage(
        project,
        dataUrlToBlob(dataUrl),
        `topdown-${Date.now()}.png`,
      );
      await submitFreezoneAddVideoCharacterLibraryItem(project, {
        name,
        media: "image",
        imageUrls: [uploaded.url],
        category: "topdown",
        scene: encodeScene(sceneRef.current),
      });
      toast.success(t("topDown.savedToast", { name }));
      setSaveAsOpen(false);
      setDraftName("");
    } catch (err) {
      console.warn("[topdown] save-as-draft failed", err);
      toast.error(t("topDown.saveAsFailed"));
    } finally {
      setBusy(null);
    }
  }, [busy, draftName, project, t]);

  // Esc：先收起另存为弹框，再取消未完成的箭头，否则关闭。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (saveAsOpen) {
        setSaveAsOpen(false);
        return;
      }
      if (draftArrow.length > 0) {
        setDraftArrow([]);
        setTool("select");
        return;
      }
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saveAsOpen, draftArrow.length, onClose]);

  const toolButton = (value: Tool, icon: React.ReactNode, label: string) => (
    <button
      type="button"
      onClick={() => {
        if (draftArrow.length > 0) setDraftArrow([]);
        setTool(value);
        setSelectedId(null);
      }}
      className={`flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] transition-colors ${
        tool === value ? "bg-accent/25 text-accent" : "text-text-dark/80 hover:bg-white/[0.06]"
      }`}
    >
      <span className="shrink-0">{icon}</span>
      <span className="truncate">{label}</span>
    </button>
  );

  return createPortal(
    <div className="fixed inset-0 z-[120] flex flex-col bg-bg-dark/95 backdrop-blur-sm">
      {/* 顶栏 */}
      <div className="flex shrink-0 items-center gap-3 border-b border-white/10 px-5 py-3">
        <MapIcon />
        <span className="text-[15px] font-semibold text-white/90">{t("topDown.title")}</span>
        <span className="ml-auto flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setDraftName("");
              setSaveAsOpen(true);
            }}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-lg border border-white/15 bg-white/[0.04] px-3 py-1.5 text-[13px] text-text-dark hover:bg-white/[0.08] disabled:opacity-50"
          >
            <Save className="h-4 w-4" />
            {t("topDown.saveAs")}
          </button>
          <button
            type="button"
            onClick={() => void runExport()}
            disabled={busy !== null}
            className="flex items-center gap-1.5 rounded-lg border border-white/15 bg-white/[0.04] px-3 py-1.5 text-[13px] text-text-dark hover:bg-white/[0.08] disabled:opacity-50"
          >
            <Download className="h-4 w-4" />
            {t("topDown.export")}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-[13px] text-white/70 hover:bg-white/[0.08]"
          >
            <LogOut className="h-4 w-4" />
            {t("topDown.close")}
          </button>
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* 左：工具面板 */}
        <div className="flex w-52 shrink-0 flex-col gap-4 overflow-y-auto border-r border-white/10 p-3">
          <section className="flex flex-col gap-1">
            {toolButton("select", <MousePointer2 className="h-4 w-4" />, t("topDown.tool.select"))}
            {toolButton("person", <User className="h-4 w-4" />, t("topDown.tool.person"))}
            {toolButton("camera", <Camera className="h-4 w-4" />, t("topDown.tool.camera"))}
            {toolButton("arrow", <ArrowUpRight className="h-4 w-4" />, t("topDown.tool.arrow"))}
            {toolButton("text", <Type className="h-4 w-4" />, t("topDown.tool.text"))}
          </section>
          <section>
            <div className="mb-1 flex items-center gap-1.5 px-1 text-[12px] font-semibold text-white/50">
              <LayoutGrid className="h-3.5 w-3.5" />
              {t("topDown.props")}
            </div>
            <div className="grid grid-cols-2 gap-1">
              {PROP_TOOLS.map((p) => (
                <button
                  key={p.shape}
                  type="button"
                  onClick={() => {
                    setPropShape(p.shape);
                    setTool("prop");
                    setSelectedId(null);
                  }}
                  className={`rounded-lg px-2 py-1.5 text-[12px] transition-colors ${
                    tool === "prop" && propShape === p.shape
                      ? "bg-accent/25 text-accent"
                      : "text-text-dark/80 hover:bg-white/[0.06]"
                  }`}
                >
                  {t(p.labelKey)}
                </button>
              ))}
            </div>
          </section>
          <section className="mt-auto flex flex-col gap-1">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center gap-2 rounded-lg border border-white/15 bg-white/[0.04] px-3 py-2 text-[13px] text-text-dark hover:bg-white/[0.08]"
            >
              <Upload className="h-4 w-4" />
              {t("topDown.uploadBase")}
            </button>
            <button
              type="button"
              onClick={clearAll}
              className="flex items-center gap-2 rounded-lg px-3 py-2 text-[13px] text-white/60 hover:bg-white/[0.06]"
            >
              <Eraser className="h-4 w-4" />
              {t("topDown.clear")}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void onUploadBackground(file);
                e.target.value = "";
              }}
            />
          </section>
        </div>

        {/* 中：画布 */}
        <div className="flex min-w-0 flex-1 items-center justify-center overflow-auto p-6">
          <div className="w-full max-w-[min(100%,calc(100vh-160px))]">
            <TopDownCanvas
              scene={scene}
              selectedId={selectedId}
              draftArrow={draftArrow}
              svgRef={svgRef}
              onPointerDownBackground={onPointerDownBackground}
              onPointerDownItem={onPointerDownItem}
              onPointerDownRotate={onPointerDownRotate}
              onDoubleClickBackground={onDoubleClickBackground}
            />
            {tool === "arrow" ? (
              <div className="mt-2 flex items-center justify-between rounded-lg bg-white/[0.04] px-3 py-1.5 text-[12px] text-white/60">
                <span>{t("topDown.arrowHint")}</span>
                <button
                  type="button"
                  onClick={commitArrow}
                  disabled={draftArrow.length < 2}
                  className="rounded-md bg-accent/20 px-2 py-1 text-accent disabled:opacity-40"
                >
                  {t("topDown.arrowDone")}
                </button>
              </div>
            ) : null}
          </div>
        </div>

        {/* 右：属性面板 */}
        <div className="w-60 shrink-0 overflow-y-auto border-l border-white/10 p-4">
          {selected ? (
            <div className="flex flex-col gap-4">
              <div className="text-[13px] font-semibold text-white/80">{t("topDown.properties")}</div>
              {selected.kind === "text" ? (
                <label className="flex flex-col gap-1 text-[12px] text-white/60">
                  {t("topDown.field.text")}
                  <textarea
                    value={selected.text}
                    onChange={(e) => patchItem(selected.id, { text: e.target.value })}
                    className="min-h-[72px] rounded-lg border border-white/10 bg-white/[0.04] p-2 text-[13px] text-text-dark outline-none focus:border-accent/50"
                  />
                </label>
              ) : selected.kind !== "arrow" ? (
                <label className="flex flex-col gap-1 text-[12px] text-white/60">
                  {t("topDown.field.label")}
                  <input
                    value={selected.label ?? ""}
                    onChange={(e) => patchItem(selected.id, { label: e.target.value })}
                    className="rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1.5 text-[13px] text-text-dark outline-none focus:border-accent/50"
                  />
                </label>
              ) : (
                <label className="flex flex-col gap-1 text-[12px] text-white/60">
                  {t("topDown.field.label")}
                  <input
                    value={selected.label ?? ""}
                    onChange={(e) => patchItem(selected.id, { label: e.target.value })}
                    className="rounded-lg border border-white/10 bg-white/[0.04] px-2 py-1.5 text-[13px] text-text-dark outline-none focus:border-accent/50"
                  />
                </label>
              )}

              {selected.kind === "camera" ? (
                <label className="flex flex-col gap-1 text-[12px] text-white/60">
                  {t("topDown.field.angle")}
                  <input
                    type="range"
                    min={0}
                    max={359}
                    value={((selected.angleDeg % 360) + 360) % 360}
                    onChange={(e) => patchItem(selected.id, { angleDeg: Number(e.target.value) })}
                  />
                </label>
              ) : null}

              {"color" in selected ? (
                <div className="flex flex-col gap-1.5 text-[12px] text-white/60">
                  {t("topDown.field.color")}
                  <div className="flex flex-wrap gap-2">
                    {TOPDOWN_PALETTE.map((c) => (
                      <button
                        key={c}
                        type="button"
                        onClick={() => patchItem(selected.id, { color: c })}
                        className={`h-6 w-6 rounded-full border-2 ${
                          selected.color === c ? "border-white" : "border-transparent"
                        }`}
                        style={{ backgroundColor: c }}
                      />
                    ))}
                  </div>
                </div>
              ) : null}

              <button
                type="button"
                onClick={deleteSelected}
                className="mt-2 flex items-center justify-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-[13px] text-red-300 hover:bg-red-500/20"
              >
                <Trash2 className="h-4 w-4" />
                {t("topDown.delete")}
              </button>
            </div>
          ) : (
            <div className="flex flex-col items-center gap-2 pt-10 text-center text-[12px] text-white/40">
              <Square className="h-6 w-6" />
              {t("topDown.emptyProperties")}
            </div>
          )}
        </div>
      </div>

      {/* 另存为草稿弹框 */}
      {saveAsOpen ? (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center bg-black/55"
          onClick={() => setSaveAsOpen(false)}
        >
          <div
            className="w-[min(92%,420px)] rounded-xl border border-white/10 bg-bg-dark p-4 shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-2 text-[14px] font-semibold text-white/90">
              {t("topDown.saveAsTitle")}
            </div>
            <input
              autoFocus
              value={draftName}
              maxLength={60}
              onChange={(e) => setDraftName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void runSaveAsDraft();
              }}
              placeholder={t("topDown.saveAsPlaceholder")}
              className="w-full rounded-lg border border-white/10 bg-white/[0.04] px-3 py-2 text-[13px] text-text-dark outline-none focus:border-accent/50"
            />
            <div className="mt-3 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setSaveAsOpen(false)}
                className="rounded-lg px-3 py-1.5 text-[13px] text-white/70 hover:bg-white/[0.08]"
              >
                {t("topDown.close")}
              </button>
              <button
                type="button"
                onClick={() => void runSaveAsDraft()}
                disabled={busy === "save" || !draftName.trim()}
                className="rounded-lg bg-accent/25 px-3 py-1.5 text-[13px] text-accent hover:bg-accent/35 disabled:opacity-50"
              >
                {busy === "save" ? t("topDown.busy") : t("topDown.saveAsConfirm")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>,
    document.body,
  );
}

function MapIcon() {
  return (
    <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent/20 text-accent">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M9 3 3 6v15l6-3 6 3 6-3V3l-6 3-6-3Z" />
        <path d="M9 3v15M15 6v15" />
      </svg>
    </span>
  );
}
