// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * t4 回归：音频节点重新把「上传」当作主操作（仿图生图侧栏），与生成解耦。
 *
 * 锁两件事：
 *   1. 空态（无 audioUrl、未生成）在侧栏渲染「上传音频」按钮；
 *   2. 选中一个音频文件后走 uploadFreezoneImage，并把返回的 URL + 文件名写回节点。
 *
 * 整节点渲染依赖 xyflow 的 NodeToolbar/useStore（侧栏）、网络与 org 门控，这里
 * 用最小桩把它们隔离；被测的是 AudioNode 自身的上传接线（processFile + 隐藏 input）。
 */
import { act, fireEvent, render } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { CANVAS_NODE_TYPES, type AudioNodeData } from '@/features/canvas/domain/canvasNodes';
import { useCanvasStore } from '@/stores/canvasStore';

// ---- xyflow：侧栏用 NodeToolbar（渲染 children，绕开 autoHide 的可见性门控）
//      + useStore 读 nodeZ；Handle/useUpdateNodeInternals 给空实现即可。 ----
vi.mock('@xyflow/react', () => ({
  Handle: () => null,
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useUpdateNodeInternals: () => () => {},
  useStore: (selector: (state: unknown) => unknown) =>
    selector({ nodeLookup: new Map(), transform: [0, 0, 1] }),
  NodeResizeControl: ({ children }: { children?: ReactNode }) =>
    <div>{children}</div>,
  NodeToolbar: ({ children }: { children?: ReactNode }) =>
    <div data-testid="node-toolbar">{children}</div>,
}));

vi.mock('@/api/ops', () => ({
  uploadFreezoneImage: vi.fn(async () => ({ url: 'https://cdn/voice.mp3' })),
  fetchFreezoneAudioReferences: vi.fn(async () => ({ available: [] })),
  createFreezoneAudioVoice: vi.fn(async () => ({ voice_id: 'v1' })),
  submitFreezoneAudioSpeech: vi.fn(),
  submitFreezoneAudioMusic: vi.fn(),
  fetchFreezoneJobResult: vi.fn(),
  submitFreezoneTextTranslate: vi.fn(),
  fetchFreezoneTextTranslateResult: vi.fn(),
}));

vi.mock('@/api/tasks', () => ({
  awaitTaskCompletion: vi.fn(async () => undefined),
  isTaskPollTimeoutError: () => false,
}));

vi.mock('@/lib/model-task-access', () => ({
  useModelTaskAccess: () => ({ blocked: false, message: undefined }),
}));

vi.mock('@/lib/url-params', () => ({
  readUrl: () => ({ project: 'p1', canvas: 'default' }),
}));

vi.mock('@/features/canvas/hooks/useFreezoneAudioModels', () => ({
  readFreezoneAudioModels: () => [],
  useFreezoneAudioModels: () => ({ models: [] }),
}));

import { uploadFreezoneImage } from '@/api/ops';
import { AudioNode } from '@/features/canvas/nodes/AudioNode';

function seedNode(data: Partial<AudioNodeData>) {
  useCanvasStore.getState().setCanvasData(
    [
      {
        id: 'audio-1',
        type: CANVAS_NODE_TYPES.audio,
        position: { x: 0, y: 0 },
        data: { audioUrl: null, ...data },
      } as never,
    ],
    [],
  );
}

function renderAudioNode(data: Partial<AudioNodeData> = {}) {
  seedNode(data);
  return render(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    <AudioNode id="audio-1" data={{ audioUrl: null, ...data } as AudioNodeData} selected={false} {...({} as any)} />,
  );
}

describe('AudioNode 上传入口 (t4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useCanvasStore.getState().setCanvasData([], []);
    // NodeHeader / AudioWaveformPlayer 用到的浏览器 API，jsdom 缺省没有：
    // 同 lod-audio-playback.test 的最小桩，保证节点挂得起来（非本用例关注点）。
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('no network in test')));
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null);
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('空态在侧栏渲染「上传音频」按钮', () => {
    const { getByTitle, getByText } = renderAudioNode({ audioUrl: null });
    // 上传按钮 title=node.audio.uploadLocalTitle、文案=node.audio.uploadAudio。
    expect(getByTitle('上传本地音频')).toBeTruthy();
    expect(getByText('上传音频')).toBeTruthy();
  });

  it('选中音频文件后上传并写回节点 audioUrl + 文件名', async () => {
    const { container } = renderAudioNode({ audioUrl: null });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).not.toBeNull();

    const file = new File([new Uint8Array([1, 2, 3])], 'voice.mp3', { type: 'audio/mpeg' });
    await act(async () => {
      fireEvent.change(input, { target: { files: [file] } });
    });

    expect(uploadFreezoneImage).toHaveBeenCalledTimes(1);
    const node = useCanvasStore.getState().nodes.find((n) => n.id === 'audio-1');
    const stored = node?.data as AudioNodeData | undefined;
    expect(stored?.audioUrl).toBe('https://cdn/voice.mp3');
    expect(stored?.sourceFileName).toBe('voice.mp3');
  });

  it('拒绝非音频文件：不上传、不落库', async () => {
    const { container } = renderAudioNode({ audioUrl: null });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;

    const video = new File([new Uint8Array([1])], 'clip.mp4', { type: 'video/mp4' });
    await act(async () => {
      fireEvent.change(input, { target: { files: [video] } });
    });

    expect(uploadFreezoneImage).not.toHaveBeenCalled();
    const node = useCanvasStore.getState().nodes.find((n) => n.id === 'audio-1');
    const stored = node?.data as AudioNodeData | undefined;
    expect(stored?.audioUrl).toBeFalsy();
  });
});
