// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const routeSource = readFileSync(
  "src/routes/_app/projects.$project/episodes.tsx",
  "utf-8",
);

describe("episodes workbench integration", () => {
  it("wires NiceGUI-style stats and manual refresh into the episode list", () => {
    expect(routeSource).toContain("deriveEpisodeStats");
    expect(routeSource).toContain("EpisodeStatsStrip");
    expect(routeSource).toContain("handleRefresh");
    expect(routeSource).toContain("episode.list.stats.totalEpisodes");
    expect(routeSource).toContain("episode.list.refresh");
  });

  it("wires list-card identity, scene, and prop planning shortcuts", () => {
    expect(routeSource).toContain("usePlanIdentities");
    expect(routeSource).toContain("usePlanEpisodeScenes");
    expect(routeSource).toContain("usePlanEpisodeProps");
    expect(routeSource).toContain('taskType: "identity_planner"');
    expect(routeSource).toContain("onClick={handlePlanScenes}");
    expect(routeSource).toContain("onClick={handlePlanProps}");
    expect(routeSource).toContain("episode.list.planIdentities");
    expect(routeSource).toContain("episode.list.planScenes");
    expect(routeSource).toContain("episode.list.planProps");
  });

  it("blocks list-card identity planning until character extraction is ready", () => {
    expect(routeSource).toContain("useTaskActivity(TASK_TYPES.BUILD_CHARACTERS");
    expect(routeSource).toContain("identityCharactersRequired");
    expect(routeSource).toContain("identityCharactersBuilding");
    expect(routeSource).toContain("identityDisabledReason={identityDisabledReason}");
  });

  it("blocks list-card scene planning while scene extraction is running", () => {
    expect(routeSource).toContain("useTaskActivity(TASK_TYPES.BUILD_SCENES");
    expect(routeSource).toContain("sceneCatalogBuilding");
    expect(routeSource).toContain("sceneDisabledReason={sceneDisabledReason}");
  });

  // [aipdd fork delta] WP-D: 积分估价与 CreditCostInline 展示已拆除，
  // 这里只保留规划动作的后端错误透传契约。
  it("surfaces backend errors on list-card scene and prop planning actions", () => {
    expect(routeSource).toMatch(
      /const handlePlanScenes[\s\S]*toast\.error\(backendErrorToastMessage\(res\.error, t\)\)[\s\S]*catch \(err\)[\s\S]*toast\.error\(backendErrorToastMessage\(err, t\)\)/,
    );
    expect(routeSource).toMatch(
      /const handlePlanProps[\s\S]*toast\.error\(backendErrorToastMessage\(res\.error, t\)\)[\s\S]*catch \(err\)[\s\S]*toast\.error\(backendErrorToastMessage\(err, t\)\)/,
    );
  });

  // 每张卡片自己持有规划 mutation + 任务控制器，转圈天然只作用于被点的那一集；
  // 场景/道具在 EE 下是异步任务，转圈必须跟着任务流走到任务结束。
  it("keeps list-card planning spinners running until the async task finishes", () => {
    expect(routeSource).toContain("planIdentities.isPending || identityTask.started");
    expect(routeSource).toContain('taskType: "identity_planner"');
    expect(routeSource).toContain("planScenes.isPending || sceneTask.started");
    expect(routeSource).toContain("planProps.isPending || propTask.started");
    expect(routeSource).toContain("TASK_TYPES.EPISODE_SCENE_PLANNER");
    expect(routeSource).toContain("TASK_TYPES.EPISODE_PROP_PLANNER");
    expect(routeSource).toContain("sceneTask.start({ scope: res.scope })");
    expect(routeSource).toContain("propTask.start({ scope: res.scope })");
  });

  it("shows only one episode planning action for the list state", () => {
    expect(routeSource).toContain("showPlan={!selectedEpisode && displayEpisodes.length === 0}");
    expect(routeSource).toContain("showReplan={!selectedEpisode && displayEpisodes.length > 0}");
  });

  it("uses localized copy for the episode detail back action", () => {
    expect(routeSource).toContain('t("episode.list.backToEpisodes")');
    expect(routeSource).not.toContain("返回剧集列表");
  });

  it("gates both per-episode auto buttons on the story-version prompt", () => {
    // 故事版是分镜脚本/分镜图的硬前置：缺提示词时两枚按钮口径一致地禁按并给出原因，
    // 而不是一个能点一个不能点、或静默跳过故事版那一步。
    expect(routeSource).toContain(
      "disabled={autoRunning || autoCount === 0 || !autoScriptEnabled}",
    );
    expect(routeSource).toContain(
      "disabled={autoRunning || autoCount === 0 || !autoStoryboardEnabled}",
    );
    expect(routeSource).toContain("autoScriptEnabled={auto.canRunScript}");
    expect(routeSource).toContain("autoStoryboardEnabled={auto.canRunStoryboard}");
    expect(routeSource).toContain('t("episode.storyVersion.promptMissing")');
    // 整链「一键运行」已搬到故事页，分镜页不再挂控制条（只留两枚按集批量按钮）。
    expect(routeSource).not.toContain("PipelineControlBar");
  });

  it("opens a target picker before the storyboard automation instead of running at once", () => {
    // 「自动化生成分镜图」一枚按钮管两种产物（分镜草图 / 渲染图），
    // 所以点下去先开面板让用户勾，而不是直接按单一目标开跑。
    expect(routeSource).toContain('t("episode.list.autoStoryboard")');
    expect(routeSource).toContain(
      "onRunAutoStoryboard={() => setStoryboardDialogOpen(true)}",
    );
    expect(routeSource).toContain("<AutoStoryboardDialog");
    expect(routeSource).toContain(
      "void auto.runStoryboard(selectedEpisodesInput, targets)",
    );
    // 横幅的步骤表跟着勾选走，只勾草图时不多出一个走不到的格子。
    expect(routeSource).toContain("targets={auto.targets}");
    // 旧的「镜头草图」单目标入口不该留下死引用。
    expect(routeSource).not.toContain("autoSketch");
    expect(routeSource).not.toContain("runSketch");
  });
});
