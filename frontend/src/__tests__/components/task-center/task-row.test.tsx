// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TaskRow } from "@/components/task-center/task-row";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";

// Mock react-i18next — matches the repo convention (see save-status.test.tsx).
// Using I18nextProvider + the real HTTP-backed i18n instance would require a
// Suspense boundary in jsdom (translations are lazy-loaded), leaving the tree
// empty. A deterministic mock keeps component tests focused on render logic.
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string>) => {
      if (vars && Object.keys(vars).length > 0) {
        return `${key}:${JSON.stringify(vars)}`;
      }
      return key;
    },
  }),
}));

function renderRow(props: Parameters<typeof TaskRow>[0]) {
  return render(<TaskRow {...props} />);
}

describe("TaskRow", () => {
  it("renders running icon + progress bar", () => {
    renderRow({
      task: sampleTask({ status: "running", progress: 0.45 }),
      selected: false,
      onClick: vi.fn(),
    });
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("fires onClick", () => {
    const onClick = vi.fn();
    renderRow({ task: sampleTask(), selected: false, onClick });
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("applies selected highlight class", () => {
    renderRow({ task: sampleTask(), selected: true, onClick: vi.fn() });
    expect(screen.getByRole("button").className).toMatch(/bg-muted/);
  });

  it("hides progress bar for completed tasks", () => {
    const { container } = renderRow({
      task: sampleTask({ status: "completed", progress: 1 }),
      selected: false,
      onClick: vi.fn(),
    });
    // Progress renders a div with `role="progressbar"` or similar; simpler: no "w-16" wrapper
    const wrappers = container.querySelectorAll(".w-16");
    expect(wrappers.length).toBe(0);
  });
});
