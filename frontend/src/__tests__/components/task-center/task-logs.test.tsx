// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

// Match repo convention: mock react-i18next to return the key verbatim so
// assertions stay stable without loading translation files over HTTP in jsdom.
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

import { TaskLogs } from "@/components/task-center/task-logs";
import { sampleTask } from "@/__mocks__/msw/handlers/tasks";

function renderLogs(task: Parameters<typeof TaskLogs>[0]["task"]) {
  return render(<TaskLogs task={task} />);
}

describe("TaskLogs", () => {
  it("renders placeholder when logs empty", () => {
    renderLogs(sampleTask({ logs: [] }));
    // With the i18n mock the key is rendered verbatim — verify the placeholder
    // key is on screen so localized text can swap in later without test churn.
    expect(
      screen.getByText("taskCenter.detail.logs.placeholder"),
    ).toBeInTheDocument();
  });

  it("renders log lines joined by newline", () => {
    renderLogs(
      sampleTask({ logs: ["[14:32:51] start", "[14:33:01] step 1", "[14:33:12] done"] }),
    );
    const pre = screen.getByText(/\[14:32:51\] start/, { selector: "pre" });
    expect(pre.textContent).toContain("start");
    expect(pre.textContent).toContain("step 1");
    expect(pre.textContent).toContain("done");
  });

  it("uses monospace styling", () => {
    const { container } = renderLogs(sampleTask({ logs: ["line"] }));
    const pre = container.querySelector("pre");
    expect(pre?.className).toMatch(/font-mono/);
  });
});
