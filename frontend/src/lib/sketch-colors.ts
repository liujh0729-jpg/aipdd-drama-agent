// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Helpers for the `sketch_colors` map on the episode script.
 *
 * `sketch_colors` lives on the script JSON as `{ identity_id: "#HEX NAME" }`
 * (e.g. `"#FF00FF MAGENTA"`). identity_id is `<character>_<identity_name>`,
 * sanitized via `re.sub` on `[/\\:*?"<>|]` → `_` on the backend, so the split
 * prefers the longest known character-name prefix and falls back to first
 * underscore split.
 */

export function parseColorValue(value: string): { hex: string | null; name: string } {
  const match = value.trim().match(/^(#[0-9a-fA-F]{3,8})\s*(.*)$/);
  if (!match) return { hex: null, name: value.trim() };
  return { hex: match[1], name: match[2] };
}

export function splitIdentityId(
  identityId: string,
  knownNames: Set<string>,
): { character: string; identity: string } {
  let bestMatch: string | null = null;
  for (const name of knownNames) {
    if (identityId === name || identityId.startsWith(`${name}_`)) {
      if (!bestMatch || name.length > bestMatch.length) bestMatch = name;
    }
  }
  if (bestMatch) {
    const rest = identityId.slice(bestMatch.length);
    const identity = rest.startsWith("_") ? rest.slice(1) : rest;
    return { character: bestMatch, identity };
  }
  const idx = identityId.indexOf("_");
  if (idx < 0) return { character: identityId, identity: "" };
  return {
    character: identityId.slice(0, idx),
    identity: identityId.slice(idx + 1),
  };
}
