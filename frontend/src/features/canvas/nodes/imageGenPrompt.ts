// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export function hasImageGenPromptOverride(prompt: string): boolean {
  return prompt.trim().length > 0;
}
