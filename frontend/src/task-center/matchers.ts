// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { TaskState } from "./types";

export const matchByType = (type: string) => (t: TaskState) => t.task_type === type;

export const matchByEpisode = (project: string, episode: number) => (t: TaskState) =>
  t.project === project && t.episode === episode;

export const matchBeat = (project: string, episode: number, beat: number) => (t: TaskState) =>
  t.project === project && t.episode === episode && t.beat_num === beat;
