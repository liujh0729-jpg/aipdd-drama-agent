// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface TTSVoice {
  name: string;
  short_name: string;
  gender: string;
  locale: string;
}

export interface GridImage {
  cell_url?: string;
  grid_url?: string;
  stale?: boolean;
}
