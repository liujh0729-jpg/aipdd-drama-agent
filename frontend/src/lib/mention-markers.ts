// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export interface MentionMarkerOptions {
  identities?: string[];
  props?: string[];
}

function uniqueNonEmpty(values: string[] | undefined): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const raw of values ?? []) {
    const value = raw.trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Build a global regex that matches `@<label>` against the *known* candidate
// dictionary — longest label first (so e.g. "青桐_少女时期" wins over
// "青桐_少女"), with the label captured in group 1. Matching is purely
// dictionary-driven: only `@` immediately followed by a known label matches, so
// `a@b.com` stays literal without needing a boundary guard. Crucially there is
// NO look-behind on the char before `@`, so adjacent mentions resolve even when
// labels end in digits — `@图片1@图片2` yields both. Because we never "grab up
// to the next space", a trailing space is never required:
// `@青桐_少女时期哈哈哈` resolves to `青桐_少女时期` + `哈哈哈`. Returns null when
// there are no labels. Shared by the highlight layer (mention-textarea) and the
// parse layer so both stay in lock-step (WYSIWYG).
export function buildMentionRegex(labels: string[]): RegExp | null {
  const valid = uniqueNonEmpty(labels);
  if (valid.length === 0) return null;
  const sorted = [...valid].sort((a, b) => b.length - a.length);
  const alternation = sorted.map(escapeRegExp).join("|");
  return new RegExp(`@(${alternation})`, "gu");
}

export function normalizeMentionSeparatorSpaces(
  text: string,
  labels: string[],
  caret = text.length,
): { text: string; caret: number } {
  const pattern = buildMentionRegex(labels);
  if (!pattern) return { text, caret };

  let normalized = "";
  let lastIndex = 0;
  let nextCaret = caret;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    const end = match.index + match[0].length;
    normalized += text.slice(lastIndex, end);
    const nextChar = text[end];
    if (nextChar && !/\s/u.test(nextChar)) {
      normalized += " ";
      if (end <= caret) nextCaret += 1;
    }
    lastIndex = end;
  }
  normalized += text.slice(lastIndex);
  return { text: normalized, caret: Math.min(nextCaret, normalized.length) };
}

export function mentionsToProgramMarkers(
  text: string,
  options: MentionMarkerOptions,
): string {
  const identitySet = new Set(uniqueNonEmpty(options.identities));
  const propSet = new Set(uniqueNonEmpty(options.props));
  if (identitySet.size === 0 && propSet.size === 0) return text;

  const pattern = buildMentionRegex([...identitySet, ...propSet]);
  if (!pattern) return text;

  return text.replace(pattern, (match, token: string) => {
    if (identitySet.has(token)) return `{{${token}}}`;
    if (propSet.has(token)) return `[[${token}]]`;
    return match;
  });
}

export function programMarkersToMentions(text: string): string {
  return text
    .replace(/\{\{([^}]+)\}\}/g, (_match, token: string) => `@${token.trim()}`)
    .replace(/\[\[([^\]]+)\]\]/g, (_match, token: string) => `@${token.trim()}`);
}

export function extractIdentityMarkers(text: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const match of text.matchAll(/\{\{([^}]+)\}\}/g)) {
    const value = match[1]?.trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}

export function extractPropMarkers(text: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const match of text.matchAll(/\[\[([^\]]+)\]\]/g)) {
    const value = match[1]?.trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    out.push(value);
  }
  return out;
}
