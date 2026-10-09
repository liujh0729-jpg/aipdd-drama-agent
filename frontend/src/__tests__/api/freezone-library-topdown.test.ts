// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiCall } from "@/api/client";
import { submitFreezoneAddVideoCharacterLibraryItem } from "@/api/ops";

vi.mock("@/api/client", () => ({
  apiCall: vi.fn(),
  apiClient: {},
}));

describe("submitFreezoneAddVideoCharacterLibraryItem topdown scene", () => {
  beforeEach(() => {
    vi.mocked(apiCall).mockReset();
    vi.mocked(apiCall).mockResolvedValue({ id: "item-1" });
  });

  it("forwards the inline scene for a topdown draft", async () => {
    const scene = { version: 1, size: 1280, items: [] };
    await submitFreezoneAddVideoCharacterLibraryItem("project-1", {
      name: "客厅走位",
      media: "image",
      imageUrls: ["/static/topdown-preview.png"],
      category: "topdown",
      scene,
    });

    expect(apiCall).toHaveBeenCalledWith(
      "projects/project-1/freezone/video/character-library",
      expect.objectContaining({
        method: "POST",
        json: expect.objectContaining({
          category: "topdown",
          image_urls: ["/static/topdown-preview.png"],
          scene,
        }),
      }),
    );
  });

  it("omits scene from the body when the payload carries none", async () => {
    await submitFreezoneAddVideoCharacterLibraryItem("project-1", {
      name: "参考图",
      imageUrls: ["/static/a.png"],
    });

    const request = vi.mocked(apiCall).mock.calls[0]?.[1] as {
      json?: Record<string, unknown>;
    };
    expect(request.json).not.toHaveProperty("scene");
  });
});
