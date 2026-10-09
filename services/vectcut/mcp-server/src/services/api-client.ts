// API client for CapCut server communication

import axios, { AxiosError, AxiosInstance, AxiosRequestConfig } from 'axios';
import { API_BASE_URL } from '../constants.js';
import type { ApiResponse } from '../types.js';

export class CapCutApiClient {
  private client: AxiosInstance;

  constructor(baseURL: string = API_BASE_URL) {
    this.client = axios.create({
      baseURL,
      timeout: 60000, // 60 seconds timeout
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Add response interceptor for error handling
    this.client.interceptors.response.use(
      (response) => response,
      (error: AxiosError) => {
        if (error.response) {
          // Server responded with error status
          const status = error.response.status;
          const data = error.response.data as any;
          
          if (status === 404) {
            throw new Error(`Resource not found: ${error.config?.url}`);
          } else if (status === 400) {
            throw new Error(`Bad request: ${data?.error || error.message}`);
          } else if (status === 500) {
            throw new Error(`Server error: ${data?.error || 'Internal server error'}`);
          } else if (status === 429) {
            throw new Error('Rate limit exceeded. Please try again later.');
          }
          
          throw new Error(data?.error || `API error (${status})`);
        } else if (error.request) {
          // Request made but no response
          throw new Error('CapCut API server is not responding. Please ensure the server is running.');
        } else {
          // Error setting up request
          throw new Error(`Request error: ${error.message}`);
        }
      }
    );
  }

  async request<T>(
    endpoint: string,
    method: 'GET' | 'POST' | 'PUT' | 'DELETE' = 'POST',
    data?: any,
    params?: Record<string, any>
  ): Promise<ApiResponse<T>> {
    try {
      const config: AxiosRequestConfig = {
        method,
        url: endpoint,
        ...(data && { data }),
        ...(params && { params }),
      };

      const response = await this.client.request<any>(config);
      const resData = response.data;
      if (resData && resData.output !== undefined && resData.result === undefined) {
        resData.result = resData.output;
      }
      return resData;
    } catch (error) {
      if (error instanceof Error) {
        return {
          success: false,
          error: error.message
        };
      }
      return {
        success: false,
        error: 'Unknown error occurred'
      };
    }
  }

  // Specific API methods
  async createDraft(config: { width: number; height: number; fps?: number; ratio?: string }) {
    const res = await this.request<any>('/create_draft', 'POST', config);
    if (res.success && res.result && typeof res.result === 'object') {
      res.result.width = config.width;
      res.result.height = config.height;
      res.result.fps = config.fps || 30;
      res.result.ratio = config.ratio || (config.height > config.width ? '9:16' : '16:9');
      res.result.duration = 0;
      res.result.created_at = new Date().toISOString();
    }
    return res;
  }

  async addVideo(data: any) {
    return this.request('/add_video', 'POST', data);
  }

  async addAudio(data: any) {
    return this.request('/add_audio', 'POST', data);
  }

  async addText(data: any) {
    const payload = { ...data };
    if (payload.position_x !== undefined && payload.transform_x === undefined) {
      payload.transform_x = (payload.position_x - 0.5) * 2;
    }
    if (payload.position_y !== undefined && payload.transform_y === undefined) {
      payload.transform_y = (payload.position_y - 0.5) * 2;
    }
    if (payload.animation && !payload.intro_animation) {
      payload.intro_animation = payload.animation;
    }
    return this.request('/add_text', 'POST', payload);
  }

  async addImage(data: any) {
    const payload = { ...data };
    if (payload.position_x !== undefined && payload.transform_x === undefined) {
      payload.transform_x = (payload.position_x - 0.5) * 2;
    }
    if (payload.position_y !== undefined && payload.transform_y === undefined) {
      payload.transform_y = (payload.position_y - 0.5) * 2;
    }
    if (payload.scale !== undefined && payload.scale_x === undefined) {
      payload.scale_x = payload.scale;
      payload.scale_y = payload.scale;
    }
    if (payload.animation && !payload.intro_animation) {
      payload.intro_animation = payload.animation;
    }
    return this.request('/add_image', 'POST', payload);
  }

  async addSubtitle(data: any) {
    const payload = { ...data };
    if (payload.srt_content && !payload.srt) {
      payload.srt = payload.srt_content;
    }
    return this.request('/add_subtitle', 'POST', payload);
  }

  async addKeyframe(data: any) {
    return this.request('/add_keyframe', 'POST', data);
  }

  async addEffect(data: any) {
    const payload = { ...data };
    if (payload.effect_name && !payload.effect_type) {
      payload.effect_type = payload.effect_name;
    }
    return this.request('/add_effect', 'POST', payload);
  }

  async addSticker(data: any) {
    const payload = { ...data };
    if (payload.sticker_url && !payload.sticker_id) {
      payload.sticker_id = payload.sticker_url;
    }
    return this.request('/add_sticker', 'POST', payload);
  }

  async saveDraft(draftId: string, projectName?: string, autoDeploy: boolean = true, autoReload: boolean = false) {
    return this.request('/save_draft', 'POST', {
      draft_id: draftId,
      project_name: projectName,
      auto_deploy: autoDeploy,
      auto_reload: autoReload
    });
  }

  async listProjects(filter?: string) {
    return this.request('/list_projects', 'POST', { filter });
  }

  async readProject(projectName: string) {
    return this.request('/read_project', 'POST', { project_name: projectName });
  }

  async reloadDesktop(projectName?: string) {
    return this.request('/reload_desktop', 'POST', { project_name: projectName });
  }

  async getDuration(url: string) {
    return this.request('/get_duration', 'POST', { url, video_url: url });
  }
}

// Singleton instance
export const apiClient = new CapCutApiClient();
