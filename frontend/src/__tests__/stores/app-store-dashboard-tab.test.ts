// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach, vi } from "vitest";
import { useAppStore } from "@/stores/app-store";

describe("app-store: dashboardTab", () => {
  beforeEach(() => {
    window.localStorage.clear();
    useAppStore.setState({ dashboardTab: "active" });
  });

  it("defaults to 'active'", () => {
    expect(useAppStore.getState().dashboardTab).toBe("active");
  });

  it("setDashboardTab updates the value", () => {
    useAppStore.getState().setDashboardTab("archived");
    expect(useAppStore.getState().dashboardTab).toBe("archived");
    useAppStore.getState().setDashboardTab("deleted");
    expect(useAppStore.getState().dashboardTab).toBe("deleted");
    useAppStore.getState().setDashboardTab("active");
    expect(useAppStore.getState().dashboardTab).toBe("active");
  });

  it("persists to localStorage under 'supertale-app'", () => {
    useAppStore.getState().setDashboardTab("archived");
    const raw = window.localStorage.getItem("supertale-app");
    expect(raw).not.toBeNull();
    const parsed = JSON.parse(raw!);
    expect(parsed.state.dashboardTab).toBe("archived");
  });

  it("does not crash when localStorage quota is exceeded", () => {
    const setItem = vi
      .spyOn(window.localStorage, "setItem")
      .mockImplementation(() => {
        throw new DOMException("quota exceeded", "QuotaExceededError");
      });

    expect(() => useAppStore.getState().setDashboardTab("archived")).not.toThrow();
    expect(useAppStore.getState().dashboardTab).toBe("archived");

    setItem.mockRestore();
  });
});
