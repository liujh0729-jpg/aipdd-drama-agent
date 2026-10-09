// Type definitions for CapCut API

export interface DraftConfig {
  width: number;
  height: number;
  fps?: number;
}

export interface Draft {
  draft_id: string;
  width: number;
  height: number;
  fps: number;
  duration: number;
  created_at: string;
}

export interface VideoTrack {
  video_url: string;
  start: number;
  end: number;
  volume?: number;
  transition?: string;
  speed?: number;
}

export interface AudioTrack {
  audio_url: string;
  start: number;
  end: number;
  volume?: number;
  fade_in?: number;
  fade_out?: number;
}

export interface TextConfig {
  text: string;
  start: number;
  end: number;
  font?: string;
  font_size?: number;
  font_color?: string;
  background_color?: string;
  background_alpha?: number;
  shadow_enabled?: boolean;
  shadow_color?: string;
  position_x?: number;
  position_y?: number;
  animation?: string;
}

export interface ImageConfig {
  image_url: string;
  start: number;
  end: number;
  position_x?: number;
  position_y?: number;
  scale?: number;
  rotation?: number;
  animation?: string;
}

export interface SubtitleConfig {
  srt_content: string;
  font?: string;
  font_size?: number;
  font_color?: string;
  background_enabled?: boolean;
  background_color?: string;
}

export interface KeyframeConfig {
  draft_id: string;
  track_name: string;
  property_types: string[];
  times: number[];
  values: string[];
}

export interface EffectConfig {
  effect_name: string;
  start: number;
  end: number;
  intensity?: number;
}

export interface StickerConfig {
  sticker_url: string;
  start: number;
  end: number;
  position_x?: number;
  position_y?: number;
  scale?: number;
  rotation?: number;
}

export interface ApiResponse<T> {
  success: boolean;
  result?: T;
  error?: string;
}

export interface MediaDuration {
  duration: number;
  format: string;
  width?: number;
  height?: number;
}

export enum ResponseFormat {
  MARKDOWN = 'markdown',
  JSON = 'json'
}
