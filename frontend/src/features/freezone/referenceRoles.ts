// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
/**
 * Reference image role tagging (v1.6ζ).
 *
 * The base GenNode connects N reference images as a flat array. SuperTale's `grid_generation` likewise
 * doesn't natively distinguish "use this as the character anchor" vs "use
 * this as the style reference" — but those have very different effects on
 * how the model uses the image.
 *
 * Equivalent of MJ's `--oref` (object/character reference) and `--sref`
 * (style reference): we let users tag references inline in the prompt:
 *
 *   ```
 *   a portrait of the protagonist
 *
 *   [ref:1=character]
 *   [ref:2=style]
 *   [ref:3=pose]
 *   ```
 *
 * `parseReferenceRoles` strips those markers and returns:
 *   - `cleaned` prompt without the marker block
 *   - `roles` map keyed by 1-based reference index
 *
 * Then `renderReferenceRolesForPrompt` appends a single human-readable line
 * the model can use ("reference 1 = character anchor, ..."). The frontend
 * also reorders references so character anchors come first (most providers
 * weight earlier references higher).
 */

export type ReferenceRole = "character" | "style" | "pose" | "generic";

const VALID_ROLES = new Set<ReferenceRole>([
  "character",
  "style",
  "pose",
  "generic",
]);

const MARKER_RE = /\[ref:(\d+)=([a-z]+)\]/gi;

export interface ParsedRoles {
  /** Map from 1-based reference index → role. */
  roles: Map<number, ReferenceRole>;
  /** Prompt with all `[ref:n=role]` markers stripped. */
  cleaned: string;
}

export function parseReferenceRoles(prompt: string): ParsedRoles {
  const roles = new Map<number, ReferenceRole>();
  let match: RegExpExecArray | null;
  while ((match = MARKER_RE.exec(prompt)) !== null) {
    const idx = Number.parseInt(match[1], 10);
    const role = match[2].toLowerCase() as ReferenceRole;
    if (Number.isFinite(idx) && idx > 0 && VALID_ROLES.has(role)) {
      roles.set(idx, role);
    }
  }
  MARKER_RE.lastIndex = 0;
  const cleaned = prompt
    .replace(MARKER_RE, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return { roles, cleaned };
}

/**
 * Render a one-line role legend for the model. Empty when no roles tagged.
 */
export function renderReferenceRolesForPrompt(
  roles: Map<number, ReferenceRole>,
  refCount: number,
): string {
  if (roles.size === 0 || refCount === 0) return "";
  const labelMap: Record<ReferenceRole, string> = {
    character: "character anchor (preserve identity, pose, outfit)",
    style: "style reference (color palette, texture, lighting mood)",
    pose: "pose reference (body posture, gesture, framing)",
    generic: "additional reference",
  };
  const lines: string[] = [];
  for (let i = 1; i <= refCount; i++) {
    const role = roles.get(i) ?? "generic";
    lines.push(`- reference ${i}: ${labelMap[role]}`);
  }
  return `\n[reference roles]\n${lines.join("\n")}`;
}

/**
 * Reorder references so character anchors come first, then pose, then style,
 * then generic. Most providers (gpt-image-2, nanobanana, OpenRouter Gemini)
 * weight earlier references higher; pinning identity first usually wins.
 *
 * Returns reordered URLs + a remapped role table aligned to the new order.
 */
export function reorderReferencesByRole(
  refs: string[],
  roles: Map<number, ReferenceRole>,
): { reordered: string[]; rolesAfter: Map<number, ReferenceRole> } {
  if (refs.length === 0) return { reordered: refs, rolesAfter: new Map() };
  const ROLE_PRIORITY: ReferenceRole[] = ["character", "pose", "style", "generic"];
  const items = refs.map((url, i) => ({
    url,
    role: roles.get(i + 1) ?? "generic",
  }));
  items.sort(
    (a, b) =>
      ROLE_PRIORITY.indexOf(a.role) - ROLE_PRIORITY.indexOf(b.role),
  );
  const reordered = items.map((it) => it.url);
  const rolesAfter = new Map<number, ReferenceRole>();
  items.forEach((it, idx) => rolesAfter.set(idx + 1, it.role));
  return { reordered, rolesAfter };
}
