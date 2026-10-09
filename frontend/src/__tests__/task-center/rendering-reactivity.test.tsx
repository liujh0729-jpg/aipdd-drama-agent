// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, beforeAll } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { TaskStatusBar } from "@/components/task-center/status-bar";
import { HeaderEntry } from "@/components/task-center/header-entry";
import { useTaskCenterStore } from "@/task-center/store";
import { useAppStore } from "@/stores/app-store";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";

/**
 * Rendered-DOM reactivity suite: regression guard for the bug where the store
 * mutated the tasks Map in place and passed the same reference back to zustand,
 * which made `Object.is` equality bail out and blocked every subscriber from
 * re-rendering. The ER-INTEGRATION multi-review flagged this as a P0 correctness
 * bug — fixed in the store by switching to immutable Map updates.
 *
 * These tests intentionally assert on the DOM output, not on store state, so
 * they'd catch a regression in the subscription behavior even if the store's
 * data is correct.
 */

const i18n = i18next.createInstance();

beforeAll(async () => {
  if (!i18n.isInitialized) {
    await i18n.use(initReactI18next).init({
      lng: "en",
      fallbackLng: "en",
      resources: {
        en: {
          translation: {
            nav: { taskCenter: "Task Center" },
            taskCenter: {
              title: "Task Center",
              statusBar: {
                idle: "Idle",
                connecting: "Connecting",
                connected: "Connected",
                reconnecting: "Reconnecting",
                polling: "Polling",
                offline: "Offline",
                running: "{{count}} running",
              },
              relative: { justNow: "just now" },
            },
            tasks: {
              types: { script_writer: "Script writer", sketch_regen: "Sketch regen" },
            },
          },
        },
      },
      interpolation: { escapeValue: false },
    });
  }
});

function renderStatusBar() {
  return render(
    <I18nextProvider i18n={i18n}>
      <TaskStatusBar />
    </I18nextProvider>,
  );
}

function renderHeaderEntry() {
  return render(
    <I18nextProvider i18n={i18n}>
      <HeaderEntry />
    </I18nextProvider>,
  );
}

beforeEach(() => {
  useTaskCenterStore.getState().reset();
  useAppStore.setState({ taskPanelOpen: false });
});

describe("rendered-DOM reactivity — status bar", () => {
  it("re-renders running count when a task is upserted", () => {
    renderStatusBar();
    // Initial: 0 running — count chip hidden; idle visible
    expect(screen.getByText(/idle/i)).toBeInTheDocument();

    act(() => {
      useTaskCenterStore.getState().upsert(
        sampleTask({ task_key: "a", status: "running", progress: 0 }),
      );
    });

    // Now the running-count chip must render and show "1"
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("re-renders leading-task label when tasks change", () => {
    // Progress % was removed from the status bar (noise under discrete
    // updates); we now assert the running-task label is visible.
    renderStatusBar();
    act(() => {
      useTaskCenterStore.getState().upsert(
        sampleTask({ task_id: "a", task_key: "a", task_type: "script_writer", status: "running" }),
      );
    });
    expect(screen.getAllByText(/script writer|ep\d|剧本/i).length).toBeGreaterThan(0);
  });

  it("transitions from running-chip to completion chip when task finishes", () => {
    const completedAt = new Date().toISOString();
    act(() => {
      useTaskCenterStore.getState().upsert(
        sampleTask({ task_id: "a", task_key: "a", status: "running", progress: 0.5 }),
      );
    });
    renderStatusBar();
    // Running: no ✓ chip yet
    expect(screen.queryByText(/✓/)).not.toBeInTheDocument();

    act(() => {
      useTaskCenterStore.getState().upsert(
        sampleTask({ task_id: "a", task_key: "a", status: "completed", completed_at: completedAt }),
      );
    });
    // After completion the "✓ label · Xm ago" chip renders.
    expect(screen.getByText(/✓/)).toBeInTheDocument();
  });

  it("re-renders stream health indicator when setHealth is called", () => {
    renderStatusBar();
    // Initial health is "connecting"; both sr-only and visible label exist
    expect(screen.getAllByText(/connecting/i).length).toBeGreaterThan(0);

    act(() => {
      useTaskCenterStore.getState().setHealth("reconnecting");
    });
    expect(screen.getAllByText(/reconnecting/i).length).toBeGreaterThan(0);

    act(() => {
      useTaskCenterStore.getState().setHealth("polling");
    });
    expect(screen.getAllByText(/polling/i).length).toBeGreaterThan(0);
  });
});

describe("rendered-DOM reactivity — header entry badge", () => {
  it("shows badge when running count grows from 0 to N and back", () => {
    renderHeaderEntry();
    // Initial: no badge
    const btn = screen.getByRole("button", { name: /task center/i });
    expect(btn.textContent).not.toMatch(/\d/);

    act(() => {
      useTaskCenterStore.getState().upsert(
        sampleTask({ task_id: "a", task_key: "a", status: "running" }),
      );
      useTaskCenterStore.getState().upsert(
        sampleTask({ task_id: "b", task_key: "b", status: "running" }),
      );
    });
    expect(btn.textContent).toMatch(/2/);

    // Finish both tasks → badge hidden again. Must reuse the same task_id
    // so the upsert overwrites the running entry rather than appending a
    // new one (store keys by task_id now).
    act(() => {
      useTaskCenterStore
        .getState()
        .upsert(sampleTask({ task_id: "a", task_key: "a", status: "completed", completed_at: new Date().toISOString() }));
      useTaskCenterStore
        .getState()
        .upsert(sampleTask({ task_id: "b", task_key: "b", status: "completed", completed_at: new Date().toISOString() }));
    });
    expect(btn.textContent).not.toMatch(/\d/);
  });
});
