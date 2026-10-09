// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { MyBuddyAction } from "@/features/companion/mybuddy-actions";
import type { MyBuddyBubbleKind } from "@/features/companion/mybuddy-bubbles";

export type PikoFestivalId = "dragon-boat-2026";
export type PikoFestivalSkin = "dragon-boat";
export type PikoFestivalPhase = "warmup" | "festival" | "extension";

export type PikoFestivalAction = {
  id: MyBuddyAction;
  weight: number;
};

export type PikoFestivalConfig = {
  id: PikoFestivalId;
  labelKey: string;
  warmupFrom: string;
  festivalFrom: string;
  festivalTo: string;
  // Normal festivals should end on festivalTo; long cultural seasons can extend this.
  displayTo: string;
  skin: PikoFestivalSkin;
  bubbleKind: MyBuddyBubbleKind;
  idleActions: readonly PikoFestivalAction[];
};

export type ActivePikoFestival = PikoFestivalConfig & {
  phase: PikoFestivalPhase;
};

export const PIKO_FESTIVALS = [
  {
    id: "dragon-boat-2026",
    labelKey: "myBuddy.festivals.dragonBoat2026",
    warmupFrom: "2026-06-15",
    festivalFrom: "2026-06-19",
    festivalTo: "2026-06-19",
    displayTo: "2026-06-19",
    skin: "dragon-boat",
    bubbleKind: "dragonBoat",
    idleActions: [
      { id: "dragon-boat-paddle", weight: 2 },
      { id: "zongzi-check", weight: 2 },
    ],
  },
] as const satisfies readonly PikoFestivalConfig[];

function toLocalDateKey(date: Date) {
  const year = date.getFullYear();
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function getPikoFestivalPhase(
  festival: PikoFestivalConfig,
  today: string,
): PikoFestivalPhase | null {
  if (festival.warmupFrom <= today && today < festival.festivalFrom) return "warmup";
  if (festival.festivalFrom <= today && today <= festival.festivalTo) return "festival";
  if (festival.festivalTo < today && today <= festival.displayTo) return "extension";
  return null;
}

export function getActivePikoFestival(date = new Date()): ActivePikoFestival | null {
  const today = toLocalDateKey(date);
  for (const festival of PIKO_FESTIVALS) {
    const phase = getPikoFestivalPhase(festival, today);
    if (phase) return { ...festival, phase };
  }
  return null;
}
