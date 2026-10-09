// Shared constants for CapCut MCP Server

export const API_BASE_URL = process.env.CAPCUT_API_URL || 'http://127.0.0.1:9001';
export const CHARACTER_LIMIT = 15000;
export const DEFAULT_FPS = 30;
export const DEFAULT_VIDEO_RESOLUTION = {
  width: 1920,
  height: 1080
};

export const SUPPORTED_VIDEO_FORMATS = [
  'mp4', 'mov', 'avi', 'mkv', 'webm', 'flv'
];

export const SUPPORTED_AUDIO_FORMATS = [
  'mp3', 'wav', 'aac', 'm4a', 'flac', 'ogg'
];

export const SUPPORTED_IMAGE_FORMATS = [
  'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'
];

export const TRANSITIONS = [
  'fade_in',
  'fade_out',
  'dissolve',
  'wipe',
  'slide',
  'zoom'
];

export const TEXT_ANIMATIONS = [
  'fade_in',
  'slide_up',
  'slide_down',
  'slide_left',
  'slide_right',
  'zoom_in',
  'bounce'
];

export const AVAILABLE_EFFECTS = [
  'blur',
  'sharpen',
  'brightness',
  'contrast',
  'saturation',
  'vignette',
  'grain',
  'glitch'
];
