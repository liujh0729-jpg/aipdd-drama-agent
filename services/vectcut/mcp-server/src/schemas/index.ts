// Zod validation schemas for CapCut MCP tools

import { z } from 'zod';
import { ResponseFormat } from '../types.js';
import {
  TRANSITIONS,
  TEXT_ANIMATIONS,
  AVAILABLE_EFFECTS
} from '../constants.js';

// Common schemas
export const ResponseFormatSchema = z.nativeEnum(ResponseFormat)
  .default(ResponseFormat.MARKDOWN)
  .describe("Output format: 'markdown' for human-readable or 'json' for machine-readable");

const UrlSchema = z.string()
  .min(1, 'Path or URL cannot be empty')
  .describe('URL or local file path to the media file');

// Draft creation schema
export const CreateDraftSchema = z.object({
  width: z.number()
    .int()
    .min(360, 'Width must be at least 360')
    .max(4096, 'Width must not exceed 4096')
    .default(1920)
    .describe('Video width in pixels'),
  height: z.number()
    .int()
    .min(360, 'Height must be at least 360')
    .max(4096, 'Height must not exceed 4096')
    .default(1080)
    .describe('Video height in pixels'),
  fps: z.number()
    .int()
    .min(24, 'FPS must be at least 24')
    .max(120, 'FPS must not exceed 120')
    .default(30)
    .describe('Frames per second'),
  ratio: z.enum(['9:16', '16:9', '1:1', 'original'])
    .default('9:16')
    .optional()
    .describe("Canvas aspect ratio ('9:16', '16:9', '1:1', or 'original')"),
  response_format: ResponseFormatSchema
}).strict();

// Video track schema
export const AddVideoSchema = z.object({
  draft_id: z.string()
    .min(1, 'Draft ID is required')
    .describe('The ID of the draft to add video to'),
  video_url: UrlSchema,
  start: z.number()
    .min(0, 'Start time must be non-negative')
    .default(0)
    .describe('Start time in seconds'),
  end: z.number()
    .positive('End time must be positive')
    .optional()
    .describe('End time in seconds'),
  target_start: z.number()
    .min(0)
    .default(0)
    .optional()
    .describe('Target start time on the timeline in seconds'),
  track_name: z.string()
    .optional()
    .describe('Track name (e.g., video_main, avatar_overlay)'),
  relative_index: z.number()
    .int()
    .default(0)
    .optional()
    .describe('Layer rendering order index'),
  transform_x: z.number()
    .default(0)
    .optional()
    .describe('Horizontal transform (-1.0 to 1.0)'),
  transform_y: z.number()
    .default(0)
    .optional()
    .describe('Vertical transform (-1.0 to 1.0)'),
  scale_x: z.number()
    .default(1.0)
    .optional()
    .describe('Horizontal scale multiplier'),
  scale_y: z.number()
    .default(1.0)
    .optional()
    .describe('Vertical scale multiplier'),
  mask_type: z.string()
    .optional()
    .describe('Mask type (circle, linear, mirror, rectangle, heart, star)'),
  mask_center_x: z.number()
    .default(0.5)
    .optional()
    .describe('Mask center X (0.0 to 1.0)'),
  mask_center_y: z.number()
    .default(0.5)
    .optional()
    .describe('Mask center Y (0.0 to 1.0)'),
  mask_size: z.number()
    .default(1.0)
    .optional()
    .describe('Mask size (0.0 to 1.0)'),
  mask_rotation: z.number()
    .default(0.0)
    .optional()
    .describe('Mask rotation in degrees'),
  mask_feather: z.number()
    .default(0.0)
    .optional()
    .describe('Mask feather level (0.0 to 1.0)'),
  mask_invert: z.boolean()
    .default(false)
    .optional()
    .describe('Whether to invert the mask'),
  volume: z.number()
    .min(0, 'Volume must be between 0 and 1')
    .max(1, 'Volume must be between 0 and 1')
    .default(1.0)
    .describe('Audio volume (0.0 to 1.0)'),
  transition: z.enum(TRANSITIONS as [string, ...string[]])
    .optional()
    .describe('Transition effect to apply'),
  transition_duration: z.number()
    .default(0.5)
    .optional()
    .describe('Transition duration in seconds'),
  speed: z.number()
    .min(0.1, 'Speed must be at least 0.1x')
    .max(10, 'Speed must not exceed 10x')
    .default(1.0)
    .describe('Playback speed multiplier'),
  duration: z.number()
    .optional()
    .describe('Total video duration in seconds'),
  draft_folder: z.string()
    .optional()
    .describe('Target draft directory path'),
  response_format: ResponseFormatSchema
}).strict();

// Audio track schema
export const AddAudioSchema = z.object({
  draft_id: z.string()
    .min(1, 'Draft ID is required')
    .describe('The ID of the draft to add audio to'),
  audio_url: UrlSchema,
  start: z.number()
    .min(0, 'Start time must be non-negative')
    .describe('Start time in seconds'),
  end: z.number()
    .positive('End time must be positive')
    .describe('End time in seconds'),
  volume: z.number()
    .min(0, 'Volume must be between 0 and 1')
    .max(1, 'Volume must be between 0 and 1')
    .default(1.0)
    .describe('Audio volume (0.0 to 1.0)'),
  fade_in: z.number()
    .min(0)
    .default(0)
    .describe('Fade in duration in seconds'),
  fade_out: z.number()
    .min(0)
    .default(0)
    .describe('Fade out duration in seconds'),
  response_format: ResponseFormatSchema
}).strict();

// Text schema
export const AddTextSchema = z.object({
  draft_id: z.string()
    .min(1, 'Draft ID is required')
    .describe('The ID of the draft to add text to'),
  text: z.string()
    .min(1, 'Text content is required')
    .max(500, 'Text must not exceed 500 characters')
    .describe('The text content to display'),
  start: z.number()
    .min(0, 'Start time must be non-negative')
    .describe('Start time in seconds'),
  end: z.number()
    .positive('End time must be positive')
    .describe('End time in seconds'),
  font: z.string()
    .optional()
    .describe('Font family name'),
  font_size: z.number()
    .int()
    .min(12, 'Font size must be at least 12')
    .max(200, 'Font size must not exceed 200')
    .default(48)
    .describe('Font size in points'),
  font_color: z.string()
    .regex(/^#[0-9A-Fa-f]{6}$/, 'Must be a valid hex color (e.g., #FFFFFF)')
    .default('#FFFFFF')
    .describe('Font color in hex format'),
  background_color: z.string()
    .regex(/^#[0-9A-Fa-f]{6}$/, 'Must be a valid hex color')
    .optional()
    .describe('Background color in hex format'),
  background_alpha: z.number()
    .min(0, 'Alpha must be between 0 and 1')
    .max(1, 'Alpha must be between 0 and 1')
    .default(0.8)
    .describe('Background opacity (0.0 to 1.0)'),
  shadow_enabled: z.boolean()
    .default(false)
    .describe('Enable text shadow'),
  shadow_color: z.string()
    .regex(/^#[0-9A-Fa-f]{6}$/, 'Must be a valid hex color')
    .default('#000000')
    .describe('Shadow color in hex format'),
  position_x: z.number()
    .min(0)
    .max(1)
    .default(0.5)
    .describe('Horizontal position (0.0 to 1.0, where 0.5 is center)'),
  position_y: z.number()
    .min(0)
    .max(1)
    .default(0.5)
    .describe('Vertical position (0.0 to 1.0, where 0.5 is center)'),
  bold: z.boolean()
    .default(false)
    .optional()
    .describe('Bold font weight'),
  italic: z.boolean()
    .default(false)
    .optional()
    .describe('Italic font style'),
  underline: z.boolean()
    .default(false)
    .optional()
    .describe('Underline text decoration'),
  alignment: z.enum(['left', 'center', 'right'])
    .default('center')
    .optional()
    .describe('Text alignment (left, center, right)'),
  line_spacing: z.number()
    .min(0)
    .max(5)
    .default(0.25)
    .optional()
    .describe('Line spacing between lines for multi-line text (default: 0.25)'),
  letter_spacing: z.number()
    .min(0)
    .max(5)
    .default(0.0)
    .optional()
    .describe('Letter spacing / tracking (default: 0.0)'),
  animation: z.enum(TEXT_ANIMATIONS as [string, ...string[]])
    .optional()
    .describe('Animation effect to apply'),
  response_format: ResponseFormatSchema
}).strict();

// Image schema
export const AddImageSchema = z.object({
  draft_id: z.string()
    .min(1, 'Draft ID is required')
    .describe('The ID of the draft to add image to'),
  image_url: UrlSchema,
  start: z.number()
    .min(0, 'Start time must be non-negative')
    .describe('Start time in seconds'),
  end: z.number()
    .positive('End time must be positive')
    .describe('End time in seconds'),
  position_x: z.number()
    .min(0)
    .max(1)
    .default(0.5)
    .describe('Horizontal position (0.0 to 1.0)'),
  position_y: z.number()
    .min(0)
    .max(1)
    .default(0.5)
    .describe('Vertical position (0.0 to 1.0)'),
  scale: z.number()
    .min(0.1, 'Scale must be at least 0.1')
    .max(5, 'Scale must not exceed 5')
    .default(1.0)
    .describe('Scale multiplier'),
  rotation: z.number()
    .min(0)
    .max(360)
    .default(0)
    .describe('Rotation angle in degrees'),
  animation: z.string()
    .optional()
    .describe('Animation effect to apply'),
  response_format: ResponseFormatSchema
}).strict();

// Subtitle schema
export const AddSubtitleSchema = z.object({
  draft_id: z.string()
    .min(1, 'Draft ID is required')
    .describe('The ID of the draft to add subtitles to'),
  srt_content: z.string()
    .min(1, 'SRT content is required')
    .describe('SRT formatted subtitle content'),
  font: z.string()
    .optional()
    .describe('Font family name'),
  font_size: z.number()
    .int()
    .min(12)
    .max(100)
    .default(36)
    .describe('Font size in points'),
  font_color: z.string()
    .regex(/^#[0-9A-Fa-f]{6}$/, 'Must be a valid hex color')
    .default('#FFFFFF')
    .describe('Font color in hex format'),
  background_enabled: z.boolean()
    .default(true)
    .describe('Enable background behind text'),
  background_color: z.string()
    .regex(/^#[0-9A-Fa-f]{6}$/, 'Must be a valid hex color')
    .default('#000000')
    .describe('Background color in hex format'),
  response_format: ResponseFormatSchema
}).strict();

// Keyframe schema
export const AddKeyframeSchema = z.object({
  draft_id: z.string()
    .min(1, 'Draft ID is required')
    .describe('The ID of the draft to add keyframes to'),
  track_name: z.string()
    .min(1, 'Track name is required')
    .describe('Name of the track to animate'),
  property_types: z.array(z.string())
    .min(1, 'At least one property type is required')
    .describe('Properties to animate (e.g., scale_x, scale_y, alpha, rotation)'),
  times: z.array(z.number())
    .min(2, 'At least 2 keyframe times are required')
    .describe('Keyframe times in seconds'),
  values: z.array(z.string())
    .min(2, 'At least 2 values are required')
    .describe('Values for each keyframe'),
  response_format: ResponseFormatSchema
}).strict();

// Effect schema
export const AddEffectSchema = z.object({
  draft_id: z.string()
    .min(1, 'Draft ID is required')
    .describe('The ID of the draft to add effect to'),
  effect_name: z.enum(AVAILABLE_EFFECTS as [string, ...string[]])
    .describe('Name of the effect to apply'),
  start: z.number()
    .min(0, 'Start time must be non-negative')
    .describe('Start time in seconds'),
  end: z.number()
    .positive('End time must be positive')
    .describe('End time in seconds'),
  intensity: z.number()
    .min(0, 'Intensity must be between 0 and 1')
    .max(1, 'Intensity must be between 0 and 1')
    .default(0.5)
    .describe('Effect intensity (0.0 to 1.0)'),
  response_format: ResponseFormatSchema
}).strict();

// Sticker schema
export const AddStickerSchema = z.object({
  draft_id: z.string()
    .min(1, 'Draft ID is required')
    .describe('The ID of the draft to add sticker to'),
  sticker_url: UrlSchema,
  start: z.number()
    .min(0, 'Start time must be non-negative')
    .describe('Start time in seconds'),
  end: z.number()
    .positive('End time must be positive')
    .describe('End time in seconds'),
  position_x: z.number()
    .min(0)
    .max(1)
    .default(0.5)
    .describe('Horizontal position (0.0 to 1.0)'),
  position_y: z.number()
    .min(0)
    .max(1)
    .default(0.5)
    .describe('Vertical position (0.0 to 1.0)'),
  scale: z.number()
    .min(0.1)
    .max(5)
    .default(1.0)
    .describe('Scale multiplier'),
  rotation: z.number()
    .min(0)
    .max(360)
    .default(0)
    .describe('Rotation angle in degrees'),
  response_format: ResponseFormatSchema
}).strict();

// Save draft schema
export const SaveDraftSchema = z.object({
  draft_id: z.string()
    .min(1, 'Draft ID is required')
    .describe('The ID of the draft to save'),
  project_name: z.string()
    .optional()
    .describe('Custom project folder name in CapCut'),
  auto_deploy: z.boolean()
    .default(true)
    .optional()
    .describe('Automatically copy into CapCut Desktop projects directory (default: true)'),
  auto_reload: z.boolean()
    .default(false)
    .optional()
    .describe('Automatically trigger CapCut Desktop hot-reload after saving (default: false)'),
  response_format: ResponseFormatSchema
}).strict();

// List projects schema
export const ListProjectsSchema = z.object({
  filter: z.string()
    .optional()
    .describe('Optional search keyword to filter project names'),
  response_format: ResponseFormatSchema
}).strict();

// Read project schema
export const ReadProjectSchema = z.object({
  project_name: z.string()
    .min(1, 'Project name is required')
    .describe('Name of the project folder in CapCut or absolute path'),
  response_format: ResponseFormatSchema
}).strict();

// Reload desktop schema
export const ReloadDesktopSchema = z.object({
  project_name: z.string()
    .optional()
    .describe('Name of the project to reopen in CapCut Desktop'),
  response_format: ResponseFormatSchema
}).strict();

// Get duration schema
export const GetDurationSchema = z.object({
  url: UrlSchema.describe('URL to the media file to analyze'),
  response_format: ResponseFormatSchema
}).strict();

// Export type inference helpers
export type CreateDraftInput = z.infer<typeof CreateDraftSchema>;
export type AddVideoInput = z.infer<typeof AddVideoSchema>;
export type AddAudioInput = z.infer<typeof AddAudioSchema>;
export type AddTextInput = z.infer<typeof AddTextSchema>;
export type AddImageInput = z.infer<typeof AddImageSchema>;
export type AddSubtitleInput = z.infer<typeof AddSubtitleSchema>;
export type AddKeyframeInput = z.infer<typeof AddKeyframeSchema>;
export type AddEffectInput = z.infer<typeof AddEffectSchema>;
export type AddStickerInput = z.infer<typeof AddStickerSchema>;
export type SaveDraftInput = z.infer<typeof SaveDraftSchema>;
export type ListProjectsInput = z.infer<typeof ListProjectsSchema>;
export type ReadProjectInput = z.infer<typeof ReadProjectSchema>;
export type ReloadDesktopInput = z.infer<typeof ReloadDesktopSchema>;
export type GetDurationInput = z.infer<typeof GetDurationSchema>;

