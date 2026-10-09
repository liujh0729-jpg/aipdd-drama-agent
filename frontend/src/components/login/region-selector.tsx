// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { clusterConfig } from "@/lib/cluster-config";
import { setRegionCookie } from "@/lib/region-cookie";
import { authRequired } from "@/lib/runtime-config";
import { useRegionStore } from "@/stores/region-store";

export function RegionSelector() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const selected = useRegionStore((s) => s.selectedRegionId);
  const setRegion = useRegionStore((s) => s.setRegion);

  const selectRegion = (id: string) => {
    setRegion(id);
    setRegionCookie(id);
    if (!authRequired()) {
      navigate({ to: "/", replace: true });
    }
  };

  useEffect(() => {
    if (
      clusterConfig.mode === "multi-region" &&
      clusterConfig.regions.length === 1 &&
      selected !== clusterConfig.regions[0].id
    ) {
      selectRegion(clusterConfig.regions[0].id);
    }
  }, [selected, setRegion, navigate]);

  if (clusterConfig.mode !== "multi-region") return null;

  if (clusterConfig.regions.length === 0) {
    return (
      <div className="space-y-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm">
        <div className="font-medium">{t("region.empty.title")}</div>
        <div className="text-muted-foreground">{t("region.empty.body")}</div>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="rounded-md border px-3 py-1 text-xs hover:bg-muted"
        >
          {t("region.empty.retry")}
        </button>
      </div>
    );
  }

  if (clusterConfig.regions.length === 1) {
    const only = clusterConfig.regions[0];
    return (
      <div className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
        <span className="text-muted-foreground">{t("region.picker.label")}</span>
        <span>{only.displayName}</span>
      </div>
    );
  }

  return (
    <Select
      value={selected ?? undefined}
      onValueChange={(id) => {
        if (typeof id !== "string") return;
        selectRegion(id);
      }}
    >
      <SelectTrigger aria-label={t("region.picker.label")}>
        <SelectValue placeholder={t("region.picker.placeholder")} />
      </SelectTrigger>
      <SelectContent>
        {clusterConfig.regions.map((r) => (
          <SelectItem key={r.id} value={r.id}>
            {r.displayName}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
