// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export const queryKeys = {
  currentUser: () => ["auth", "me"] as const,
  accountSecurity: () => ["account", "security"] as const,
  productSurfaces: () => ["product-surfaces", "me"] as const,
  // [aipdd fork delta] WP-D: 积分汇总 / 流水 / 充值套餐的查询键随额度体系一起拆除。
  org: () => ["org"] as const,
  orgMe: () => ["org", "me"] as const,
  orgBranding: () => ["org", "branding"] as const,
  projects: () => ["projects"] as const,
  projectSummaries: () => ["projects", "summaries"] as const,
  project: (p: string) => ["projects", p] as const,
  projectGrants: (p: string) => ["projects", p, "grants"] as const,
  userSearch: (project: string, q: string) =>
    ["users", "search", project, q] as const,
  pipelineStatus: (p: string) => ["projects", p, "pipeline-status"] as const,
  characters: (p: string) => ["projects", p, "characters"] as const,
  characterDetails: (p: string, name: string) =>
    ["projects", p, "characters", "details", name] as const,
  character: (p: string, name: string) =>
    ["projects", p, "characters", name] as const,
  characterVoiceSamples: (p: string, name: string) =>
    ["projects", p, "characters", name, "voice-samples"] as const,
  characterAssetHistories: (p: string, name: string) =>
    ["projects", p, "characters", name, "asset-history"] as const,
  characterAssetHistory: (p: string, name: string, url: string) =>
    ["projects", p, "characters", name, "asset-history", url] as const,
  identities: (p: string, name: string) =>
    ["projects", p, "characters", name, "identities"] as const,
  assetReferences: (p: string) => ["projects", p, "asset-references"] as const,
  // Nested under assetReferences so one prefix invalidation drops every
  // per-asset detail slice that is currently cached.
  assetReferenceDetail: (p: string, signature: string) =>
    ["projects", p, "asset-references", "detail", signature] as const,
  scenes: (p: string) => ["projects", p, "scenes"] as const,
  sceneDetails: (p: string, signature: string) =>
    ["projects", p, "scenes", "details", signature] as const,
  scenePlatePreview: (
    p: string,
    sceneId: string,
    variantId: string,
    timeOfDay: string,
  ) =>
    [
      "projects",
      p,
      "scenes",
      "plate-preview",
      sceneId,
      variantId,
      timeOfDay,
    ] as const,
  scene: (p: string, name: string) =>
    ["projects", p, "scenes", name] as const,
  scenePanoManifest: (p: string, name: string) =>
    ["projects", p, "scenes", name, "pano-manifest"] as const,
  sceneDirectorStageManifest: (p: string, name: string) =>
    ["projects", p, "scenes", name, "director-stage-manifest"] as const,
  props: (p: string) => ["projects", p, "props"] as const,
  prop: (p: string, name: string) => ["projects", p, "props", name] as const,
  episodes: (p: string) => ["projects", p, "episodes"] as const,
  episode: (p: string, ep: number) =>
    ["projects", p, "episodes", ep] as const,
  episodeDetail: (p: string, ep: number) =>
    ["projects", p, "episodes", ep, "detail"] as const,
  chapters: (p: string) => ["projects", p, "chapters"] as const,
  chapterPreview: (p: string, spineTemplate: string) =>
    ["projects", p, "chapters", spineTemplate] as const,
  knowledgeGraph: (p: string) => ["projects", p, "knowledge-graph"] as const,
  // [aipdd fork delta] 场景头自动补全结果（增强文本 + 导入格式复检）。
  autoSceneHeaders: (p: string) =>
    ["projects", p, "auto-scene-headers"] as const,
  // [aipdd fork delta] L1「故事」：项目故事源与整本剧本生成任务。
  storySource: (p: string) => ["projects", p, "story", "source"] as const,
  storyScriptJob: (p: string) =>
    ["projects", p, "story", "generate-script"] as const,
  storyScriptEpisode: (p: string, ep: number) =>
    ["projects", p, "story", "generate-script", "episodes", ep] as const,
  script: (p: string, ep: number) =>
    ["projects", p, "episodes", ep, "script"] as const,
  beats: (p: string, ep: number) =>
    ["projects", p, "episodes", ep, "beats"] as const,
  grids: (p: string, ep: number) =>
    ["projects", p, "episodes", ep, "grids"] as const,
  sketchRegenQueue: (p: string, ep: number) =>
    ["projects", p, "episodes", ep, "sketch-regen-queue"] as const,
  sketchImageUsage: (p: string, ep: number) =>
    ["projects", p, "episodes", ep, "sketch-image-usage"] as const,
  sketchPoseEditor: (p: string, ep: number, beat: number) =>
    ["projects", p, "episodes", ep, "beats", beat, "sketch-pose-editor"] as const,
  beatPanoBackgroundManifest: (p: string, ep: number, beat: number) =>
    ["projects", p, "episodes", ep, "beats", beat, "pano-background-manifest"] as const,
  beatDirectorStageManifest: (p: string, ep: number, beat: number) =>
    ["projects", p, "episodes", ep, "beats", beat, "director-stage-manifest"] as const,
  beatBackgroundAnchors: (p: string, ep: number, beat: number) =>
    ["projects", p, "episodes", ep, "beats", beat, "background-anchors"] as const,
  directorControlFrame: (p: string, ep: number, beat: number) =>
    ["projects", p, "episodes", ep, "beats", beat, "director-control-frame"] as const,
  videoPool: (p: string, ep: number) =>
    ["projects", p, "episodes", ep, "video-pool"] as const,
  videoBackends: (p: string) => ["projects", p, "video-backends"] as const,
  renderSettings: (p: string) => ["projects", p, "render-settings"] as const,
  sketchSettings: (p: string) => ["projects", p, "sketch-settings"] as const,
  narratorVoice: (p: string) => ["projects", p, "narrator-voice"] as const,
  narratorVoiceSources: (p: string) =>
    ["projects", p, "narrator-voice", "sources"] as const,
  finalVideo: (p: string, ep: number) =>
    ["projects", p, "episodes", ep, "final-video"] as const,
  tasks: (p?: string) => (p ? (["projects", p, "tasks"] as const) : (["tasks"] as const)),
  freezoneCanvases: (p: string) =>
    ["projects", p, "freezone", "canvases"] as const,
  freezoneProjectAssets: (p: string) =>
    ["projects", p, "freezone", "assets"] as const,
  freezoneAssetLibrary: (p: string) =>
    ["projects", p, "freezone", "asset-library"] as const,
  freezoneAssetLibraryFolders: (p: string) =>
    ["projects", p, "freezone", "asset-library", "folders"] as const,
  freezoneBeatContext: (p: string, episode?: number | null, beat?: number | null) =>
    ["projects", p, "freezone", "beat-context", episode ?? null, beat ?? null] as const,
  styles: (p?: string) =>
    p ? (["styles", p] as const) : (["styles"] as const),
  style: (id: string) => ["styles", "detail", id] as const,
  ttsVoices: (p: string) => ["projects", p, "tts", "voices"] as const,
  modelGateway: () => ["model-gateway", "config"] as const,
  // [aipdd fork delta] WP-E: 模型广场（只读目录 + 探活），按 connector 分区可分别缓存。
  modelPlaza: (connector?: string) =>
    connector ? (["model-plaza", connector] as const) : (["model-plaza"] as const),
  // [aipdd fork delta req47] 设置 → Seedance 素材库。探测结论本身就是一条键：
  // 不支持时组/素材列表压根不该发请求，所以它们各自带 enabled 闸门。
  seedanceAssetLibrary: () => ["seedance-assets", "library"] as const,
  seedanceAssetGroups: (groupType: string, name: string) =>
    ["seedance-assets", "groups", groupType, name] as const,
  seedanceAssets: (groupType: string, groupIds: string, statuses: string) =>
    ["seedance-assets", "assets", groupType, groupIds, statuses] as const,
  // [aipdd fork delta] 设置 → 提示词库。
  promptLibrary: () => ["prompt-library"] as const,
  // [aipdd fork delta] 设置 → 风格管理（本地图墙封面）。
  styleGalleryStyles: () => ["style-gallery", "styles"] as const,
  // [aipdd fork delta] 「AI 生成封面」可选的图片模型目录（scoped，与生成端同源）。
  styleGalleryImageModels: () => ["style-gallery", "image-models"] as const,
  // [aipdd fork delta] 设置 → 3D 世界（运行时按需安装）。
  worldStatus: () => ["admin", "world", "status"] as const,
  // [aipdd fork delta] 设置 → 环境安装（部署环境运行依赖只读探测）。
  envStatus: () => ["admin", "env-setup", "status"] as const,
  // [aipdd fork delta req-automcp] 环境补齐：探测 → 待办计划 → 逐项安装。
  envPlan: () => ["admin", "env-setup", "plan"] as const,
  // [aipdd fork delta] 设置 → 系统整体自检（环境 / 模型渠道 / 默认模型 / 存储一盘只读体检）。
  diagnostics: () => ["admin", "diagnostics"] as const,
  // [aipdd fork delta] 用户管理（仅超管）与首次初始化探测。
  adminUsers: () => ["admin", "users"] as const,
  setupStatus: () => ["auth", "setup-status"] as const,
  // [aipdd fork delta] 设置 → API 密钥（统一密钥：外部 MCP + 模型广场）。
  apiKeys: () => ["api-keys"] as const,
  releaseNotifications: (locale: string) => ["release-notifications", locale] as const,
  // [aipdd fork delta] Agent 模块：状态 / 设置 / 会话 / 消息 / 记忆 / 技能 / MCP 服务器。
  agentStatus: () => ["agent", "status"] as const,
  agentSettings: () => ["agent", "settings"] as const,
  agentConversations: (project?: string) =>
    project
      ? (["agent", "conversations", project] as const)
      : (["agent", "conversations"] as const),
  agentMessages: (conversationId: string) =>
    ["agent", "conversations", conversationId, "messages"] as const,
  // [aipdd fork delta] 待办计划（项目级；AI 工具与「待办计划」面板共用这份清单）。
  agentPlan: (project: string) => ["agent", "plan", project] as const,
  agentMemory: () => ["agent", "memory"] as const,
  agentSkills: () => ["agent", "skills"] as const,
  agentMcpServers: () => ["agent", "mcp", "servers"] as const,
  agentBuiltinMcpTools: () => ["agent", "mcp", "builtin-tools"] as const,
  agentLocalTools: () => ["agent", "local-tools"] as const,
};
