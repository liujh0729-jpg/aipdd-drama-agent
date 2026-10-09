// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SaveStatus } from "@/components/save-status";
import { useSaveStatusStore } from "@/stores/save-status-store";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, vars?: Record<string, string>) => {
      if (vars?.t) return `${key}:${vars.t}`;
      return key;
    },
  }),
}));

beforeEach(() => {
  useSaveStatusStore.setState({ scopes: {} });
  vi.useRealTimers();
});

describe("<SaveStatus />", () => {
  it("renders nothing while idle", () => {
    const { container } = render(<SaveStatus scope="a" />);
    expect(container.firstChild).toBeNull();
  });

  it("renders saving with role=status aria-live=polite on header variant", () => {
    useSaveStatusStore.getState().setScopeStatus("a", "saving");
    render(<SaveStatus scope="a" variant="header" />);
    const el = screen.getByRole("status");
    expect(el).toHaveAttribute("aria-live", "polite");
    expect(el).toHaveAttribute("aria-atomic", "true");
    expect(el.textContent).toContain("common.saveStatus.saving");
  });

  it("inline variant has NO aria-live (regression: a11y chatter)", () => {
    // Only the page-level header should announce. Inline chips are visual-only
    // to avoid duplicate screen-reader announcements per blur.
    useSaveStatusStore.getState().setScopeStatus("a", "saving");
    const { container } = render(<SaveStatus scope="a" variant="inline" />);
    expect(container.querySelector("[aria-live]")).toBeNull();
    expect(container.querySelector("[role='status']")).toBeNull();
  });

  it("renders fresh saved state with 'saved' label", () => {
    useSaveStatusStore.getState().setScopeStatus("a", "saved");
    render(<SaveStatus scope="a" />);
    expect(screen.getByRole("status").textContent).toContain(
      "common.saveStatus.saved",
    );
  });

  it("crosses the 10s boundary via a one-shot timeout", async () => {
    vi.useFakeTimers();
    const when = Date.now() - 9_000;
    useSaveStatusStore.setState({
      scopes: { a: { status: "saved", lastSavedAt: when } },
    });
    render(<SaveStatus scope="a" />);
    expect(screen.getByRole("status").textContent).toContain(
      "common.saveStatus.saved",
    );
    // Advance past the 10s freshness boundary — one-shot timer should fire.
    await act(async () => {
      vi.advanceTimersByTime(2_000);
    });
    expect(screen.getByRole("status").textContent).toContain(
      "common.saveStatus.savedAgo",
    );
  });

  it("renders savedAgo immediately when mounted past the 10s boundary", () => {
    const tenMinAgo = Date.now() - 10 * 60_000;
    useSaveStatusStore.setState({
      scopes: { a: { status: "saved", lastSavedAt: tenMinAgo } },
    });
    render(<SaveStatus scope="a" />);
    expect(screen.getByRole("status").textContent).toContain(
      "common.saveStatus.savedAgo",
    );
  });

  it("renders error with role=alert on header, fires onRetry", async () => {
    const onRetry = vi.fn();
    useSaveStatusStore.getState().setScopeStatus("a", "error", "boom");
    render(<SaveStatus scope="a" onRetry={onRetry} />);
    const el = screen.getByRole("alert");
    expect(el).toHaveAttribute("aria-live", "assertive");
    await userEvent.click(screen.getByText("common.saveStatus.retry"));
    expect(onRetry).toHaveBeenCalled();
  });

  it("updates when leaf state changes", () => {
    render(<SaveStatus scope="a" />);
    expect(screen.queryByRole("status")).toBeNull();
    act(() => {
      useSaveStatusStore.getState().setScopeStatus("a", "saving");
    });
    expect(screen.getByRole("status").textContent).toContain(
      "common.saveStatus.saving",
    );
  });

  it("reflects derived parent state when subscribed to a parent scope", () => {
    useSaveStatusStore.setState({
      scopes: {
        "p.a": { status: "error", lastSavedAt: null, error: "A" },
        "p.b": { status: "saved", lastSavedAt: Date.now() },
      },
    });
    render(<SaveStatus scope="p" variant="header" />);
    // Parent should show error (child A), not saved (child B).
    expect(screen.getByRole("alert").textContent).toContain(
      "common.saveStatus.error",
    );
  });
});
