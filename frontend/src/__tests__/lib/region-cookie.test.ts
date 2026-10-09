// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect, beforeEach } from "vitest";
import { getRegionCookie, setRegionCookie, clearRegionCookie } from "@/lib/region-cookie";

describe("region-cookie", () => {
  beforeEach(() => {
    document.cookie.split(";").forEach((c) => {
      const eq = c.indexOf("=");
      const name = eq > -1 ? c.substring(0, eq).trim() : c.trim();
      document.cookie = `${name}=; path=/; max-age=0`;
    });
  });

  it("returns null when cookie is absent", () => {
    expect(getRegionCookie()).toBeNull();
  });

  it("writes and reads a valid region id", () => {
    setRegionCookie("cn-1");
    expect(getRegionCookie()).toBe("cn-1");
  });

  it("clearRegionCookie removes the cookie", () => {
    setRegionCookie("cn-1");
    clearRegionCookie();
    expect(getRegionCookie()).toBeNull();
  });

  it("rejects region ids that don't match the regex", () => {
    expect(() => setRegionCookie("BAD ID")).toThrow();
  });

  it("getRegionCookie returns null for a tampered value that fails the regex", () => {
    document.cookie = "server-region=Evil_Value; path=/";
    expect(getRegionCookie()).toBeNull();
  });

  it("returns null for a malformed percent-encoded cookie value", () => {
    document.cookie = "server-region=%E0%A4; path=/";
    expect(getRegionCookie()).toBeNull();
  });
});
