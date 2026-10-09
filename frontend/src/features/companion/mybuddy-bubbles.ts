// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export const MYBUDDY_BUBBLE_KEYS = {
  running: [
    "myBuddy.bubbles.running.queued",
    "myBuddy.bubbles.running.working",
    "myBuddy.bubbles.running.holdOn",
    "myBuddy.bubbles.running.gears",
    "myBuddy.bubbles.running.onIt",
    "myBuddy.bubbles.running.readingRoom",
    "myBuddy.bubbles.running.keepWindowOpen",
    "myBuddy.bubbles.running.checkingEdges",
    "myBuddy.bubbles.running.smallSteps",
    "myBuddy.bubbles.running.movingPieces",
  ],
  success: [
    "myBuddy.bubbles.success.done",
    "myBuddy.bubbles.success.nice",
    "myBuddy.bubbles.success.landed",
    "myBuddy.bubbles.success.tidy",
    "myBuddy.bubbles.success.flag",
    "myBuddy.bubbles.success.cleared",
    "myBuddy.bubbles.success.goodShape",
    "myBuddy.bubbles.success.savedTrip",
    "myBuddy.bubbles.success.noDrama",
    "myBuddy.bubbles.success.oneLessThing",
  ],
  failure: [
    "myBuddy.bubbles.failure.stuck",
    "myBuddy.bubbles.failure.checking",
    "myBuddy.bubbles.failure.retry",
    "myBuddy.bubbles.failure.noted",
    "myBuddy.bubbles.failure.box",
    "myBuddy.bubbles.failure.hitSnag",
    "myBuddy.bubbles.failure.needsLook",
    "myBuddy.bubbles.failure.leftClue",
    "myBuddy.bubbles.failure.tryAgainLater",
    "myBuddy.bubbles.failure.notClean",
  ],
  // Keep the default idle pool season-neutral. Festival/holiday copy belongs in
  // the matching festival pool below, even if a related decoration is permanent.
  idle: [
    "myBuddy.bubbles.idle.watching",
    "myBuddy.bubbles.idle.quiet",
    "myBuddy.bubbles.idle.standingBy",
    "myBuddy.bubbles.idle.idea",
    "myBuddy.bubbles.idle.tinyBreak",
    "myBuddy.bubbles.idle.noRush",
    "myBuddy.bubbles.idle.roomBreathes",
    "myBuddy.bubbles.idle.stillHere",
    "myBuddy.bubbles.idle.goodPause",
    "myBuddy.bubbles.idle.mindingTop",
    "myBuddy.bubbles.idle.keepRhythm",
    "myBuddy.bubbles.idle.littleCorner",
    "myBuddy.bubbles.idle.online",
    "myBuddy.bubbles.idle.nextStep",
    "myBuddy.bubbles.idle.smallPatrol",
    "myBuddy.bubbles.idle.softPause",
  ],
  dragonBoat: [
    "myBuddy.bubbles.dragonBoat.leafScent",
    "myBuddy.bubbles.dragonBoat.tightWrap",
    "myBuddy.bubbles.dragonBoat.waterQuiet",
    "myBuddy.bubbles.dragonBoat.goodStroke",
    "myBuddy.bubbles.dragonBoat.steadyBoat",
  ],
} as const;

export type MyBuddyBubbleKind = keyof typeof MYBUDDY_BUBBLE_KEYS;
