// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { clusterConfig } from "@/lib/cluster-config";
import { switchRegion } from "@/lib/region-switch";
import { useRegionStore } from "@/stores/region-store";

export function RegionBadge() {
  // `clusterConfig.mode` is read from a module-level object that is frozen
  // for the lifetime of the page load (see `loadClusterConfig`), so the
  // early-return order is stable across renders — hooks below always run
  // for a given mode. In `mode: "none"` we short-circuit before any hook
  // that requires a provider (e.g. `useQueryClient`) so the badge can
  // render in environments where TanStack Query isn't mounted.
  if (clusterConfig.mode !== "multi-region") return null;
  return <RegionBadgeInner />;
}

function RegionBadgeInner() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const regionId = useRegionStore((s) => s.selectedRegionId);
  const [open, setOpen] = useState(false);
  const [picking, setPicking] = useState<string | null>(null);

  const current = clusterConfig.regions.find((r) => r.id === regionId);
  if (!current) return null;

  const others = clusterConfig.regions.filter((r) => r.id !== regionId);

  return (
    <>
      <button
        type="button"
        aria-label={t("region.badge.label")}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        className="rounded-md border px-2 py-0.5 text-xs text-muted-foreground hover:bg-muted"
      >
        {t("region.badge.label")}: {current.displayName}
      </button>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("region.switch.confirm.title")}</AlertDialogTitle>
            <AlertDialogDescription>{t("region.switch.confirm.body")}</AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex flex-col gap-2 pt-2">
            {others.map((r) => (
              <button
                key={r.id}
                type="button"
                aria-pressed={picking === r.id}
                className="rounded-md border px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => setPicking(r.id)}
              >
                {r.displayName}
              </button>
            ))}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={!picking}
              onClick={async () => {
                if (!picking) return;
                await switchRegion({ newRegionId: picking, queryClient });
              }}
            >
              {t("region.switch.confirm.cta")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
