// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { quotaSafeStateStorage } from "@/lib/localStorageQuota";
import { clusterConfig } from "@/lib/cluster-config";

interface RegionState {
  selectedRegionId: string | null;
  isSwitching: boolean;
  isLocked: boolean;
  setRegion: (id: string) => void;
  clearRegion: () => void;
  setSwitching: (v: boolean) => void;
  setLocked: (v: boolean) => void;
  sanitizeAgainstConfig: () => void;
}

export const useRegionStore = create<RegionState>()(
  persist(
    (set, get) => ({
      selectedRegionId: null,
      isSwitching: false,
      isLocked: false,
      setRegion: (id) => set({ selectedRegionId: typeof id === "string" ? id : null }),
      clearRegion: () => set({ selectedRegionId: null }),
      setSwitching: (v) => set({ isSwitching: v }),
      setLocked: (v) => set({ isLocked: v }),
      sanitizeAgainstConfig: () => {
        const id = get().selectedRegionId;
        if (id && !clusterConfig.regions.some((r) => r.id === id)) {
          set({ selectedRegionId: null });
        }
      },
    }),
    {
      name: "supertale-region",
      version: 1,
      storage: createJSONStorage(() => quotaSafeStateStorage),
      partialize: (s) => ({ selectedRegionId: s.selectedRegionId }),
      merge: (persisted, current) => {
        const raw = (persisted as { selectedRegionId?: unknown } | null)?.selectedRegionId;
        return {
          ...current,
          selectedRegionId: typeof raw === "string" ? raw : null,
        };
      },
    },
  ),
);
