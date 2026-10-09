// Tool registration and implementation for CapCut MCP server

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { apiClient } from '../services/api-client.js';
import { ResponseFormat } from '../types.js';
import {
  CreateDraftSchema,
  AddVideoSchema,
  AddAudioSchema,
  AddTextSchema,
  AddImageSchema,
  AddSubtitleSchema,
  AddKeyframeSchema,
  AddEffectSchema,
  AddStickerSchema,
  SaveDraftSchema,
  ListProjectsSchema,
  ReadProjectSchema,
  ReloadDesktopSchema,
  GetDurationSchema,
  type CreateDraftInput,
  type AddVideoInput,
  type AddAudioInput,
  type AddTextInput,
  type AddImageInput,
  type AddSubtitleInput,
  type AddKeyframeInput,
  type AddEffectInput,
  type AddStickerInput,
  type SaveDraftInput,
  type ListProjectsInput,
  type ReadProjectInput,
  type ReloadDesktopInput,
  type GetDurationInput
} from '../schemas/index.js';

// Utility function to format responses
function formatResponse(data: any, format: ResponseFormat): {
  content: Array<{ type: "text"; text: string }>;
  structuredContent?: any;
} {
  if (format === ResponseFormat.JSON) {
    return {
      content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }],
      structuredContent: data
    };
  } else {
    // Markdown format
    let markdown = '';
    if (data.draft_id) {
      markdown += `## Draft Created\n\n`;
      markdown += `- **Draft ID**: \`${data.draft_id}\`\n`;
      markdown += `- **Dimensions**: ${data.width}x${data.height}\n`;
      markdown += `- **FPS**: ${data.fps}\n`;
    } else if (data.duration !== undefined) {
      markdown += `## Media Duration\n\n`;
      markdown += `- **Duration**: ${data.duration.toFixed(2)}s\n`;
      if (data.format) markdown += `- **Format**: ${data.format}\n`;
      if (data.width) markdown += `- **Resolution**: ${data.width}x${data.height}\n`;
    } else if (data.draft_url) {
      markdown += `## Draft Saved\n\n`;
      markdown += `Draft saved successfully at:\n\`${data.draft_url}\`\n\n`;
      markdown += `Copy this folder to your CapCut drafts directory to open it in the application.\n`;
    } else {
      markdown += `## Operation Successful\n\n`;
      markdown += JSON.stringify(data, null, 2);
    }
    return {
      content: [{ type: "text" as const, text: markdown }],
      structuredContent: data
    };
  }
}

function handleError(error: unknown): {
  content: Array<{ type: "text"; text: string }>;
} {
  const message = error instanceof Error ? error.message : 'Unknown error occurred';
  return {
    content: [{
      type: "text" as const,
      text: `Error: ${message}\n\nPlease check that:\n- The CapCut API server is running\n- All required parameters are valid\n- Media URLs are accessible`
    }]
  };
}

export function registerTools(server: McpServer): void {
  // Tool 1: Create Draft
  server.registerTool(
    'capcut_create_draft',
    {
      title: 'Create CapCut Draft',
      description: `Create a new video editing draft with specified dimensions and frame rate.

This tool initializes a new draft project that can be edited by adding videos, audio, text, images, and effects.

Args:
  - width (number): Video width in pixels (360-4096, default: 1920)
  - height (number): Video height in pixels (360-4096, default: 1080)
  - fps (number): Frames per second (24-120, default: 30)
  - response_format ('markdown' | 'json'): Output format (default: 'markdown')

Returns:
  {
    "draft_id": string,      // Unique draft identifier for subsequent operations
    "width": number,         // Video width
    "height": number,        // Video height
    "fps": number,           // Frame rate
    "duration": number,      // Current duration (starts at 0)
    "created_at": string     // ISO timestamp
  }

Examples:
  - Create HD draft: params with width=1920, height=1080
  - Create vertical video: params with width=1080, height=1920
  - Create 4K draft: params with width=3840, height=2160`,
      inputSchema: CreateDraftSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false
      }
    },
    async (params: CreateDraftInput) => {
      try {
        const response = await apiClient.createDraft({
          width: params.width,
          height: params.height,
          fps: params.fps,
          ratio: params.ratio
        });

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to create draft');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 2: Add Video
  server.registerTool(
    'capcut_add_video',
    {
      title: 'Add Video to Draft',
      description: `Add a video clip to an existing draft with timing, volume, and effects.

This tool adds video content to the timeline with support for transitions, speed adjustments, and volume control.

Args:
  - draft_id (string): The draft ID from create_draft
  - video_url (string): URL to video file (mp4, mov, avi, mkv, webm, flv)
  - start (number): Start time in seconds (>= 0)
  - end (number): End time in seconds (> 0)
  - volume (number): Audio volume 0.0-1.0 (default: 1.0)
  - transition (string): Optional transition effect (fade_in, fade_out, dissolve, wipe, slide, zoom)
  - speed (number): Playback speed 0.1-10x (default: 1.0)
  - response_format ('markdown' | 'json'): Output format

Examples:
  - Add background video: draft_id="abc123", video_url="https://...", start=0, end=10
  - Add with slow motion: speed=0.5
  - Add with fade in: transition="fade_in"`,
      inputSchema: AddVideoSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true
      }
    },
    async (params: AddVideoInput) => {
      try {
        const response = await apiClient.addVideo(params);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to add video');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 3: Add Audio
  server.registerTool(
    'capcut_add_audio',
    {
      title: 'Add Audio to Draft',
      description: `Add audio track to draft with volume and fade effects.

This tool adds background music or sound effects to the video timeline.

Args:
  - draft_id (string): The draft ID
  - audio_url (string): URL to audio file (mp3, wav, aac, m4a, flac, ogg)
  - start (number): Start time in seconds
  - end (number): End time in seconds
  - volume (number): Audio volume 0.0-1.0 (default: 1.0)
  - fade_in (number): Fade in duration in seconds (default: 0)
  - fade_out (number): Fade out duration in seconds (default: 0)
  - response_format ('markdown' | 'json'): Output format

Examples:
  - Add background music: audio_url="https://...", volume=0.5
  - Add with fade: fade_in=2, fade_out=2`,
      inputSchema: AddAudioSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true
      }
    },
    async (params: AddAudioInput) => {
      try {
        const response = await apiClient.addAudio(params);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to add audio');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 4: Add Text
  server.registerTool(
    'capcut_add_text',
    {
      title: 'Add Text to Draft',
      description: `Add styled text overlay to video with positioning, colors, shadows, and animations.

This tool creates text elements with full styling control including fonts, colors, backgrounds, shadows, and animations.

Args:
  - draft_id (string): The draft ID
  - text (string): Text content to display (1-500 characters)
  - start (number): Start time in seconds
  - end (number): End time in seconds
  - font (string): Font family name (optional)
  - font_size (number): Font size 12-200 (default: 48)
  - font_color (string): Hex color e.g., #FFFFFF (default: #FFFFFF)
  - background_color (string): Background hex color (optional)
  - background_alpha (number): Background opacity 0.0-1.0 (default: 0.8)
  - shadow_enabled (boolean): Enable shadow (default: false)
  - shadow_color (string): Shadow hex color (default: #000000)
  - position_x (number): Horizontal position 0.0-1.0 (default: 0.5 center)
  - position_y (number): Vertical position 0.0-1.0 (default: 0.5 center)
  - animation (string): Animation effect (fade_in, slide_up, slide_down, slide_left, slide_right, zoom_in, bounce)
  - response_format ('markdown' | 'json'): Output format

Examples:
  - Add title: text="Welcome", font_size=72, position_y=0.2, animation="fade_in"
  - Add subtitle: text="Subscribe!", font_size=36, background_color="#000000"`,
      inputSchema: AddTextSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false
      }
    },
    async (params: AddTextInput) => {
      try {
        const response = await apiClient.addText(params);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to add text');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 5: Add Image
  server.registerTool(
    'capcut_add_image',
    {
      title: 'Add Image to Draft',
      description: `Add image overlay to video with positioning, scaling, rotation, and animation.

This tool adds static or animated images to the video timeline.

Args:
  - draft_id (string): The draft ID
  - image_url (string): URL to image file (jpg, jpeg, png, gif, webp, bmp)
  - start (number): Start time in seconds
  - end (number): End time in seconds
  - position_x (number): Horizontal position 0.0-1.0 (default: 0.5)
  - position_y (number): Vertical position 0.0-1.0 (default: 0.5)
  - scale (number): Scale multiplier 0.1-5.0 (default: 1.0)
  - rotation (number): Rotation angle 0-360 degrees (default: 0)
  - animation (string): Animation effect (optional)
  - response_format ('markdown' | 'json'): Output format

Examples:
  - Add logo: image_url="https://...", position_x=0.9, position_y=0.1, scale=0.3
  - Add rotating image: rotation=45, animation="zoom_in"`,
      inputSchema: AddImageSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true
      }
    },
    async (params: AddImageInput) => {
      try {
        const response = await apiClient.addImage(params);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to add image');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 6: Add Subtitle
  server.registerTool(
    'capcut_add_subtitle',
    {
      title: 'Add Subtitles to Draft',
      description: `Add subtitles from SRT file content with styling options.

This tool imports subtitles in SRT format and applies styling.

Args:
  - draft_id (string): The draft ID
  - srt_content (string): SRT formatted subtitle content
  - font (string): Font family name (optional)
  - font_size (number): Font size 12-100 (default: 36)
  - font_color (string): Hex color (default: #FFFFFF)
  - background_enabled (boolean): Enable background (default: true)
  - background_color (string): Background hex color (default: #000000)
  - response_format ('markdown' | 'json'): Output format

Example SRT format:
  1
  00:00:01,000 --> 00:00:03,000
  Welcome to my video

  2
  00:00:03,500 --> 00:00:05,000
  Subscribe for more content`,
      inputSchema: AddSubtitleSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false
      }
    },
    async (params: AddSubtitleInput) => {
      try {
        const response = await apiClient.addSubtitle(params);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to add subtitle');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 7: Add Keyframe
  server.registerTool(
    'capcut_add_keyframe',
    {
      title: 'Add Keyframe Animation',
      description: `Add keyframe-based property animation to tracks.

This tool creates smooth animations by interpolating between keyframe values.

Args:
  - draft_id (string): The draft ID
  - track_name (string): Name of track to animate
  - property_types (string[]): Properties to animate (scale_x, scale_y, alpha, rotation, position_x, position_y)
  - times (number[]): Keyframe times in seconds (at least 2)
  - values (string[]): Values for each keyframe (same length as times)
  - response_format ('markdown' | 'json'): Output format

Examples:
  - Fade in: property_types=["alpha"], times=[0, 2], values=["0.0", "1.0"]
  - Zoom in: property_types=["scale_x", "scale_y"], times=[0, 2], values=["0.5", "1.5"]
  - Rotate: property_types=["rotation"], times=[0, 3], values=["0", "360"]`,
      inputSchema: AddKeyframeSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false
      }
    },
    async (params: AddKeyframeInput) => {
      try {
        const response = await apiClient.addKeyframe(params);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to add keyframe');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 8: Add Effect
  server.registerTool(
    'capcut_add_effect',
    {
      title: 'Add Visual Effect',
      description: `Apply visual effects to video segments.

This tool adds effects like blur, sharpen, brightness adjustments, and more.

Args:
  - draft_id (string): The draft ID
  - effect_name (string): Effect to apply (blur, sharpen, brightness, contrast, saturation, vignette, grain, glitch)
  - start (number): Start time in seconds
  - end (number): End time in seconds
  - intensity (number): Effect intensity 0.0-1.0 (default: 0.5)
  - response_format ('markdown' | 'json'): Output format

Examples:
  - Add blur: effect_name="blur", intensity=0.7
  - Increase brightness: effect_name="brightness", intensity=0.8
  - Add vignette: effect_name="vignette", intensity=0.4`,
      inputSchema: AddEffectSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false
      }
    },
    async (params: AddEffectInput) => {
      try {
        const response = await apiClient.addEffect(params);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to add effect');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 9: Add Sticker
  server.registerTool(
    'capcut_add_sticker',
    {
      title: 'Add Sticker to Draft',
      description: `Add sticker/emoji overlay with positioning and transformation.

This tool adds decorative stickers or emojis to the video.

Args:
  - draft_id (string): The draft ID
  - sticker_url (string): URL to sticker image
  - start (number): Start time in seconds
  - end (number): End time in seconds
  - position_x (number): Horizontal position 0.0-1.0 (default: 0.5)
  - position_y (number): Vertical position 0.0-1.0 (default: 0.5)
  - scale (number): Scale multiplier 0.1-5.0 (default: 1.0)
  - rotation (number): Rotation angle 0-360 degrees (default: 0)
  - response_format ('markdown' | 'json'): Output format

Examples:
  - Add corner sticker: position_x=0.9, position_y=0.1, scale=0.2
  - Add rotating emoji: rotation=15, scale=0.5`,
      inputSchema: AddStickerSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: true
      }
    },
    async (params: AddStickerInput) => {
      try {
        const response = await apiClient.addSticker(params);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to add sticker');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 10: Save Draft
  server.registerTool(
    'capcut_save_draft',
    {
      title: 'Save Draft',
      description: `Save the draft and auto-deploy it to CapCut Desktop projects directory.

This tool finalizes the draft and writes both the root draft_content.json and the active Timelines/<UUID>/draft_content.json. By default, it automatically copies the project into your local CapCut projects directory (com.lveditor.draft) and clears stale lock files so it can be opened immediately.

Args:
  - draft_id (string): The draft ID to save
  - project_name (string): Optional custom project folder name in CapCut
  - auto_deploy (boolean): Automatically deploy to local CapCut projects (default: true)
  - response_format ('markdown' | 'json'): Output format

Returns:
  {
    "draft_url": string,    // Path to the saved / deployed draft folder
    "status": "saved"
  }`,
      inputSchema: SaveDraftSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false
      }
    },
    async (params: SaveDraftInput) => {
      try {
        const response = await apiClient.saveDraft(params.draft_id, params.project_name, params.auto_deploy, params.auto_reload);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to save draft');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 11: Get Media Duration
  server.registerTool(
    'capcut_get_duration',
    {
      title: 'Get Media Duration',
      description: `Get duration and metadata of video or audio file.

This tool analyzes media files to retrieve duration, format, and resolution information.

Args:
  - url (string): URL to media file
  - response_format ('markdown' | 'json'): Output format

Returns:
  {
    "duration": number,     // Duration in seconds
    "format": string,       // File format
    "width": number,        // Video width (if video)
    "height": number        // Video height (if video)
  }`,
      inputSchema: GetDurationSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: true
      }
    },
    async (params: GetDurationInput) => {
      try {
        const response = await apiClient.getDuration(params.url);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to get duration');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 12: List Projects
  server.registerTool(
    'capcut_list_projects',
    {
      title: 'List CapCut Desktop Projects',
      description: `Scan and list all projects currently available in your CapCut Desktop projects directory (com.lveditor.draft).

Returns:
  {
    "count": number,
    "projects": [
      {
        "name": string,
        "path": string,
        "is_locked": boolean,
        "has_draft_content": boolean,
        "modified_time": string
      }
    ]
  }`,
      inputSchema: ListProjectsSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false
      }
    },
    async (params: ListProjectsInput) => {
      try {
        const response = await apiClient.listProjects(params.filter);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to list projects');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 13: Read Project
  server.registerTool(
    'capcut_read_project',
    {
      title: 'Read CapCut Project Details',
      description: `Inspect an existing CapCut project on disk to read its resolution, frame rate, track counts, and materials summary.

Args:
  - project_name (string): Project folder name in CapCut or absolute path
  - response_format ('markdown' | 'json'): Output format`,
      inputSchema: ReadProjectSchema,
      annotations: {
        readOnlyHint: true,
        destructiveHint: false,
        idempotentHint: true,
        openWorldHint: false
      }
    },
    async (params: ReadProjectInput) => {
      try {
        const response = await apiClient.readProject(params.project_name);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to read project');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );

  // Tool 14: Reload CapCut Desktop
  server.registerTool(
    'capcut_reload_desktop',
    {
      title: 'Reload CapCut Desktop Project',
      description: `Hot-reload CapCut Desktop on Windows by cycling to the project home and reopening the active draft.

Use this tool whenever you want CapCut Desktop to immediately reflect new text, video clips, or subtitle changes without manually closing and reopening the editor.

Args:
  - project_name (string): Name of the project folder in CapCut (default: latest)
  - response_format ('markdown' | 'json'): Output format`,
      inputSchema: ReloadDesktopSchema,
      annotations: {
        readOnlyHint: false,
        destructiveHint: false,
        idempotentHint: false,
        openWorldHint: false
      }
    },
    async (params: ReloadDesktopInput) => {
      try {
        const response = await apiClient.reloadDesktop(params.project_name);

        if (!response.success || !response.result) {
          throw new Error(response.error || 'Failed to reload CapCut Desktop');
        }

        return formatResponse(response.result, params.response_format);
      } catch (error) {
        return handleError(error);
      }
    }
  );
}
