// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface PropAsset {
  name: string;
  aliases?: string[];
  prop_type?: string;
  visual_prompt?: string;
  description?: string;
  owner?: string;
  notes?: string;
  reference_path?: string | null;
  reference_url?: string | null;
}

