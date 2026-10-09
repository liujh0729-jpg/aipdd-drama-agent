// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const source = readFileSync(
  resolve(process.cwd(), "src/features/canvas/nodes/SkillNode.tsx"),
  "utf8",
);
const mainlineFlagsSource = readFileSync(
  resolve(process.cwd(), "src/features/canvas/domain/mainlineNodeFlags.ts"),
  "utf8",
);
const canvasCss = readFileSync(resolve(process.cwd(), "src/index.css"), "utf8");

describe("SkillNode director world entry", () => {
  it("keeps director world entry scoped to director-combined actions", () => {
    expect(source).toContain("openContextDirectorWorld");
    expect(source).toContain("director_combined");
    expect(source).not.toContain("openContextDirectorWorld('selected_background')");
    expect(source).not.toContain("PanoCaptureDialog");
    expect(source).not.toContain("handleOpenPanoCapture");
    expect(source).not.toContain("handleOpenDirectorStage");
  });

  it("lets selected-background crop director background with the same flat-source cropper as master and reverse", () => {
    expect(source).toContain("handlePickFlatSource('director_background')");
    expect(source).toContain("handlePickFlatSource('master')");
    expect(source).toContain("handlePickFlatSource('reverse')");
    expect(source).toContain("t('viewer.threeD.cropDirectorBackground')");
    expect(source).toContain("t('viewer.threeD.cropDirectorBackgroundDetail',");
    expect(source).toContain("const SELECTED_BACKGROUND_CROP_ASPECT_OPTIONS = ['2:3', '16:9'] as const");
    expect(source).toContain("aspectOptions={SELECTED_BACKGROUND_CROP_ASPECT_OPTIONS}");
    expect(source).not.toContain("handleUseDirectorEnvOnly");
    expect(source).not.toContain("t('viewer.threeD.useEnvDirectly')");
  });

  it("keeps director-combined materialization compatible with mainline context validation", () => {
    expect(source).toContain("satisfies MainlineContext");
    expect(source).toContain("projectId,");
  });

  it("clears director-world destination after capture failures", () => {
    expect(source).toMatch(/finally\s*{[\s\S]*setDirectorWorldDestination\(null\);[\s\S]*}/);
  });

  it("uses translation keys for selected-background source labels", () => {
    expect(source).toContain("t('viewer.threeD.currentBackgroundSource')");
    expect(source).toContain("t('viewer.threeD.savedEnvOnlyBackground')");
    expect(source).not.toContain(">当前背景来源<");
    expect(source).not.toContain(">下游当前背景<");
  });

  it("injects the scene pano source into contextual director-world manifests", () => {
    expect(source).toContain("directorManifestWithScenePanoSource");
    expect(source).toContain("assets.pano_360_url");
    expect(source).toContain("scene_director_pano_360");
    expect(source).toMatch(/setDirectorStageManifest\(\s*mergeManifestWithCanvasBeatContext\(\s*directorManifestWithScenePanoSource/);
  });

  it("treats only director-combined captures as committed control-frame bundles", () => {
    expect(source).toContain("autoCommitDirectorCombined");
    expect(source).toContain("onSubmitDirectorCombined");
    expect(source).toContain("director_control_bundle");
    expect(source).not.toContain("syncDirectorEnvOnlyToSelectedBackground");
    expect(source).toContain("uploadAndStageSelectedBackground(");
    expect(source).toContain("committed: true");
    expect(source).toContain("meta?.controlFrameBundle");
  });

  it("only enables director-combined auto commit for mainline-managed skill nodes", () => {
    expect(source).toContain("mainlineManaged");
    expect(source).toContain("isSystemManagedNodeData(data)");
    expect(mainlineFlagsSource).toContain("projection_key");
    expect(mainlineFlagsSource).toContain("user_spawned");
    expect(source).toContain("autoCommitDirectorCombined={mainlineManaged}");
    expect(source).not.toContain("\n          autoCommitDirectorCombined\n");
  });

  it("loads fresh env-only assets for director background crop", () => {
    expect(source).toContain("ensureSceneAssets(kind === 'director_background')");
    expect(source).not.toContain("response.ok || !response.data");
    expect(source).toContain("directorEnvOnlyPreviewUrl");
  });

  it("keeps dynamic skill input handles visible despite the global canvas handle hiding rule", () => {
    expect(source).toContain("skill-node-input-handle");
    expect(canvasCss).toContain(".react-flow__handle.skill-node-input-handle");
    expect(canvasCss).toContain("opacity: 1 !important");
    expect(canvasCss).toContain("pointer-events: auto");
  });

  it("derives contextual identity and prop handles from the unified current BeatContextNode view", () => {
    expect(source).toContain("getCurrentBeatContextFromNode");
    expect(source).toContain("beatContext?.detected_identities");
    expect(source).toContain("beatContext?.detected_props");
    expect(source).toContain("renderContextReferenceRow");
    expect(source).toContain("t('viewer.threeD.skillInputFromBeatContext')");
    expect(source).not.toContain("snapshot.detectedIdentities");
    expect(source).not.toContain("snapshot.detectedProps");
  });

  it("renders explicit no-character and no-prop BeatContext sentinels as semantic empty states", () => {
    expect(source).toContain("noCharacter");
    expect(source).toContain("noProp");
    expect(source).toContain("t('viewer.threeD.skillInputNoCharacter'");
    expect(source).toContain("t('viewer.threeD.skillInputNoProp'");
    expect(source).toContain("__NO_CHARACTER__");
    expect(source).toContain("__NO_PROP__");
  });
});
