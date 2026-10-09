// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, beforeAll, afterAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18next from "i18next";
import { TaskList } from "@/components/task-center/task-list";
import { useTaskCenterStore } from "@/task-center/store";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";

const i18n = i18next.createInstance();

// jsdom reports offsetWidth/offsetHeight of 0 for every element, which makes
// @tanstack/react-virtual's internal getRect() measure the scroll viewport as
// empty and emit zero virtual items. Patch the prototype descriptors so the
// virtualizer sees a realistic viewport and actually renders rows.
const originalOffsetWidth = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  "offsetWidth",
);
const originalOffsetHeight = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  "offsetHeight",
);
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get() {
      return 320;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get() {
      return 600;
    },
  });
});

afterAll(() => {
  if (originalOffsetWidth) {
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", originalOffsetWidth);
  }
  if (originalOffsetHeight) {
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", originalOffsetHeight);
  }
});

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    resources: {
      en: {
        translation: {
          taskCenter: {
            panel: {
              filters: { all: "All", running: "Running", failed: "Failed", done: "Done" },
              empty: "No tasks yet. Trigger a generation action to see them here.",
              emptyFiltered: "No {{filter}} tasks.",
            },
          },
          tasks: {
            types: {
              script_writer: "Script writer",
              sketch_regen: "Sketch regen",
            },
          },
        },
      },
    },
    interpolation: { escapeValue: false },
  });
});

function renderList() {
  return render(
    <I18nextProvider i18n={i18n}>
      <TaskList />
    </I18nextProvider>,
  );
}

beforeEach(() => {
  useTaskCenterStore.getState().reset();
});

describe("TaskList", () => {
  it("renders empty state when no tasks", () => {
    renderList();
    expect(screen.getByText(/no tasks yet/i)).toBeInTheDocument();
  });

  it("renders filter tabs", () => {
    renderList();
    expect(screen.getAllByRole("tab")).toHaveLength(4);
  });

  it("filter tab click updates store filter", () => {
    renderList();
    const failedTab = screen.getByRole("tab", { name: /failed/i });
    fireEvent.click(failedTab);
    expect(useTaskCenterStore.getState().filter).toBe("failed");
  });

  it("row click sets selectedTaskKey", () => {
    useTaskCenterStore
      .getState()
      .hydrate([sampleTask({ task_id: "task-id-a", task_key: "task-key-a", status: "running" })]);
    renderList();
    const buttons = screen.getAllByRole("button");
    const rowBtn = buttons.find((b) => b.getAttribute("role") !== "tab");
    expect(rowBtn).toBeDefined();
    fireEvent.click(rowBtn!);
    expect(useTaskCenterStore.getState().selectedTaskKey).toBe("task-key-a");
  });

  it("renders task rows for each task with the active filter", () => {
    useTaskCenterStore.getState().hydrate([
      sampleTask({ task_key: "a", status: "running" }),
      sampleTask({ task_key: "b", status: "completed" }),
    ]);
    renderList();
    const nonTabButtons = screen
      .getAllByRole("button")
      .filter((b) => b.getAttribute("role") !== "tab");
    expect(nonTabButtons.length).toBe(2);
  });
});
