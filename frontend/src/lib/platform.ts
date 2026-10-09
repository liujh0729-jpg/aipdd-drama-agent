// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab

// Single source of truth for platform detection. `navigator.platform` is the
// most reliable signal for desktop macOS; the userAgent fallback covers iPad
// (which reports a Mac-like platform on iPadOS 13+) and older engines that
// leave `platform` empty.
export const IS_MAC =
  typeof navigator !== 'undefined' &&
  (/mac/i.test(navigator.platform) || /mac|iphone|ipad|ipod/i.test(navigator.userAgent));

// Map the Mac keycap glyphs shown in shortcut hints to the label appropriate
// for the current platform. On macOS the glyphs are kept as-is; on Windows /
// Linux they become the words users actually press (⌘ → Ctrl, ⌥ → Alt).
const NON_MAC_KEY_LABELS: Record<string, string> = {
  '⌘': 'Ctrl',
  '⌥': 'Alt',
  '⇧': 'Shift',
};

/** Command/Ctrl label for a single-key hint (⌘ on Mac, "Ctrl" elsewhere). */
export const MOD_KEY_LABEL = IS_MAC ? '⌘' : 'Ctrl';

/** Localize a keycap glyph for the current platform. */
export function platformKeyLabel(token: string): string {
  if (IS_MAC) return token;
  return NON_MAC_KEY_LABELS[token] ?? token;
}
