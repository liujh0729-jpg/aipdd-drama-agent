// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 内置 AIPDD Drama 服务器的「工具」详情：按用途分组展示后端
// aipdd_drama_tools.TOOLS 的实时清单，可搜索，点击工具行展开调用字段与必填说明。
// 新增工具未收录到分组时自动进「其它」组，不会从页面上消失。
// 嵌入在 MCP 服务器卡片的「工具」分页内渲染；用户自装服务器的工具经「测试连接」探测。
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAgentBuiltinMcpTools } from "@/lib/queries/agent";

import { McpToolList, type McpToolListItemShape } from "./McpToolList";

// 分组名册：与后端 aipdd_drama_tools.TOOLS 全量对齐（增删工具需同步此处）。
// descKey 对应 i18n ``mcp.tools.<descKey>``；未收录的工具自动进「其它」组。
const TOOL_GROUPS: ReadonlyArray<{
  key: string;
  tools: ReadonlyArray<{ name: string; descKey: string }>;
}> = [
  {
    key: "api",
    tools: [
      { name: "aipdd_drama_get", descKey: "get" },
      { name: "aipdd_drama_post", descKey: "post" },
      { name: "aipdd_drama_patch", descKey: "patch" },
      { name: "aipdd_drama_delete", descKey: "delete" },
      { name: "aipdd_drama_env_setup", descKey: "envSetup" },
      { name: "aipdd_drama_self_heal", descKey: "selfHeal" },
    ],
  },
  {
    key: "tasks",
    tools: [
      { name: "aipdd_drama_pipeline_status", descKey: "pipelineStatus" },
      { name: "aipdd_drama_list_tasks", descKey: "listTasks" },
      { name: "aipdd_drama_get_task", descKey: "getTask" },
      { name: "aipdd_drama_get_episode_script", descKey: "episodeScript" },
    ],
  },
  {
    // [aipdd fork delta] 一键自动化：步进驱动器自动出分镜（不生视频）+ 按分镜区间批量生视频。
    key: "automation",
    tools: [
      { name: "aipdd_drama_auto_storyboard", descKey: "autoStoryboard" },
      { name: "aipdd_drama_video_range", descKey: "videoRange" },
      { name: "aipdd_drama_regen_sketches", descKey: "regenSketches" },
      { name: "aipdd_drama_generate_video_prompts", descKey: "generateVideoPrompts" },
      { name: "aipdd_drama_run_pipeline", descKey: "runPipeline" },
      { name: "aipdd_drama_get_run_status", descKey: "getRunStatus" },
      { name: "aipdd_drama_cancel_run", descKey: "cancelRun" },
      { name: "aipdd_drama_suggest_config", descKey: "suggestConfig" },
    ],
  },
  {
    key: "planning",
    tools: [
      { name: "aipdd_drama_list_ingest_uploads", descKey: "ingestUploads" },
      { name: "aipdd_drama_build_characters", descKey: "buildCharacters" },
      { name: "aipdd_drama_update_character_face_prompt", descKey: "updateCharacterFacePrompt" },
      { name: "aipdd_drama_plan_episodes", descKey: "planEpisodes" },
      { name: "aipdd_drama_plan_identities", descKey: "planIdentities" },
      { name: "aipdd_drama_plan_scenes", descKey: "planScenes" },
      { name: "aipdd_drama_plan_props", descKey: "planProps" },
    ],
  },
  {
    key: "production",
    tools: [
      { name: "aipdd_drama_get_episode_story", descKey: "episodeStory" },
      { name: "aipdd_drama_save_episode_story", descKey: "saveEpisodeStory" },
      { name: "aipdd_drama_optimize_episode_story", descKey: "optimizeEpisodeStory" },
      { name: "aipdd_drama_story_to_script", descKey: "storyToScript" },
      { name: "aipdd_drama_generate_script", descKey: "generateScript" },
      { name: "aipdd_drama_generate_scene_master", descKey: "generateSceneMaster" },
      { name: "aipdd_drama_generate_scene_reverse", descKey: "generateSceneReverse" },
      { name: "aipdd_drama_generate_portrait", descKey: "generatePortrait" },
      { name: "aipdd_drama_generate_identity_image", descKey: "generateIdentityImage" },
      { name: "aipdd_drama_generate_sketches", descKey: "generateSketches" },
      { name: "aipdd_drama_detect_sketch_identities", descKey: "detectSketchIdentities" },
      { name: "aipdd_drama_generate_audio", descKey: "generateAudio" },
      { name: "aipdd_drama_start_single_video", descKey: "startSingleVideo" },
      { name: "aipdd_drama_render_first_frames", descKey: "renderFirstFrames" },
      { name: "aipdd_drama_compose_episode", descKey: "composeEpisode" },
      { name: "aipdd_drama_optimize_video_global", descKey: "optimizeVideoGlobal" },
    ],
  },
  {
    key: "media",
    tools: [
      { name: "aipdd_drama_get_sketches", descKey: "getSketches" },
      { name: "aipdd_drama_get_first_frames", descKey: "getFirstFrames" },
      { name: "aipdd_drama_get_sketch_candidates", descKey: "getSketchCandidates" },
      { name: "aipdd_drama_get_scene_images", descKey: "getSceneImages" },
      { name: "aipdd_drama_get_character_media", descKey: "getCharacterMedia" },
      { name: "aipdd_drama_get_episode_media", descKey: "getEpisodeMedia" },
      { name: "aipdd_drama_get_final_video", descKey: "getFinalVideo" },
    ],
  },
  {
    key: "editing",
    tools: [
      { name: "vectcut_health", descKey: "vectcutHealth" },
      { name: "vectcut_create_draft", descKey: "vectcutCreateDraft" },
      { name: "vectcut_add_image", descKey: "vectcutAddImage" },
      { name: "vectcut_add_video", descKey: "vectcutAddVideo" },
      { name: "vectcut_add_audio", descKey: "vectcutAddAudio" },
      { name: "vectcut_add_text", descKey: "vectcutAddText" },
      { name: "vectcut_add_subtitle", descKey: "vectcutAddSubtitle" },
      { name: "vectcut_add_effect", descKey: "vectcutAddEffect" },
      { name: "vectcut_add_sticker", descKey: "vectcutAddSticker" },
      { name: "vectcut_add_keyframe", descKey: "vectcutAddKeyframe" },
      { name: "vectcut_list_options", descKey: "vectcutListOptions" },
      { name: "vectcut_get_duration", descKey: "vectcutGetDuration" },
      { name: "vectcut_save_draft", descKey: "vectcutSaveDraft" },
    ],
  },
  {
    // [aipdd fork delta] 与前端「工具」栏目十面板一一对应；产物默认自动入库。
    key: "toolsPage",
    tools: [
      // [aipdd fork delta] 短视频口播稿创作：工具栏首项（处境 + 素材→能直接念的逐字稿）。
      { name: "aipdd_drama_oral_script", descKey: "oralScript" },
      { name: "aipdd_drama_video_clone", descKey: "videoClone" },
      { name: "aipdd_drama_video_mix", descKey: "videoMix" },
      // [aipdd fork delta] 白板动画视频（文案→分镜→逐幕线稿→逐笔揭示）。
      { name: "aipdd_drama_whiteboard_animation", descKey: "whiteboardAnimation" },
      // [aipdd fork delta] 科普讲解视频（参考资料→分章解说词→逐帧 MG）。
      { name: "aipdd_drama_explainer_video", descKey: "explainerVideo" },
      { name: "aipdd_drama_video_idea", descKey: "videoIdea" },
      { name: "aipdd_drama_voice_clone", descKey: "voiceClone" },
      // [aipdd fork delta req46] 音乐创作（MiniMax Music 3.0 五模式，直连不经网关）。
      { name: "aipdd_drama_music_create", descKey: "musicCreate" },
      { name: "aipdd_drama_lip_sync", descKey: "lipSync" },
      { name: "aipdd_drama_replace_person", descKey: "replacePerson" },
      { name: "aipdd_drama_replace_product", descKey: "replaceProduct" },
      { name: "aipdd_drama_video_watermark", descKey: "videoWatermark" },
      { name: "aipdd_drama_image_watermark", descKey: "imageWatermark" },
      { name: "aipdd_drama_douyin_copywrite", descKey: "douyinCopywrite" },
    ],
  },
  {
    // [aipdd fork delta] 画布生图/图片处理 MCP 工具组（与画布节点、工具页同源）。
    key: "imageTools",
    tools: [
      { name: "aipdd_drama_image_generate", descKey: "imageGenerate" },
      { name: "aipdd_drama_image_edit", descKey: "imageEdit" },
      { name: "aipdd_drama_image_upscale", descKey: "imageUpscale" },
      { name: "aipdd_drama_image_outpaint", descKey: "imageOutpaint" },
      { name: "aipdd_drama_image_relight", descKey: "imageRelight" },
      { name: "aipdd_drama_image_multi_view", descKey: "imageMultiView" },
      { name: "aipdd_drama_image_template_edit", descKey: "imageTemplateEdit" },
      { name: "aipdd_drama_image_redraw", descKey: "imageRedraw" },
      { name: "aipdd_drama_image_reverse_prompt", descKey: "imageReversePrompt" },
    ],
  },
  {
    // [aipdd fork delta] 画布生视频 MCP 工具组。
    key: "videoTools",
    tools: [
      { name: "aipdd_drama_video_generate", descKey: "videoGenerate" },
      { name: "aipdd_drama_video_i2v", descKey: "videoI2v" },
      { name: "aipdd_drama_video_omni_gen", descKey: "videoOmniGen" },
      { name: "aipdd_drama_video_upscale", descKey: "videoUpscale" },
      { name: "aipdd_drama_video_edit", descKey: "videoEdit" },
      { name: "aipdd_drama_video_extend", descKey: "videoExtend" },
    ],
  },
  {
    // [aipdd fork delta] 画布音频 MCP 工具组。
    key: "audioTools",
    tools: [
      { name: "aipdd_drama_audio_separate", descKey: "audioSeparate" },
      { name: "aipdd_drama_audio_music", descKey: "audioMusic" },
    ],
  },
  {
    // [aipdd fork delta] 文本/脚本与文案提取 MCP 工具组。
    key: "textTools",
    tools: [
      { name: "aipdd_drama_text_generate", descKey: "textGenerate" },
      { name: "aipdd_drama_text_translate", descKey: "textTranslate" },
      { name: "aipdd_drama_story_script_generate", descKey: "storyScriptGenerate" },
      { name: "aipdd_drama_video_script_reverse", descKey: "videoScriptReverse" },
      { name: "aipdd_drama_media_copywrite", descKey: "mediaCopywrite" },
    ],
  },
  {
    // [aipdd fork delta] 资源与模型目录读工具组（调用前先看有什么可用）。
    key: "resourceTools",
    tools: [
      { name: "aipdd_drama_list_assets", descKey: "listAssets" },
      { name: "aipdd_drama_get_character_library", descKey: "getCharacterLibrary" },
      { name: "aipdd_drama_get_audio_references", descKey: "getAudioReferences" },
      { name: "aipdd_drama_get_models", descKey: "getModels" },
    ],
  },
];

export function McpToolCatalog() {
  const { t } = useTranslation();
  const catalogQuery = useAgentBuiltinMcpTools();
  const [search, setSearch] = useState("");

  const allTools = useMemo(() => catalogQuery.data?.data.tools ?? [], [catalogQuery.data]);

  const groups = useMemo(() => {
    const byName = new Map(allTools.map((tool) => [tool.name, tool]));
    const keyword = search.trim().toLowerCase();
    const matches = (tool: McpToolListItemShape) =>
      !keyword ||
      tool.name.toLowerCase().includes(keyword) ||
      tool.description.toLowerCase().includes(keyword);
    const rendered: Array<{ key: string; tools: McpToolListItemShape[] }> = [];
    const claimed = new Set<string>();
    for (const group of TOOL_GROUPS) {
      const tools: McpToolListItemShape[] = [];
      for (const entry of group.tools) {
        claimed.add(entry.name);
        const raw = byName.get(entry.name);
        if (!raw) continue;
        const item: McpToolListItemShape = {
          name: raw.name,
          description: t(`mcp.tools.${entry.descKey}`),
          inputSchema: raw.input_schema,
        };
        if (matches(item)) tools.push(item);
      }
      if (tools.length > 0) rendered.push({ key: group.key, tools });
    }
    const rest = allTools
      .filter((raw) => !claimed.has(raw.name))
      .map((raw) => ({
        name: raw.name,
        description: raw.description,
        inputSchema: raw.input_schema,
      }))
      .filter(matches);
    if (rest.length > 0) rendered.push({ key: "other", tools: rest });
    return rendered;
  }, [allTools, search, t]);

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium text-foreground">{t("mcp.catalog.title")}</h3>
      <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{t("mcp.catalog.hint")}</p>
      <div className="relative mt-2">
        <Search
          className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder={t("mcp.catalog.search")}
          className="h-8 pl-8 text-xs"
        />
      </div>
      {catalogQuery.isLoading ? (
        <div className="flex justify-center py-4">
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden="true" />
        </div>
      ) : catalogQuery.isError ? (
        <div className="mt-3 flex items-center gap-2">
          <p className="text-xs text-muted-foreground">{t("mcp.catalog.loadFailed")}</p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-7 gap-1.5 rounded-[7px] border-white/10 bg-white/[0.025] px-2 text-xs font-normal text-foreground shadow-none hover:border-primary/45 hover:bg-primary/12 hover:text-primary"
            onClick={() => void catalogQuery.refetch()}
          >
            {t("mcp.catalog.retry")}
          </Button>
        </div>
      ) : groups.length === 0 ? (
        <p className="mt-3 rounded-lg border border-white/[0.06] bg-white/[0.018] px-3 py-3 text-xs text-muted-foreground">
          {t("mcp.catalog.noMatch")}
        </p>
      ) : (
        <div className="mt-3 space-y-3">
          {groups.map((group) => (
            <div key={group.key}>
              <h3 className="mb-1.5 text-xs font-medium text-foreground">
                {t(`mcp.toolGroups.${group.key}`)}
                <span className="ml-1.5 font-normal text-muted-foreground">({group.tools.length})</span>
              </h3>
              <McpToolList tools={group.tools} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}