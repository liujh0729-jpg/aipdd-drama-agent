// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NodeContextBadges } from "@/features/freezone/context/NodeContextBadges";

describe("NodeContextBadges", () => {
  it("does not repeat the primary context in the detail badge row", () => {
    render(
      <NodeContextBadges
        contexts={[
          {
            kind: "director_combined",
            projectId: "project-1",
            episode: 1,
            beat: 3,
            role: "director_combined",
          },
        ]}
      />,
    );

    expect(screen.getAllByText("导演合成图 · EP1/B3")).toHaveLength(1);
  });
});
