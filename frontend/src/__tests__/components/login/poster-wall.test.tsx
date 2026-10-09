// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { PosterWall } from "@/components/login/poster-wall";
import type { Poster } from "@/types/login";

const posters: Poster[] = Array.from({ length: 36 }, (_, i) => ({
  id: String(i + 1).padStart(2, "0"),
  src_avif: `/login-posters/${String(i + 1).padStart(2, "0")}.avif`,
  src_webp: `/login-posters/${String(i + 1).padStart(2, "0")}.webp`,
  alt: "",
  dominant_hex: "#101010",
}));

describe("<PosterWall>", () => {
  it("renders exactly 6 columns of tiles", () => {
    const { container } = render(<PosterWall posters={posters} seed={42} />);
    const columns = container.querySelectorAll('[data-testid="poster-column"]');
    expect(columns.length).toBe(6);
  });

  it("marks the wall as aria-hidden (decorative)", () => {
    const { container } = render(<PosterWall posters={posters} seed={42} />);
    const wall = container.querySelector('[data-testid="poster-wall"]');
    expect(wall).toHaveAttribute("aria-hidden", "true");
  });

  it("each column track is duplicated (length multiple of 2)", () => {
    const { container } = render(<PosterWall posters={posters} seed={42} />);
    const tracks = container.querySelectorAll('[data-testid="poster-track"]');
    expect(tracks.length).toBe(6);
    for (const track of Array.from(tracks)) {
      expect(track.children.length % 2).toBe(0);
    }
  });
});
