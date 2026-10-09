// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Search, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const CHARACTER_SEARCH_FIELDS = [
  "name",
  "aliases",
  "description",
  "role",
  "gender",
  "age_group",
  "body_type",
  "face_prompt",
] as const;

type SearchField = (typeof CHARACTER_SEARCH_FIELDS)[number];
type SearchableValue = string | readonly (string | null | undefined)[] | null | undefined;

export type SearchableCharacter = Partial<Record<SearchField, SearchableValue>>;

export function filterCharacters<T extends SearchableCharacter>(
  characters: readonly T[],
  query: string,
): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...characters];

  return characters.filter((character) =>
    CHARACTER_SEARCH_FIELDS.some((field) =>
      normalizeSearchValue(character[field]).includes(needle),
    ),
  );
}

function normalizeSearchValue(value: SearchableValue): string {
  if (typeof value === "string") {
    return value.toLowerCase();
  }
  if (Array.isArray(value)) {
    return value.filter(Boolean).join(" ").toLowerCase();
  }
  return "";
}

export function CharacterSearch({
  value,
  onValueChange,
  resultCount: _resultCount,
  totalCount: _totalCount,
  placeholder = "Search characters",
}: {
  value: string;
  onValueChange: (value: string) => void;
  resultCount: number;
  totalCount: number;
  placeholder?: string;
}) {
  return (
    <div className="relative min-w-[220px] flex-1">
      <Search
        aria-hidden="true"
        className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
      />
      <Input
        aria-label="Search characters"
        className="h-8 border-0 bg-transparent pl-8 pr-8 text-sm shadow-none focus-visible:border-0 focus-visible:ring-0 focus-visible:ring-offset-0 [appearance:none] [&::-webkit-search-cancel-button]:hidden [&::-webkit-search-decoration]:hidden"
        placeholder={placeholder}
        type="search"
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
      />
      {value ? (
        <Button
          aria-label="Clear character search"
          className="absolute right-1 top-1/2 size-6 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          size="icon-xs"
          title="Clear character search"
          type="button"
          variant="ghost"
          onClick={() => onValueChange("")}
        >
          <X className="size-3" />
        </Button>
      ) : null}
    </div>
  );
}
