// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CharacterSearch, filterCharacters } from "@/components/assets/character-search";

const characters = [
  {
    name: "Mira Vale",
    aliases: ["The Navigator", "Star Cartographer"],
    description: "Maps routes through old tunnels.",
    role: "Guide",
    gender: "Female",
    age_group: "adult",
    body_type: "lean",
    face_prompt: "freckled face, short black hair",
  },
  {
    name: "Jun",
    aliases: ["Ironhand"],
    description: "Keeps the workshop running.",
    role: "Engineer",
    gender: "Male",
    age_group: "elder",
    body_type: "stocky",
    face_prompt: "weathered face, silver beard",
  },
  {
    name: "Lio",
    aliases: [],
    description: "Quiet observer.",
    role: "Scout",
    gender: "Nonbinary",
    age_group: "child",
    body_type: "small",
    face_prompt: "round face, bright eyes",
  },
];

describe("filterCharacters", () => {
  it("returns every character for blank search text", () => {
    expect(filterCharacters(characters, "")).toEqual(characters);
    expect(filterCharacters(characters, "   ")).toEqual(characters);
  });

  it("matches supported character fields without case sensitivity", () => {
    expect(filterCharacters(characters, "mira").map((item) => item.name)).toEqual(["Mira Vale"]);
    expect(filterCharacters(characters, "NAVIGATOR").map((item) => item.name)).toEqual(["Mira Vale"]);
    expect(filterCharacters(characters, "workshop").map((item) => item.name)).toEqual(["Jun"]);
    expect(filterCharacters(characters, "engineer").map((item) => item.name)).toEqual(["Jun"]);
    expect(filterCharacters(characters, "nonbinary").map((item) => item.name)).toEqual(["Lio"]);
    expect(filterCharacters(characters, "child").map((item) => item.name)).toEqual(["Lio"]);
    expect(filterCharacters(characters, "stocky").map((item) => item.name)).toEqual(["Jun"]);
    expect(filterCharacters(characters, "freckled").map((item) => item.name)).toEqual(["Mira Vale"]);
  });
});

describe("CharacterSearch", () => {
  it("renders a compact controlled search box with counts", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();

    render(
      <CharacterSearch
        value="jun"
        onValueChange={onValueChange}
        resultCount={1}
        totalCount={3}
        placeholder="Search cast"
      />,
    );

    const input = screen.getByRole("searchbox", { name: "Search characters" });
    expect(input).toHaveValue("jun");
    expect(input).toHaveAttribute("placeholder", "Search cast");

    await user.type(input, "x");
    expect(onValueChange).toHaveBeenLastCalledWith("junx");
  });

  it("clears the search value from the icon button", async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();

    const { rerender } = render(
      <CharacterSearch
        value="guide"
        onValueChange={onValueChange}
        resultCount={1}
        totalCount={3}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Clear character search" }));
    expect(onValueChange).toHaveBeenCalledWith("");

    rerender(
      <CharacterSearch
        value=""
        onValueChange={onValueChange}
        resultCount={3}
        totalCount={3}
      />,
    );
    expect(screen.queryByRole("button", { name: "Clear character search" })).not.toBeInTheDocument();
  });
});
