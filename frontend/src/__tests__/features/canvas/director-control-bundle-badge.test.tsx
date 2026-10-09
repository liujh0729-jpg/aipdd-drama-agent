// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  DirectorControlBundleBadge,
  hasDirectorControlBundle,
} from "@/features/canvas/ui/DirectorControlBundleBadge";

describe("DirectorControlBundleBadge", () => {
  it("detects only complete director control bundle records", () => {
    expect(hasDirectorControlBundle({
      schema_version: "director_control_bundle_v1",
      rel_paths: {
        combined: "director_control_frames/ep001/beat_01/combined.png",
        env_only: "director_control_frames/ep001/beat_01/env_only.png",
        frame_meta: "director_control_frames/ep001/beat_01/frame_meta.json",
      },
    })).toBe(true);

    expect(hasDirectorControlBundle({
      schema_version: "director_control_bundle_v1",
      rel_paths: {
        combined: "director_control_frames/ep001/beat_01/combined.png",
      },
    })).toBe(false);
    expect(hasDirectorControlBundle(null)).toBe(false);
  });

  it("renders a compact director bundle badge", () => {
    render(
      <DirectorControlBundleBadge
        bundle={{
          schema_version: "director_control_bundle_v1",
          rel_paths: {
            combined: "director_control_frames/ep001/beat_01/combined.png",
            env_only: "director_control_frames/ep001/beat_01/env_only.png",
            frame_meta: "director_control_frames/ep001/beat_01/frame_meta.json",
          },
        }}
      />,
    );

    expect(screen.getByText("导演合成")).toBeInTheDocument();
  });

  it("does not render for ordinary images", () => {
    const { container } = render(<DirectorControlBundleBadge bundle={null} />);

    expect(container).toBeEmptyDOMElement();
  });
});
