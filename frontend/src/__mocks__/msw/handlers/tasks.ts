// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { http, HttpResponse } from "msw";
import type { TaskState } from "@/task-center/types";

let _sampleTaskSeq = 0;
export const sampleTask = (overrides: Partial<TaskState> = {}): TaskState => ({
  task_key: "task:script_writer:alice:demo:1",
  // Each call gets a distinct task_id by default so tests exercising the
  // store's task_id-keyed Map don't accidentally collapse multiple tasks
  // into a single entry. Callers can still override explicitly.
  task_id: `uuid-${++_sampleTaskSeq}`,
  task_type: "script_writer",
  username: "alice",
  project: "demo",
  episode: 1,
  beat_num: null,
  scope: null,
  status: "running",
  progress: 0.5,
  current_task: "Writing beats...",
  result: null,
  error: null,
  logs: ["[14:32:51] start"],
  created_at: "2026-04-18T14:32:50Z",
  updated_at: "2026-04-18T14:33:12Z",
  completed_at: "",
  expires_at: null,
  ...overrides,
});

export const handlers = [
  http.get("/api/v1/projects/:project/tasks", () => {
    return HttpResponse.json({ ok: true, data: [sampleTask()] });
  }),
  // /api/v1/projects/:project/tasks/stream — SSE handler is defined per-test with server.use()
  // because MSW v2 SSE needs ReadableStream composition specific to each test case.
];
