// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Grid2X2, Wand2 } from "lucide-react";

import { useCharacters } from "@/lib/queries/characters";
import { useEpisodeBeats, useEpisodeDetail } from "@/lib/queries/episodes";
import { useScript } from "@/lib/queries/scripts";
import { parseColorValue, splitIdentityId } from "@/lib/sketch-colors";
import { Button } from "@/components/ui/button";

interface SketchStudioActionsProps {
  project: string;
  episode: number;
  onOpenGridGallery?: () => void;
  onOpenRenderGridGallery?: () => void;
  showGridGalleryActions?: boolean;
  showLegend?: boolean;
  showDetectionSummary?: boolean;
}

interface SketchColorLegendProps {
  project: string;
  episode: number;
}

export function SketchColorLegend({
  project,
  episode,
}: SketchColorLegendProps) {
  const { data: scriptRes } = useScript(project, episode);
  const { data: charsRes } = useCharacters(project);
  const { data: episodeRes } = useEpisodeDetail(project, episode);

  const sketchColors = scriptRes?.data?.sketch_colors ?? {};
  const characterNames = useMemo(
    () => new Set((charsRes?.data ?? []).map((c) => c.name)),
    [charsRes],
  );

  const entries = useMemo(() => {
    return Object.entries(sketchColors)
      .map(([identityId, value]) => {
        const { hex } = parseColorValue(value);
        const { character, identity } = splitIdentityId(
          identityId,
          characterNames,
        );
        return { identityId, hex, character, identity };
      })
      .filter((e) => !!e.hex)
      .sort((a, b) => a.identityId.localeCompare(b.identityId));
  }, [sketchColors, characterNames]);

  const propEntries = useMemo(() => {
    return (episodeRes?.data?.prop_menu ?? [])
      .map((prop) => {
        const { hex } = parseColorValue(prop.marker_color ?? "");
        return {
          propId: prop.prop_id,
          hex,
          description: prop.description ?? prop.visual_prompt ?? "",
        };
      })
      .filter((e) => !!e.hex)
      .sort((a, b) => a.propId.localeCompare(b.propId));
  }, [episodeRes]);

  if (entries.length === 0 && propEntries.length === 0) return null;

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-1.5 px-3 pb-3 pt-3 text-xs">
      {entries.map((e) => (
        <span
          key={e.identityId}
          className="inline-flex h-5 max-w-[180px] items-center gap-1 rounded-full border border-border/35 bg-foreground/[0.03] px-1.5 text-[11px] leading-none"
          title={`${e.character}${e.identity ? ` · ${e.identity}` : ""}`}
        >
          <span
            aria-hidden
            className="size-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: e.hex ?? undefined }}
          />
          <span className="truncate text-foreground/70">
            {e.character}
            {e.identity && (
              <>
                {" · "}
                <span className="text-muted-foreground/50">{e.identity}</span>
              </>
            )}
          </span>
        </span>
      ))}
      {propEntries.length > 0 && (
        <>
          {entries.length > 0 && (
            <span className="mx-0.5 h-3 w-px bg-border/40" aria-hidden />
          )}
          {propEntries.map((prop) => (
            <span
              key={prop.propId}
              className="inline-flex h-5 max-w-[160px] items-center gap-1 rounded-full border border-border/35 bg-foreground/[0.03] px-1.5 text-[11px] leading-none"
              title={
                prop.description
                  ? `${prop.propId} · ${prop.description}`
                  : prop.propId
              }
            >
              <span
                aria-hidden
                className="size-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: prop.hex ?? undefined }}
              />
              <span className="truncate text-foreground/70">{prop.propId}</span>
            </span>
          ))}
        </>
      )}
    </div>
  );
}

/**
 * Right-edge cluster for the filterbar: sketch color palette pills plus the
 * two episode-wide sketch actions (AI 检测 / 重新配色). Kept together so the
 * palette sits next to the button that mutates it — matches the NiceGUI
 * layout where these three elements share a row.
 *
 * `sketch_colors` lives on the script JSON as `{ identity_id: "#HEX NAME" }`.
 * identity_id is `<character>_<identity_name>`, sanitized via re.sub on
 * `[/\\:*?"<>|]` → `_` on the backend, so the split prefers the longest
 * known character-name prefix and falls back to first-underscore split.
 */
export function SketchStudioActions({
  project,
  episode,
  onOpenGridGallery,
  onOpenRenderGridGallery,
  showGridGalleryActions = true,
  showLegend = true,
  showDetectionSummary = true,
}: SketchStudioActionsProps) {
  const { t } = useTranslation();
  const { data: scriptRes } = useScript(project, episode);
  const { data: charsRes } = useCharacters(project);
  const { data: episodeRes } = useEpisodeDetail(project, episode);
  const { data: beatsRes } = useEpisodeBeats(project, episode);

  const sketchColors = scriptRes?.data?.sketch_colors ?? {};
  const characterNames = useMemo(
    () => new Set((charsRes?.data ?? []).map((c) => c.name)),
    [charsRes],
  );

  const entries = useMemo(() => {
    return Object.entries(sketchColors)
      .map(([identityId, value]) => {
        const { hex } = parseColorValue(value);
        const { character, identity } = splitIdentityId(
          identityId,
          characterNames,
        );
        return { identityId, hex, character, identity };
      })
      .filter((e) => !!e.hex)
      .sort((a, b) => a.identityId.localeCompare(b.identityId));
  }, [sketchColors, characterNames]);
  const propEntries = useMemo(() => {
    return (episodeRes?.data?.prop_menu ?? [])
      .map((prop) => {
        const { hex } = parseColorValue(prop.marker_color ?? "");
        return {
          propId: prop.prop_id,
          hex,
          description: prop.description ?? prop.visual_prompt ?? "",
        };
      })
      .filter((e) => !!e.hex)
      .sort((a, b) => a.propId.localeCompare(b.propId));
  }, [episodeRes]);
  const detectionSummary = useMemo(() => {
    const identityIds = new Set<string>();
    const propIds = new Set<string>();
    let beatCount = 0;
    for (const beat of beatsRes?.data ?? []) {
      const detectedIdentities = beat.detected_identities ?? [];
      const detectedProps = beat.detected_props ?? [];
      if (detectedIdentities.length > 0 || detectedProps.length > 0) {
        beatCount += 1;
      }
      for (const identityId of detectedIdentities) {
        identityIds.add(identityId);
      }
      for (const propId of detectedProps) {
        propIds.add(propId);
      }
    }
    return {
      beatCount,
      identityCount: identityIds.size,
      propCount: propIds.size,
    };
  }, [beatsRes]);
  const hasDetectionSummary =
    detectionSummary.beatCount > 0 ||
    detectionSummary.identityCount > 0 ||
    detectionSummary.propCount > 0;
  const hasVisibleLegend = showLegend && (entries.length > 0 || propEntries.length > 0);
  const hasVisibleDetectionSummary = showDetectionSummary && hasDetectionSummary;

  return (
    <div className="flex min-w-0 flex-wrap items-center justify-end gap-2 text-xs">
      {/* 配色胶囊 */}
      {hasVisibleLegend && (
        <div className="flex items-center gap-1">
          {entries.length > 0 && (
            <span className="text-[11px] text-muted-foreground/70">
              {t("episode.workbench.sketch.identityColors")}
            </span>
          )}
          {entries.map((e) => (
            <span
              key={e.identityId}
              className="inline-flex items-center gap-1 rounded-full border border-border/40 bg-card/50 px-1.5 py-0.5 leading-none"
              title={`${e.character}${e.identity ? ` · ${e.identity}` : ""}`}
            >
              <span
                aria-hidden
                className="size-1.5 shrink-0 rounded-full"
                style={{ backgroundColor: e.hex ?? undefined }}
              />
              <span className="truncate text-foreground/70">
                {e.character}
                {e.identity && (
                  <>
                    {" · "}
                    <span className="text-muted-foreground/50">{e.identity}</span>
                  </>
                )}
              </span>
            </span>
          ))}
          {propEntries.length > 0 && (
            <>
              {entries.length > 0 && (
                <span className="mx-0.5 h-3 w-px bg-border/40" aria-hidden />
              )}
              <span className="text-[11px] text-muted-foreground/70">
                {t("episode.workbench.sketch.propColors")}
              </span>
              {propEntries.map((prop) => (
                <span
                  key={prop.propId}
                  className="inline-flex items-center gap-1 rounded-full border border-border/40 bg-card/50 px-1.5 py-0.5 leading-none"
                  title={
                    prop.description
                      ? `${prop.propId} · ${prop.description}`
                      : prop.propId
                  }
                >
                  <span
                    aria-hidden
                    className="size-1.5 shrink-0 rounded-full"
                    style={{ backgroundColor: prop.hex ?? undefined }}
                  />
                  <span className="truncate text-foreground/70">{prop.propId}</span>
                </span>
              ))}
            </>
          )}
        </div>
      )}

      {/* AI检测结果 */}
      {hasVisibleDetectionSummary && (
        <span className="inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/8 px-2 py-0.5 text-[11px] leading-none text-primary/90">
          <Wand2 className="size-3 shrink-0" />
          <span className="font-medium">
            {t("episode.workbench.sketch.aiDetectResults")}
          </span>
          <span className="text-primary/70">
            {t("episode.workbench.sketch.aiDetectResultCounts", {
              beats: detectionSummary.beatCount,
              identities: detectionSummary.identityCount,
              props: detectionSummary.propCount,
            })}
          </span>
        </span>
      )}

      {(hasVisibleLegend || hasVisibleDetectionSummary) && (
        <span className="mx-0.5 h-4 w-px bg-border/40" aria-hidden />
      )}

      {/* 画廊按钮 */}
      {showGridGalleryActions && (onOpenGridGallery || onOpenRenderGridGallery) && (
        <div className="flex items-center gap-1">
          {onOpenGridGallery && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onOpenGridGallery}
              className="h-6 gap-1 rounded-[5px] bg-transparent px-1.5 text-[11px] font-medium text-foreground/75 shadow-none hover:bg-white/[0.04] hover:text-foreground dark:bg-transparent"
              title={t("episode.workbench.sketch.openGridGallery")}
            >
              <Grid2X2 className="size-3" />
              {t("episode.workbench.sketch.openGridGallery")}
            </Button>
          )}
          {onOpenRenderGridGallery && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onOpenRenderGridGallery}
              className="h-6 gap-1 rounded-[5px] bg-transparent px-1.5 text-[11px] font-medium text-foreground/75 shadow-none hover:bg-white/[0.04] hover:text-foreground dark:bg-transparent"
              title={t("episode.workbench.renderGrid.title")}
            >
              <Grid2X2 className="size-3" />
              {t("episode.workbench.renderGrid.title")}
            </Button>
          )}
        </div>
      )}

    </div>
  );
}
