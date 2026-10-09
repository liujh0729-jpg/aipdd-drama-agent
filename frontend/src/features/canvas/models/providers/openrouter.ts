// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { ModelProviderDefinition } from '../types';

// OpenRouter — proxies multiple model families (Google Gemini, etc).
// SuperTale `_image_provider_config(provider="openrouter")` reads OPENROUTER_API_KEY.
export const provider: ModelProviderDefinition = {
  id: 'openrouter',
  name: 'OpenRouter',
  label: 'OpenRouter',
};
