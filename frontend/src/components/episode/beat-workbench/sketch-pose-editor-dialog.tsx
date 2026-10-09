// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import { useTranslation } from "react-i18next";
import {
  Eraser,
  Loader2,
  MousePointer2,
  Paintbrush,
  Pencil,
  RotateCcw,
  Save,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import {
  GLASS_DIALOG_CONTENT_CLASS,
  GLASS_DIALOG_HEADER_CLASS,
  GLASS_DIALOG_SIDEBAR_CLASS,
  GLASS_DIALOG_TOOLBAR_CLASS,
} from "@/lib/dialog-styles";
import { cn } from "@/lib/utils";
import {
  useSaveSketchPoseEditor,
  useSketchPoseEditor,
  type PosePoint,
  type PoseSkeleton,
  type PoseStroke,
} from "@/lib/queries/sketch-pose-editor";
import { resolveMediaUrl } from "@/lib/media-url";
import {
  addSkeletonToFrame,
  cloneJoints,
  hitTestPoseJoint,
  movePoseDrag,
  removeSkeletonFromFrame,
  resetSkeletonPoses,
  setActiveSkeleton,
  type PoseDragState,
} from "@/lib/sketch-pose-editor-model";

type EditorMode = "pose" | "pencil" | "ink" | "eraser";

interface SketchPoseEditorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: string;
  episode: number;
  beatNum: number;
}

export function SketchPoseEditorDialog({
  open,
  onOpenChange,
  project,
  episode,
  beatNum,
}: SketchPoseEditorDialogProps) {
  const { t } = useTranslation();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imageRef = useRef<HTMLImageElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [displaySize, setDisplaySize] = useState<{ w: number; h: number } | null>(
    null,
  );
  const poseQuery = useSketchPoseEditor(project, episode, beatNum, open);
  const savePose = useSaveSketchPoseEditor(project, episode);
  const data = poseQuery.data?.ok ? poseQuery.data.data : null;
  const [skeletons, setSkeletons] = useState<PoseSkeleton[]>([]);
  const [initialSkeletons, setInitialSkeletons] = useState<PoseSkeleton[]>([]);
  const [strokes, setStrokes] = useState<PoseStroke[]>([]);
  const [activeIdentity, setActiveIdentity] = useState("");
  const [mode, setMode] = useState<EditorMode>("pose");
  const [penWidth, setPenWidth] = useState(4);
  const [presetKey, setPresetKey] = useState("");
  const [drawingStroke, setDrawingStroke] = useState<PoseStroke | null>(null);
  const [poseDrag, setPoseDrag] = useState<PoseDragState | null>(null);

  useEffect(() => {
    if (!data || !open) return;
    const loaded = data.skeletons.map((skeleton) => ({
      ...skeleton,
      joints: cloneJoints(skeleton.joints),
    }));
    setSkeletons(loaded);
    setInitialSkeletons(loaded);
    setStrokes([]);
    setActiveIdentity(data.skeletons[0]?.identityId ?? "");
    setPresetKey(Object.keys(data.pose_presets)[0] ?? "");
    setMode("pose");
    setPoseDrag(null);
  }, [data, open]);

  const activeSkeleton = useMemo(
    () => skeletons.find((item) => item.identityId === activeIdentity),
    [activeIdentity, skeletons],
  );

  // Scale the canvas to fill the available stage (keeping aspect ratio so the
  // rendered box equals its bounding rect — pointer mapping stays accurate).
  useEffect(() => {
    if (!data || !open) return;
    const stage = stageRef.current;
    if (!stage) return;
    const compute = () => {
      const cw = stage.clientWidth;
      const ch = stage.clientHeight;
      if (cw <= 0 || ch <= 0) return;
      const scale = Math.min(cw / data.width, ch / data.height);
      setDisplaySize({
        w: Math.round(data.width * scale),
        h: Math.round(data.height * scale),
      });
    };
    compute();
    const observer = new ResizeObserver(compute);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [data, open]);

  useEffect(() => {
    if (!data || !open) return;
    const imageUrl = resolveMediaUrl(data.sketch_url);
    if (!imageUrl) return;
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.onload = () => {
      imageRef.current = image;
      drawEditorCanvas(
        canvasRef.current,
        image,
        data.skeleton_edges,
        skeletons,
        drawingStroke ? [...strokes, drawingStroke] : strokes,
      );
    };
    image.src = imageUrl;
  }, [data, drawingStroke, open, skeletons, strokes]);

  const appendPoint = (event: PointerEvent<HTMLCanvasElement>) => {
    if (!drawingStroke || !canvasRef.current) return;
    const point = canvasPoint(event, canvasRef.current);
    setDrawingStroke({
      ...drawingStroke,
      points: [...drawingStroke.points, point],
    });
  };

  const startStroke = (event: PointerEvent<HTMLCanvasElement>) => {
    if (!canvasRef.current) return;
    const point = canvasPoint(event, canvasRef.current);
    if (mode === "pose") {
      const hit = hitTestPoseJoint(skeletons, point, 18);
      if (!hit) {
        setSkeletons((items) => items.map((item) => ({ ...item, active: false })));
        return;
      }
      const skeleton = skeletons[hit.skeletonIndex];
      if (!skeleton) return;
      setActiveIdentity(skeleton.identityId);
      setSkeletons((items) => setActiveSkeleton(items, skeleton.identityId));
      setPoseDrag({
        ...hit,
        bodyDrag: hit.jointKey === "neck" || hit.jointKey === "nose",
        startPoint: point,
        startJoints: cloneJoints(skeleton.joints),
      });
      event.currentTarget.setPointerCapture(event.pointerId);
      return;
    }
    const colorHex =
      mode === "eraser"
        ? "#ffffff"
        : mode === "ink"
          ? "#333333"
          : activeSkeleton?.colorHex || "#22d3ee";
    setDrawingStroke({
      points: [point],
      width: penWidth,
      colorHex,
      eraser: mode === "eraser",
    });
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const finishStroke = () => {
    if (poseDrag) {
      setPoseDrag(null);
      return;
    }
    if (!drawingStroke) return;
    if (drawingStroke.points.length > 1) {
      setStrokes((items) => [...items, drawingStroke]);
    }
    setDrawingStroke(null);
  };

  const dragPose = (event: PointerEvent<HTMLCanvasElement>) => {
    if (!poseDrag || !canvasRef.current || !data) return;
    const point = canvasPoint(event, canvasRef.current);
    setSkeletons((items) =>
      movePoseDrag(items, poseDrag, point, data.width, data.height),
    );
  };

  const applyPreset = () => {
    if (!data || !presetKey || !activeIdentity) return;
    const preset = data.pose_presets[presetKey];
    if (!preset) return;
    setSkeletons((items) =>
      items.map((item) =>
        item.identityId === activeIdentity
          ? {
              ...item,
              visible: true,
              joints: scalePresetJoints(preset.joints, data.width, data.height),
            }
          : item,
      ),
    );
  };

  const handleSave = async () => {
    try {
      const res = await savePose.mutateAsync({
        beatNum,
        state: { skeletons, strokes },
      });
      if (!res.ok) {
        toast.error(res.error || t("common.error"));
        return;
      }
      toast.success(t("episode.workbench.sketch.poseSaved"));
      onOpenChange(false);
    } catch {
      toast.error(t("common.error"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className={cn(
          GLASS_DIALOG_CONTENT_CLASS,
          "h-[min(calc(100vh-2rem),820px)] w-[min(calc(100vw-2rem),1180px)] max-w-none overflow-hidden p-0 sm:max-w-none",
        )}
      >
        <DialogHeader className={cn(GLASS_DIALOG_HEADER_CLASS, "px-4 py-3")}>
          <DialogTitle>
            {t("episode.workbench.sketch.poseTitle", { n: beatNum })}
          </DialogTitle>
        </DialogHeader>
        {!data ? (
          <div className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground">
            <Loader2 className="mr-2 size-4 animate-spin" />
            {t("common.loading", "Loading")}
          </div>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-[280px_1fr] overflow-hidden">
            <aside className={cn(GLASS_DIALOG_SIDEBAR_CLASS, "min-h-0 overflow-y-auto p-3")}>
              <div className="space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  {t("episode.workbench.sketch.poseCharacters")}
                </div>
                {skeletons.map((skeleton) => (
                  <div
                    key={skeleton.identityId}
                    role="button"
                    tabIndex={0}
                    onClick={() => {
                      setActiveIdentity(skeleton.identityId);
                      setSkeletons((items) =>
                        setActiveSkeleton(items, skeleton.identityId),
                      );
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      setActiveIdentity(skeleton.identityId);
                      setSkeletons((items) =>
                        setActiveSkeleton(items, skeleton.identityId),
                      );
                    }}
                    className={`flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left text-xs ${
                      activeIdentity === skeleton.identityId
                        ? "border-primary bg-primary/10"
                        : "border-border"
                    }`}
                  >
                    <Checkbox
                      checked={skeleton.visible === true}
                      onCheckedChange={(checked) =>
                        setSkeletons((items) =>
                          items.map((item) =>
                            item.identityId === skeleton.identityId
                              ? {
                                  ...item,
                                  visible: checked === true,
                                  active: checked === true,
                                }
                              : item,
                          ),
                        )
                      }
                      onClick={(event) => event.stopPropagation()}
                    />
                    <span
                      aria-hidden
                      className="size-2 rounded-full"
                      style={{ backgroundColor: skeleton.colorHex }}
                    />
                    <span className="min-w-0 truncate">{skeleton.identityId}</span>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="ml-auto h-6 px-2 text-[11px]"
                      onClick={(event) => {
                        event.stopPropagation();
                        if (skeleton.visible) {
                          setSkeletons((items) =>
                            removeSkeletonFromFrame(items, skeleton.identityId),
                          );
                        } else {
                          setActiveIdentity(skeleton.identityId);
                          setSkeletons((items) =>
                            addSkeletonToFrame(items, skeleton.identityId),
                          );
                        }
                      }}
                    >
                      {skeleton.visible
                        ? t("episode.workbench.sketch.poseRemoveFromFrame")
                        : t("episode.workbench.sketch.poseAddToFrame")}
                    </Button>
                  </div>
                ))}
              </div>

              <div className="mt-4 space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  {t("episode.workbench.sketch.posePreset")}
                </div>
                <select
                  value={presetKey}
                  onChange={(event) => setPresetKey(event.currentTarget.value)}
                  className="h-8 w-full rounded-md border border-border bg-background px-2 text-xs"
                >
                  {Object.entries(data.pose_presets).map(([key, preset]) => (
                    <option key={key} value={key}>
                      {preset.label}
                    </option>
                  ))}
                </select>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={applyPreset}
                  disabled={!activeIdentity || !presetKey}
                  className="w-full"
                >
                  {t("episode.workbench.sketch.poseApplyPreset")}
                </Button>
              </div>
            </aside>

            <div className="flex min-h-0 flex-col overflow-hidden">
              <div className={cn(GLASS_DIALOG_TOOLBAR_CLASS, "flex shrink-0 flex-wrap items-center gap-1.5 px-3 py-2.5")}>
                <Button
                  size="sm"
                  variant={mode === "pose" ? "default" : "outline"}
                  onClick={() => setMode("pose")}
                  className="gap-1"
                >
                  <MousePointer2 className="size-3.5" />
                  {t("episode.workbench.sketch.poseSelect")}
                </Button>
                <Button
                  size="sm"
                  variant={mode === "pencil" ? "default" : "outline"}
                  onClick={() => setMode("pencil")}
                  className="gap-1"
                >
                  <Pencil className="size-3.5" />
                  {t("episode.workbench.sketch.poseColorPen")}
                </Button>
                <Button
                  size="sm"
                  variant={mode === "ink" ? "default" : "outline"}
                  onClick={() => setMode("ink")}
                  className="gap-1"
                >
                  <Paintbrush className="size-3.5" />
                  {t("episode.workbench.sketch.poseInk")}
                </Button>
                <Button
                  size="sm"
                  variant={mode === "eraser" ? "default" : "outline"}
                  onClick={() => setMode("eraser")}
                  className="gap-1"
                >
                  <Eraser className="size-3.5" />
                  {t("episode.workbench.sketch.poseEraser")}
                </Button>
                <label className="ml-2 text-xs text-muted-foreground">
                  {t("episode.workbench.sketch.poseWidth")}
                </label>
                <input
                  type="range"
                  min={2}
                  max={16}
                  value={penWidth}
                  onChange={(event) => setPenWidth(Number(event.currentTarget.value))}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    if (strokes.length > 0) {
                      setStrokes((items) => items.slice(0, -1));
                    } else {
                      setSkeletons((items) =>
                        resetSkeletonPoses(items, initialSkeletons),
                      );
                    }
                  }}
                  className="gap-1"
                >
                  <RotateCcw className="size-3.5" />
                  {t("episode.workbench.sketch.poseUndo")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setSkeletons((items) =>
                      resetSkeletonPoses(items, initialSkeletons),
                    )
                  }
                >
                  {t("episode.workbench.sketch.poseReset")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => setStrokes([])}
                >
                  {t("episode.workbench.sketch.poseClear")}
                </Button>
                <Button
                  size="sm"
                  variant="default"
                  onClick={handleSave}
                  disabled={savePose.isPending}
                  className="ml-auto gap-1"
                >
                  {savePose.isPending ? (
                    <Loader2 className="size-3.5 animate-spin" />
                  ) : (
                    <Save className="size-3.5" />
                  )}
                  {t("common.save", "Save")}
                </Button>
              </div>
              <div className="min-h-0 flex-1 overflow-hidden bg-black/10 p-4">
                <div
                  ref={stageRef}
                  className="flex h-full w-full items-center justify-center rounded-lg bg-black/20 ring-1 ring-white/[0.1]"
                  style={{
                    backgroundImage:
                      "radial-gradient(circle, rgba(148,163,184,0.12) 1px, transparent 1px)",
                    backgroundSize: "18px 18px",
                  }}
                >
                <canvas
                  ref={canvasRef}
                  width={data.width}
                  height={data.height}
                  className="max-h-full max-w-full rounded-md border border-border bg-background shadow-xl shadow-black/30"
                  style={{
                    width: displaySize?.w,
                    height: displaySize?.h,
                    cursor:
                      mode === "pose"
                        ? "grab"
                        : mode === "eraser"
                          ? "cell"
                          : "crosshair",
                  }}
                  onPointerDown={startStroke}
                  onPointerMove={(event) => {
                    if (mode === "pose") {
                      dragPose(event);
                    } else {
                      appendPoint(event);
                    }
                  }}
                  onPointerUp={finishStroke}
                  onPointerCancel={finishStroke}
                />
                </div>
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function canvasPoint(
  event: PointerEvent<HTMLCanvasElement>,
  canvas: HTMLCanvasElement,
): PosePoint {
  const rect = canvas.getBoundingClientRect();
  return {
    x: ((event.clientX - rect.left) / rect.width) * canvas.width,
    y: ((event.clientY - rect.top) / rect.height) * canvas.height,
  };
}

function scalePresetJoints(
  joints: Record<string, { x: number; y: number }>,
  width: number,
  height: number,
): Record<string, PosePoint> {
  return Object.fromEntries(
    Object.entries(joints).map(([key, point]) => [
      key,
      {
        x: point.x <= 1 ? point.x * width : point.x,
        y: point.y <= 1 ? point.y * height : point.y,
      },
    ]),
  );
}

function drawEditorCanvas(
  canvas: HTMLCanvasElement | null,
  image: HTMLImageElement,
  edges: Array<[string, string]>,
  skeletons: PoseSkeleton[],
  strokes: PoseStroke[],
) {
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

  for (const skeleton of skeletons) {
    if (!skeleton.visible) continue;
    const color = skeleton.colorHex || "#22d3ee";

    ctx.save();
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    // Soft glow makes the active skeleton pop without harsh outlines.
    if (skeleton.active) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 10;
    }
    ctx.strokeStyle = color;
    ctx.lineWidth = skeleton.lineWidth ?? 4;
    for (const [aKey, bKey] of edges) {
      const a = skeleton.joints[aKey];
      const b = skeleton.joints[bKey];
      if (!a || !b) continue;
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
      ctx.stroke();
    }
    const nose = skeleton.joints.nose;
    if (nose) {
      ctx.beginPath();
      ctx.arc(nose.x, nose.y, skeleton.headRadius ?? 10, 0, Math.PI * 2);
      ctx.fillStyle = withAlpha(color, 0.15);
      ctx.fill();
      ctx.stroke();
    }
    ctx.restore();

    // Joints: filled dot with a crisp white halo for a cleaner read.
    for (const joint of Object.values(skeleton.joints)) {
      const radius = skeleton.active ? 5.5 : 4;
      ctx.beginPath();
      ctx.arc(joint.x, joint.y, radius + 1.5, 0, Math.PI * 2);
      ctx.fillStyle = "#ffffff";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(joint.x, joint.y, radius, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
    }
  }

  for (const stroke of strokes) {
    drawStroke(ctx, stroke);
  }
}

function withAlpha(hex: string, alpha: number): string {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) return hex;
  const value = parseInt(match[1], 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function drawStroke(ctx: CanvasRenderingContext2D, stroke: PoseStroke) {
  if (stroke.points.length < 2) return;
  ctx.strokeStyle = stroke.eraser ? "#ffffff" : stroke.colorHex || "#333333";
  ctx.lineWidth = stroke.width ?? 4;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(stroke.points[0].x, stroke.points[0].y);
  for (const point of stroke.points.slice(1)) {
    ctx.lineTo(point.x, point.y);
  }
  ctx.stroke();
}
