// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useCallback } from "react";

import type { AssetRefType } from "@/lib/queries/asset-references";

/**
 * URL-backed asset selection for the Assets page (`/projects/$project/characters`).
 * Reads `?type=identity|scene|prop&id=<assetId>` so a teammate can deep-link
 * straight to an asset and the link round-trips through copy/paste + refresh.
 *
 * `id` semantics by type: identity → `identity_id`, scene/prop → asset `name`.
 */

const ASSET_REF_TYPES = new Set<AssetRefType>(["identity", "scene", "prop"]);

export function parseAssetType(raw: unknown): AssetRefType | null {
  if (typeof raw !== "string") return null;
  return ASSET_REF_TYPES.has(raw as AssetRefType)
    ? (raw as AssetRefType)
    : null;
}

function parseAssetId(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  return trimmed.length > 0 ? trimmed : null;
}

/** Shareable absolute URL for one asset (used for copy-to-clipboard). */
export function buildAssetShareUrl(type: AssetRefType, id: string): string {
  const url = new URL(window.location.href);
  url.searchParams.set("type", type);
  url.searchParams.set("id", id);
  return url.toString();
}

/**
 * Navigate to the Assets page focused on one asset, from anywhere in the app
 * (e.g. the beats workbench). Reuses the `?type=&id=` deep-link so the target
 * tab auto-selects and the card scroll-highlights on arrival.
 */
export function useNavigateToAsset(project: string) {
  const navigate = useNavigate();
  return useCallback(
    (type: AssetRefType, id: string) => {
      navigate({
        to: "/projects/$project/characters",
        params: { project },
        search: { type, id } as never,
      });
    },
    [navigate, project],
  );
}

export interface AssetsDeepLink {
  type: AssetRefType | null;
  id: string | null;
  /** Write `?type=` (and optionally `?id=`); pass `null` id to drop it. */
  select: (type: AssetRefType, id?: string | null) => void;
}

export function useAssetsDeepLink(): AssetsDeepLink {
  const search = useSearch({ strict: false }) as Record<string, unknown>;
  const navigate = useNavigate();
  const type = parseAssetType(search.type);
  const id = parseAssetId(search.id);

  const select = useCallback(
    (nextType: AssetRefType, nextId?: string | null) => {
      navigate({
        search: ((prev: Record<string, unknown>) => {
          const next: Record<string, unknown> = { ...prev, type: nextType };
          if (nextId) next.id = nextId;
          else delete next.id;
          return next;
        }) as never,
        replace: true,
      });
    },
    [navigate],
  );

  return { type, id, select };
}
