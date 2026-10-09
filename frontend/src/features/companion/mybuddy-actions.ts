// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export const MYBUDDY_ACTIONS = [
  { id: "idle", labelKey: "myBuddy.actions.idle" },
  { id: "peek", labelKey: "myBuddy.actions.peek" },
  { id: "count-stars", labelKey: "myBuddy.actions.countStars" },
  { id: "sleep", labelKey: "myBuddy.actions.sleep" },
  { id: "carry-box", labelKey: "myBuddy.actions.carryBox" },
  { id: "stretch", labelKey: "myBuddy.actions.stretch" },
  { id: "watch-meteor", labelKey: "myBuddy.actions.watchMeteor" },
  { id: "read-map", labelKey: "myBuddy.actions.readMap" },
  { id: "fish", labelKey: "myBuddy.actions.fish" },
  { id: "blow-bubbles", labelKey: "myBuddy.actions.blowBubbles" },
  { id: "dragon-boat-paddle", labelKey: "myBuddy.actions.dragonBoatPaddle" },
  { id: "zongzi-check", labelKey: "myBuddy.actions.zongziCheck" },
  { id: "typing", labelKey: "myBuddy.actions.typing" },
  { id: "flag", labelKey: "myBuddy.actions.flag" },
  { id: "repair", labelKey: "myBuddy.actions.repair" },
  { id: "walk-by", labelKey: "myBuddy.actions.walkBy" },
] as const;

export type MyBuddyAction = (typeof MYBUDDY_ACTIONS)[number]["id"];
export type MyBuddyHandUsage = "free" | "occupied";

export const DEFAULT_MYBUDDY_ACTION: MyBuddyAction = "idle";

export const MYBUDDY_IDLE_ACTIONS = [
  "count-stars",
  "sleep",
  "walk-by",
  "carry-box",
  "stretch",
  "watch-meteor",
  "read-map",
  "fish",
  "blow-bubbles",
] as const satisfies readonly MyBuddyAction[];

const MYBUDDY_ACTION_IDS = new Set<string>(MYBUDDY_ACTIONS.map((action) => action.id));

const MYBUDDY_OCCUPIED_HAND_ACTIONS = new Set<MyBuddyAction>([
  "sleep",
  "carry-box",
  "read-map",
  "dragon-boat-paddle",
  "zongzi-check",
  "typing",
]);

const MYBUDDY_DISABLED_ACCESSORY_ACTIONS = new Set<MyBuddyAction>([
  "dragon-boat-paddle",
  "zongzi-check",
]);

export function isMyBuddyAction(value: string): value is MyBuddyAction {
  return MYBUDDY_ACTION_IDS.has(value);
}

export function getMyBuddyHandUsage(action: MyBuddyAction): MyBuddyHandUsage {
  return MYBUDDY_OCCUPIED_HAND_ACTIONS.has(action) ? "occupied" : "free";
}

export function isMyBuddyAccessoryDisabledAction(action: MyBuddyAction) {
  return MYBUDDY_DISABLED_ACCESSORY_ACTIONS.has(action);
}
