// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { FreezoneBeatContextBeat } from "@/api/projects";
import type { BeatContextNodeData } from "@/features/canvas/domain/canvasNodes";
import type { MainlineContext } from "./mainlineContext";

export function buildBeatContextNodeRefreshPatch(
  projectId: string,
  beat: FreezoneBeatContextBeat,
  currentData?: Pick<BeatContextNodeData, "snapshot" | "beat_edit_fields">,
): Partial<BeatContextNodeData> {
  const visualDescription = beat.visual_description ?? "";
  const narrationSegment = beat.narration_segment ?? "";
  const sceneId = beat.scene_id ?? "";
  const sceneVariantId = beat.scene_variant_id ?? "";
  const hasTimeOfDay = Object.prototype.hasOwnProperty.call(beat, "time_of_day");
  const localTimeOfDay =
    typeof currentData?.snapshot?.timeOfDay === "string"
      ? currentData.snapshot.timeOfDay
      : typeof currentData?.beat_edit_fields?.time_of_day === "string"
        ? currentData.beat_edit_fields.time_of_day
        : "";
  const timeOfDay = hasTimeOfDay ? beat.time_of_day ?? "" : localTimeOfDay;
  const detectedIdentities = beat.detected_identities ?? [];
  const detectedProps = beat.detected_props ?? [];
  const sketchColors = beat.sketch_colors ?? {};
  const propMarkerColors = beat.prop_marker_colors ?? {};
  const selectedBackgroundExists = beat.assets.some((asset) =>
    ["selected_background", "background_candidate"].includes(
      String(asset.role || asset.kind || ""),
    ),
  );
  const currentSketchExists = beat.assets.some((asset) =>
    ["current_sketch", "sketch_candidate"].includes(String(asset.role || asset.kind || "")),
  );
  const currentFrameExists = beat.assets.some((asset) =>
    ["current_frame", "frame_candidate"].includes(String(asset.role || asset.kind || "")),
  );
  const mainlineContext: MainlineContext = {
    kind: "beat",
    projectId,
    episode: beat.episode,
    beat: beat.beat,
    role: "beat_context",
    label: beat.label || `EP${beat.episode} Beat ${beat.beat} context`,
    visualDescription,
    narrationSegment,
    sceneId,
    sceneVariantId,
    timeOfDay,
    detectedIdentities,
    detectedProps,
    sketchColors,
    propMarkerColors,
  };

  return {
    projectId,
    episode: beat.episode,
    beat: beat.beat,
    content: visualDescription,
    snapshot: {
      visualDescription,
      narrationSegment,
      sceneId,
      sceneVariantId,
      timeOfDay,
      detectedIdentities,
      detectedProps,
      sketchColors,
      propMarkerColors,
      selectedBackgroundExists,
      currentSketchExists,
      currentFrameExists,
    },
    snapshotLoadedAt: new Date().toISOString(),
    syncStatus: "fresh",
    errorMessage: "",
    mainline_context: [mainlineContext],
    beat_edit_fields: {
      visual_description: visualDescription,
      scene_id: sceneId,
      scene_variant_id: sceneVariantId,
      time_of_day: timeOfDay,
      detected_identities: detectedIdentities,
      detected_props: detectedProps,
    },
  };
}
