// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import i18next from "i18next";
import { initReactI18next } from "react-i18next";
import { HeaderEntry } from "@/components/task-center/header-entry";
import { useAppStore } from "@/stores/app-store";
import { useTaskCenterStore } from "@/task-center/store";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";

const i18n = i18next.createInstance();

beforeAll(async () => {
  await i18n.use(initReactI18next).init({
    lng: "en",
    fallbackLng: "en",
    resources: {
      en: { translation: { nav: { taskCenter: "Task Center" } } },
    },
    interpolation: { escapeValue: false },
  });
});

function renderEntry() {
  return render(
    <I18nextProvider i18n={i18n}>
      <HeaderEntry />
    </I18nextProvider>,
  );
}

beforeEach(() => {
  useAppStore.setState({ taskPanelOpen: false });
  useTaskCenterStore.getState().reset();
});

describe("HeaderEntry", () => {
  it("renders button with aria-label 'Task Center'", () => {
    renderEntry();
    expect(screen.getByRole("button", { name: /task center/i })).toBeInTheDocument();
  });

  it("badge hidden when zero running tasks", () => {
    renderEntry();
    const btn = screen.getByRole("button");
    expect(btn.textContent).not.toMatch(/\d/);
  });

  it("badge shows running count", () => {
    useTaskCenterStore.getState().hydrate([
      sampleTask({ task_key: "a", status: "running" }),
      sampleTask({ task_key: "b", status: "running" }),
      sampleTask({ task_key: "c", status: "running" }),
    ]);
    renderEntry();
    expect(screen.getByRole("button").textContent).toMatch(/3/);
  });

  it("badge caps at 9+ when count exceeds 9", () => {
    const tasks = Array.from({ length: 12 }, (_, i) =>
      sampleTask({ task_key: `t${i}`, status: "running" }),
    );
    useTaskCenterStore.getState().hydrate(tasks);
    renderEntry();
    expect(screen.getByRole("button").textContent).toContain("9+");
  });

  it("click toggles taskPanelOpen", () => {
    renderEntry();
    fireEvent.click(screen.getByRole("button"));
    expect(useAppStore.getState().taskPanelOpen).toBe(true);
    fireEvent.click(screen.getByRole("button"));
    expect(useAppStore.getState().taskPanelOpen).toBe(false);
  });

  it("aria-pressed reflects panel state", () => {
    useAppStore.setState({ taskPanelOpen: true });
    renderEntry();
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true");
  });
});
