// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useCallback, useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { FileText } from "lucide-react";
import { EpisodeEmptyState } from "@/components/episode/episode-empty-state";
import type { SelectionState } from "@/hooks/use-selection";
import type { Beat } from "@/types/episode";
import type { BeatStates } from "@/types/beat-state";
import {
  DEFAULT_ACTION_PANEL_SECTIONS,
  episodeWorkbenchScopeKey,
  useEpisodeWorkbenchStore,
} from "@/stores/episode-workbench-store";
import { SingleBeatPanel, type SectionId } from "./single-beat-panel";

interface ActionPanelProps {
  selection: SelectionState;
  beats: Beat[];
  states: BeatStates;
  project: string;
  episode: number;
  defaultBackend: string;
  onDefaultBackendChange: (backend: string) => void;
  spineTemplate?: "drama" | "narrated";
  isSeedance2Backend?: boolean;
  showAudioMediaStatus?: boolean;
  targetSection?: SectionId | null;
}

export function ActionPanel({
  selection,
  beats,
  states,
  project,
  episode,
  defaultBackend,
  onDefaultBackendChange,
  spineTemplate = "drama",
  isSeedance2Backend = false,
  showAudioMediaStatus = true,
  targetSection,
}: ActionPanelProps) {
  const scope = useMemo(() => ({ project, episode }), [episode, project]);
  const scopeKey = episodeWorkbenchScopeKey(scope);
  const persistedOpenSections = useEpisodeWorkbenchStore(
    useCallback(
      (s) =>
        s.actionPanelSectionsByScope[scopeKey] ??
        DEFAULT_ACTION_PANEL_SECTIONS,
      [scopeKey],
    ),
  );
  const setActionPanelSections = useEpisodeWorkbenchStore(
    (s) => s.setActionPanelSections,
  );
  const openSections = useMemo(
    () => new Set<SectionId>(persistedOpenSections),
    [persistedOpenSections],
  );

  useEffect(() => {
    if (!targetSection) return;
    if (openSections.has(targetSection)) return;
    const next = new Set(openSections);
    next.add(targetSection);
    setActionPanelSections(scope, next);
  }, [openSections, scope, setActionPanelSections, targetSection]);

  const toggleSection = useCallback((id: SectionId) => {
    const next = new Set(openSections);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setActionPanelSections(scope, next);
  }, [openSections, scope, setActionPanelSections]);

  const beatNum = selection.mode === "single" ? selection.beatNum : null;

  if (beatNum !== null) {
    const beatIndex = beats.findIndex((b) => b.beat_number === beatNum);
    const beat = beatIndex >= 0 ? beats[beatIndex] : undefined;
    if (!beat) return <EmptyPrompt />;
    return (
      <SingleBeatPanel
        beat={beat}
        project={project}
        episode={episode}
        stages={states[beat.beat_number]}
        defaultBackend={defaultBackend}
        onDefaultBackendChange={onDefaultBackendChange}
        spineTemplate={spineTemplate}
        isSeedance2Backend={isSeedance2Backend}
        showAudioMediaStatus={showAudioMediaStatus}
        openSections={openSections}
        onToggleSection={toggleSection}
      />
    );
  }

  return <EmptyPrompt />;
}

function EmptyPrompt() {
  const { t } = useTranslation();
  return (
    <EpisodeEmptyState
      icon={FileText}
      description={t("episode.beat.clickToView")}
    />
  );
}
