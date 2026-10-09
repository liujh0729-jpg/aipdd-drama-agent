// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, expect, it } from 'vitest';

import {
  normalizedSkillParameters,
  skillParameterEntries,
} from '@/features/canvas/nodes/skillNodeParameters';
import type { SkillDefinition } from '@/features/freezone/context/skillRoles';

const skill: SkillDefinition = {
  id: 'freezone.frame_from_context',
  provider: 'freezone_mainline',
  display_name: 'Frame From Context',
  description: '',
  capabilities: {},
  inputs: [],
  outputs: [],
  parameters: {
    quality: {
      type: 'enum',
      label: '质量',
      default: 'medium',
      options: ['low', 'medium', 'high'],
    },
    background_reference_mode: {
      type: 'enum',
      label: '背景参考模式',
      default: 'material_only',
      options: ['material_only', 'scene_anchor'],
    },
  },
};

describe('skill node parameters', () => {
  it('includes enum parameters with default values', () => {
    expect(normalizedSkillParameters(skill, {})).toEqual({
      quality: 'medium',
      background_reference_mode: 'material_only',
    });
  });

  it('preserves an explicit scene anchor mode', () => {
    expect(
      normalizedSkillParameters(skill, {
        background_reference_mode: 'scene_anchor',
      }),
    ).toMatchObject({
      background_reference_mode: 'scene_anchor',
    });
  });

  it('falls back to the default for unsupported modes', () => {
    expect(
      normalizedSkillParameters(skill, {
        background_reference_mode: 'unsupported',
      }),
    ).toMatchObject({
      background_reference_mode: 'material_only',
    });
  });

  it('keeps all enum entries renderable', () => {
    const entries = skillParameterEntries(skill, {});

    expect(entries).toEqual([
      expect.objectContaining({
        key: 'quality',
        type: 'enum',
        options: ['low', 'medium', 'high'],
        value: 'medium',
      }),
      expect.objectContaining({
        key: 'background_reference_mode',
        type: 'enum',
        options: ['material_only', 'scene_anchor'],
        value: 'material_only',
      }),
    ]);
  });
});
