// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown, FileText, Image as ImageIcon, Mic2, Pencil, Video, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

import { SaveStatus } from "@/components/save-status";
import { useEscapeToClose } from "@/hooks/use-escape-to-close";
import { useGridsByBeat } from "@/lib/queries/sketches";
import { useVideoBackends } from "@/lib/queries/video";
import { resolveImage } from "@/lib/resolve-image";
import { saveScopes, useSaveState } from "@/stores/save-status-store";
import { cn } from "@/lib/utils";
import type { Beat } from "@/types/episode";
import type { BeatStageState } from "@/types/beat-state";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  WORKBENCH_SELECT_CONTENT_CLASS,
  WORKBENCH_SELECT_ITEM_CLASS,
} from "./toolbar-select-styles";

import { TextPane } from "./text-pane";
import { SketchSection } from "./sketch-section";
import { RenderSection } from "./render-section";
import { AudioPane } from "./audio-pane";
import { VideoPane } from "./video-pane";

export type SectionId = "text" | "sketch" | "render" | "audio" | "video";

interface SingleBeatPanelProps {
  beat: Beat;
  project: string;
  episode: number;
  stages: Record<string, BeatStageState> | undefined;
  defaultBackend: string;
  onDefaultBackendChange: (backend: string) => void;
  spineTemplate?: "drama" | "narrated";
  isSeedance2Backend?: boolean;
  showAudioMediaStatus?: boolean;
  /** Accordion open/close state — owned by parent so it persists across beat changes. */
  openSections: Set<SectionId>;
  onToggleSection: (id: SectionId) => void;
}

function isRenderImageMatch(img: { type?: string; id?: string; cell_path?: string | null; grid_path?: string | null; original_beat?: number | null }, assignment: string) {
  return (
    img.type === "render" &&
    (img.id === assignment ||
      img.cell_path === assignment ||
      img.grid_path === assignment)
  );
}

function sectionStatusKey(
  id: SectionId,
  beat: Beat,
  stages: Record<string, BeatStageState> | undefined,
  hasSketch: boolean,
  hasRender: boolean,
): string {
  switch (id) {
    case "text": return beat.narration_segment ? "episode.beat.edited" : "episode.beat.notEdited";
    case "sketch": return hasSketch || stages?.sketch === "ready" ? "episode.beat.selected" : "episode.beat.notSelected";
    case "render": return hasRender ? "episode.beat.rendered" : "episode.beat.notRendered";
    case "audio": return beat.audio_url ? "episode.beat.generated" : "episode.beat.notGenerated";
    case "video": return beat.video_url ? "episode.beat.generated" : "episode.beat.notGenerated";
  }
}

function isReadyStatus(statusKey: string) {
  return (
    statusKey === "episode.beat.edited" ||
    statusKey === "episode.beat.selected" ||
    statusKey === "episode.beat.rendered" ||
    statusKey === "episode.beat.generated"
  );
}

const SECTIONS: { id: SectionId; labelKey: string; icon: React.ElementType }[] = [
  { id: "text", labelKey: "episode.beat.sectionText", icon: FileText },
  { id: "sketch", labelKey: "episode.beat.sectionSketch", icon: Pencil },
  { id: "render", labelKey: "episode.beat.sectionRender", icon: ImageIcon },
  { id: "audio", labelKey: "episode.beat.sectionAudio", icon: Mic2 },
  { id: "video", labelKey: "episode.beat.sectionVideo", icon: Video },
];

const SECTION_MOTION_EASE = [0.22, 1, 0.36, 1] as const;

export function SingleBeatPanel({
  beat,
  project,
  episode,
  stages,
  defaultBackend,
  onDefaultBackendChange,
  spineTemplate = "drama",
  showAudioMediaStatus = true,
  openSections,
  onToggleSection,
}: SingleBeatPanelProps) {
  const { t } = useTranslation();
  const { byBeat, assignments } = useGridsByBeat(project, episode);
  const images = byBeat.get(beat.beat_number) ?? [];
  const resolvedSketch = resolveImage(images, assignments, beat.beat_number, "sketch", beat.sketch_url ?? null);
  const renderAssignment = assignments[String(beat.beat_number)] ?? null;
  const hasRender =
    !!beat.frame_url ||
    (renderAssignment !== null && images.some((image) => isRenderImageMatch(image, renderAssignment))) ||
    images.some((image) => image.type === "render" && image.original_beat === beat.beat_number && !!image.cell_url);
  const hasSketch = !!resolvedSketch.url;

  // Image preview popup
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  useEscapeToClose(previewUrl !== null, () => setPreviewUrl(null));

  const beatTextScope = saveScopes.beatText(project, episode, beat.beat_number);
  const textSaveState = useSaveState(beatTextScope);

  // 精品剧 (spine_template === "drama") bakes narration into the rendered video
  // and has no standalone audio stage — hide the 音频 section for it.
  const sections =
    spineTemplate === "drama"
      ? SECTIONS.filter((section) => section.id !== "audio")
      : SECTIONS;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {sections.map(({ id, labelKey, icon: Icon }) => {
          const isOpen = openSections.has(id);
          const statusKey = sectionStatusKey(id, beat, stages, hasSketch, hasRender);
          const ready = isReadyStatus(statusKey);
          return (
            <div key={id}>
              <div
                className={cn(
                  "sticky top-0 z-20 flex min-h-11 items-center border-b border-white/[0.055] bg-[#111111] text-sm font-semibold text-muted-foreground shadow-[0_1px_0_rgba(255,255,255,0.03)] transition-colors hover:bg-white/[0.035] hover:text-foreground",
                  isOpen && "bg-[#121212] text-foreground/90",
                )}
              >
                <button
                  type="button"
                  onClick={() => onToggleSection(id)}
                  className="flex min-w-0 flex-1 items-center gap-2.5 px-3 py-2.5 text-left"
                >
                  <ChevronDown
                    className={cn(
                      "size-3.5 text-muted-foreground/55 transition-transform",
                      !isOpen && "-rotate-90",
                      isOpen && "text-muted-foreground/75",
                    )}
                  />
                  <Icon className={cn("size-4", isOpen ? "text-primary/85" : "text-muted-foreground/85")} />
                  <span className={cn("font-semibold tracking-tight", isOpen ? "text-foreground" : "text-foreground/90")}>{t(labelKey)}</span>
                </button>
                {id === "video" && (
                  <VideoBackendHeaderSelect
                    project={project}
                    value={defaultBackend}
                    onChange={onDefaultBackendChange}
                  />
                )}
                <span
                  className={cn(
                    "mr-3 inline-flex h-5 shrink-0 items-center gap-1.5 rounded-full border px-2 text-[10px] font-normal",
                    ready
                      ? "border-primary/18 bg-primary/[0.09] text-primary/90"
                      : "border-white/[0.055] bg-white/[0.045] text-muted-foreground/76",
                  )}
                >
                  {id === "text" && <SaveStatus scope={beatTextScope} variant="inline" />}
                  {id === "text" &&
                    textSaveState.status !== "saving" &&
                    textSaveState.status !== "error" &&
                    statusKey === "episode.beat.edited" && (
                      <Check className="size-2.5 text-primary" />
                    )}
                  <span
                    aria-hidden
                    className={cn(
                      "size-1.5 rounded-full",
                      ready ? "bg-primary" : "bg-muted-foreground/30",
                    )}
                  />
                  {t(statusKey)}
                </span>
              </div>
              <AnimatedSectionContent open={isOpen}>
                <div className="border-b border-white/[0.04] bg-white/[0.012] px-3 py-3">
                  {id === "text" && (
                    <TextPane
                      beat={beat}
                      project={project}
                      episode={episode}
                      spineTemplate={spineTemplate}
                    />
                  )}
                  {id === "sketch" && (
                    <SketchSection
                      beat={beat}
                      project={project}
                      episode={episode}
                      images={images}
                      assignments={assignments}
                      onPreview={setPreviewUrl}
                    />
                  )}
                  {id === "render" && (
                    <RenderSection
                      beat={beat}
                      project={project}
                      episode={episode}
                      images={images}
                      assignments={assignments}
                      onPreview={setPreviewUrl}
                    />
                  )}
                  {id === "audio" && (
                    <AudioPane
                      beat={beat}
                      project={project}
                      episode={episode}
                      state={stages?.audio ?? "missing"}
                      spineTemplate={spineTemplate}
                    />
                  )}
                  {id === "video" && (
                    <VideoPane
                      beat={beat}
                      project={project}
                      episode={episode}
                      state={stages?.video ?? "missing"}
                      defaultBackend={defaultBackend}
                      showAudioMediaStatus={showAudioMediaStatus}
                    />
                  )}
                </div>
              </AnimatedSectionContent>
            </div>
          );
        })}
      </div>

      {/* Image preview overlay */}
      {previewUrl && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-8"
          onClick={() => setPreviewUrl(null)}
        >
          <button
            type="button"
            onClick={() => setPreviewUrl(null)}
            className="absolute right-4 top-4 rounded-full bg-background/80 p-2 text-foreground hover:bg-background"
          >
            <X className="size-5" />
          </button>
          <img
            src={previewUrl}
            alt="Preview"
            className="max-h-full max-w-full object-contain"
            decoding="async"
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}
    </div>
  );
}

function VideoBackendHeaderSelect({
  project,
  value,
  onChange,
}: {
  project: string;
  value: string;
  onChange: (backend: string) => void;
}) {
  const { t } = useTranslation();
  const { data: videoBackendsRes } = useVideoBackends(project);
  const videoBackends = videoBackendsRes?.data ?? [];
  const selectedBackend = videoBackends.find((backend) => backend.value === value);

  return (
    <div
      className="mr-4 hidden shrink-0 items-center md:flex"
      onClick={(event) => event.stopPropagation()}
    >
      <Select value={value} onValueChange={(next) => onChange(next ?? "")}>
        <SelectTrigger
          aria-label={t("episode.workbench.batch.videoModel")}
          className="!h-[26px] w-auto min-w-[150px] rounded-[7px] border-white/[0.12] bg-white/[0.018] px-2.5 text-xs font-normal text-foreground/80 shadow-none hover:border-white/[0.20] hover:bg-white/[0.035] hover:text-foreground focus-visible:border-white/[0.22] focus-visible:bg-white/[0.035] focus-visible:ring-white/10 dark:border-white/[0.12] dark:bg-white/[0.018] dark:hover:bg-white/[0.035] [&>svg]:ml-1.5 [&>svg]:size-3.5"
        >
          <SelectValue>
            {() => selectedBackend?.label ?? value}
          </SelectValue>
        </SelectTrigger>
        <SelectContent
          align="start"
          sideOffset={8}
          alignItemWithTrigger={false}
          className={WORKBENCH_SELECT_CONTENT_CLASS}
        >
          {videoBackends.map((backend) => (
            <SelectItem
              key={backend.value}
              value={backend.value}
              className={WORKBENCH_SELECT_ITEM_CLASS}
            >
              <span className="flex items-center gap-2">
                {backend.label}
                {backend.is_default && (
                  <span className="text-[10px] text-muted-foreground">
                    {t("episode.workbench.video.noteDefault")}
                  </span>
                )}
                {backend.is_seedance2 && (
                  <span className="text-[10px] text-muted-foreground">
                    Seedance2
                  </span>
                )}
                {backend.dialogue_only && (
                  <span className="text-[10px] text-muted-foreground">
                    {t("episode.workbench.video.noteDialogue")}
                  </span>
                )}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

function AnimatedSectionContent({
  open,
  children,
}: {
  open: boolean;
  children: React.ReactNode;
}) {
  const reducedMotion = useReducedMotion();

  return (
    <AnimatePresence initial={false}>
      {open ? (
        <motion.div
          key="section-content"
          initial={reducedMotion ? false : { height: 0, opacity: 0, y: -4 }}
          animate={{ height: "auto", opacity: 1, y: 0 }}
          exit={reducedMotion ? { opacity: 0 } : { height: 0, opacity: 0, y: -2 }}
          transition={
            reducedMotion
              ? { duration: 0 }
              : {
                  height: { duration: 0.4, ease: SECTION_MOTION_EASE },
                  opacity: { duration: 0.4, ease: SECTION_MOTION_EASE },
                  y: { duration: 0.4, ease: SECTION_MOTION_EASE },
                }
          }
          style={{ overflow: "hidden" }}
        >
          {children}
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
