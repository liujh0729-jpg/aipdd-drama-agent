// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ModelProviderDefinition } from '../types';

// OpenAI — gpt-image-1 / gpt-image-2. The only provider with native mask edit.
// SuperTale `_image_provider_config(provider="openai")` reads OPENAI_API_KEY.
export const provider: ModelProviderDefinition = {
  id: 'openai',
  name: 'OpenAI',
  label: 'OpenAI',
};
