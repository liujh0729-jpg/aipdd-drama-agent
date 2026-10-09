// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import {
  getMyBuddyHandUsage,
  isMyBuddyAccessoryDisabledAction,
  type MyBuddyAction,
} from "@/features/companion/mybuddy-actions";
import {
  PIKO_ACCESSORIES,
  type PikoAccessoryConfig,
  type PikoAccessoryId,
  type PikoAccessoryLayer,
} from "@/features/companion/piko-accessories";
import type { PikoFestivalPhase, PikoFestivalSkin } from "@/features/companion/piko-festivals";
import type { MyBuddySpecialAction } from "@/features/companion/use-mybuddy-companion-controller";
import "./mybuddy-companion.css";

const MYBUDDY_PIXELS = [
  "....BBB....",
  ".....B.....",
  "....BBB....",
  "...BBBBB...",
  "..BBBBBBB..",
  ".BBBBBBBBB.",
  "..BBBBBBB..",
  "...B...B...",
] as const;

const PIKO_LEAF_HIDDEN_ACCESSORIES = new Set<PikoAccessoryId>([
  "piko-accessory-asgard-horns",
  "piko-accessory-bamboo-hat",
  "piko-accessory-gary-snail",
  "piko-accessory-golden-hoop-staff",
  "piko-accessory-little-king",
  "piko-accessory-mengnan-wand",
]);

type PikoActionFigureProps = {
  action: MyBuddyAction;
  accessory?: PikoAccessoryId;
  festivalSkin?: PikoFestivalSkin | null;
  festivalPhase?: PikoFestivalPhase | null;
  bubbleKey?: string | null;
  isBubbleLeaving?: boolean;
  specialAction?: MyBuddySpecialAction | null;
  transitionState?: "entering" | "leaving";
  className?: string;
  style?: CSSProperties;
};

function getPixelClass(rowIndex: number, columnIndex: number) {
  const classes = ["mybuddy-companion-pixel"];
  if (rowIndex === 5 && (columnIndex === 1 || columnIndex === 9)) {
    classes.push("mybuddy-companion-arm-pixel");
    classes.push(columnIndex === 1 ? "mybuddy-companion-arm-left" : "mybuddy-companion-arm-right");
  }
  if (rowIndex === MYBUDDY_PIXELS.length - 1) {
    classes.push("mybuddy-companion-leg-pixel");
    classes.push(columnIndex < 5 ? "mybuddy-companion-leg-left" : "mybuddy-companion-leg-right");
  }
  return classes.join(" ");
}

function renderAccessoryLayer(layer: PikoAccessoryLayer, key: string) {
  return (
    <span
      key={key}
      className="mybuddy-companion-accessory-preview"
      aria-hidden="true"
      data-slot={layer.slot}
      style={{
        left: layer.x,
        top: layer.y,
        width: layer.size,
        height: layer.size,
        rotate: layer.rotate === undefined ? undefined : `${layer.rotate}deg`,
      }}
    >
      <img className="mybuddy-companion-accessory-image" src={layer.src} alt="" />
    </span>
  );
}

export function PikoActionFigure({
  action,
  accessory = "none",
  festivalSkin,
  festivalPhase,
  bubbleKey,
  isBubbleLeaving = false,
  specialAction,
  transitionState,
  className,
  style,
}: PikoActionFigureProps) {
  const { t } = useTranslation();
  const handUsage = getMyBuddyHandUsage(action);
  const accessoryConfig: PikoAccessoryConfig | null =
    accessory === "none" || isMyBuddyAccessoryDisabledAction(action)
      ? null
      : PIKO_ACCESSORIES[accessory];
  const isAccessoryDisabledForAction =
    accessoryConfig?.disabledActions?.includes(
      action as (typeof accessoryConfig.disabledActions)[number],
    ) ?? false;
  const shouldShowPrimaryAccessory =
    accessoryConfig &&
    !isAccessoryDisabledForAction &&
    (accessoryConfig.slot !== "hand" || handUsage === "free");
  const accessoryLayers = [
    ...(shouldShowPrimaryAccessory ? [accessoryConfig] : []),
    ...(accessoryConfig?.attachments ?? []),
  ];
  // Hide the permanent leaf for head-conflicting accessory sets even when the
  // hand-held primary layer is suppressed by the current action.
  const shouldHideLeafForAccessory =
    Boolean(accessoryConfig) && PIKO_LEAF_HIDDEN_ACCESSORIES.has(accessory);
  const shouldShowLeaf = !shouldHideLeafForAccessory;

  return (
    <div
      className={["mybuddy-companion-anchor", className].filter(Boolean).join(" ")}
      data-action={action}
      data-accessory={accessory === "none" ? undefined : accessory}
      data-festival={festivalSkin ?? undefined}
      data-festival-phase={festivalPhase ?? undefined}
      data-transition-state={transitionState}
      data-special-action={specialAction ?? undefined}
      aria-hidden="true"
      style={style}
    >
      <div className="mybuddy-companion-visual">
        <div className="mybuddy-companion-shadow" />
        <div className="mybuddy-companion-peek-ripple" />
        <div className="mybuddy-companion-stage">
          <div className="mybuddy-companion-figure">
            {MYBUDDY_PIXELS.map((row, rowIndex) =>
              [...row].map((cell, columnIndex) =>
                cell === "." ? null : (
                  <span
                    key={`${rowIndex}-${columnIndex}`}
                    className={getPixelClass(rowIndex, columnIndex)}
                    style={{
                      gridColumnStart: columnIndex + 1,
                      gridRowStart: rowIndex + 1,
                    }}
                  />
                ),
              ),
            )}
            <span className="mybuddy-companion-eye mybuddy-companion-eye-left" />
            <span className="mybuddy-companion-eye mybuddy-companion-eye-right" />
            <span className="mybuddy-companion-sleep-foot mybuddy-companion-sleep-foot-one" />
            <span className="mybuddy-companion-sleep-foot mybuddy-companion-sleep-foot-two" />
            {shouldShowLeaf && (
              <div className="mybuddy-companion-festival-accessory">
                <span className="mybuddy-companion-festival-leaf" />
              </div>
            )}
          </div>
          <div className="mybuddy-companion-stars">
            <span className="mybuddy-companion-star mybuddy-companion-star-one" />
            <span className="mybuddy-companion-star mybuddy-companion-star-two" />
            <span className="mybuddy-companion-star mybuddy-companion-star-three" />
          </div>
          <div className="mybuddy-companion-meteor-shower">
            <span className="mybuddy-companion-meteor mybuddy-companion-meteor-one" />
            <span className="mybuddy-companion-meteor mybuddy-companion-meteor-two" />
            <span className="mybuddy-companion-meteor mybuddy-companion-meteor-three" />
          </div>
          <div className="mybuddy-companion-map">
            <span className="mybuddy-companion-map-route mybuddy-companion-map-route-one" />
            <span className="mybuddy-companion-map-route mybuddy-companion-map-route-two" />
            <span className="mybuddy-companion-map-pin" />
          </div>
          <div className="mybuddy-companion-fishing">
            <span className="mybuddy-companion-fishing-rod" />
            <span className="mybuddy-companion-fishing-line" />
            <span className="mybuddy-companion-fishing-bobber" />
          </div>
          <div className="mybuddy-companion-soap-bubbles">
            <span className="mybuddy-companion-soap-bubble mybuddy-companion-soap-bubble-one" />
            <span className="mybuddy-companion-soap-bubble mybuddy-companion-soap-bubble-two" />
            <span className="mybuddy-companion-soap-bubble mybuddy-companion-soap-bubble-three" />
            <span className="mybuddy-companion-soap-bubble mybuddy-companion-soap-bubble-four" />
            <span className="mybuddy-companion-soap-bubble mybuddy-companion-soap-bubble-five" />
          </div>
          <div className="mybuddy-companion-dragon-boat">
            <span className="mybuddy-companion-dragon-boat-body" />
            <span className="mybuddy-companion-dragon-boat-paddle" />
          </div>
          <div className="mybuddy-companion-zongzi">
            <span className="mybuddy-companion-zongzi-body">
              <span className="mybuddy-companion-zongzi-piece mybuddy-companion-zongzi-piece-one" />
              <span className="mybuddy-companion-zongzi-piece mybuddy-companion-zongzi-piece-two" />
              <span className="mybuddy-companion-zongzi-piece mybuddy-companion-zongzi-piece-three" />
              <span className="mybuddy-companion-zongzi-piece mybuddy-companion-zongzi-piece-four" />
              <span className="mybuddy-companion-zongzi-piece mybuddy-companion-zongzi-piece-five" />
              <span className="mybuddy-companion-zongzi-piece mybuddy-companion-zongzi-piece-six" />
              <span className="mybuddy-companion-zongzi-shine mybuddy-companion-zongzi-shine-vertical" />
              <span className="mybuddy-companion-zongzi-shine mybuddy-companion-zongzi-shine-horizontal" />
            </span>
            <span className="mybuddy-companion-zongzi-steam mybuddy-companion-zongzi-steam-one" />
            <span className="mybuddy-companion-zongzi-steam mybuddy-companion-zongzi-steam-two" />
          </div>
          <div className="mybuddy-companion-keyboard">
            <span className="mybuddy-companion-key mybuddy-companion-key-one" />
            <span className="mybuddy-companion-key mybuddy-companion-key-two" />
            <span className="mybuddy-companion-key mybuddy-companion-key-three" />
          </div>
          <span className="mybuddy-companion-question">?</span>
          <span className="mybuddy-companion-map-alert">!</span>
          <div className="mybuddy-companion-flag">
            <span className="mybuddy-companion-flag-pole" />
            <span className="mybuddy-companion-flag-cloth" />
          </div>
          <div className="mybuddy-companion-repair">
            <span className="mybuddy-companion-repair-box">
              <span className="mybuddy-companion-repair-light" />
            </span>
            <span className="mybuddy-companion-repair-alert">!</span>
          </div>
          <div className="mybuddy-companion-sleep-mark">
            <span className="mybuddy-companion-sleep-z mybuddy-companion-sleep-z-one">Z</span>
            <span className="mybuddy-companion-sleep-z mybuddy-companion-sleep-z-two">Z</span>
          </div>
          <div className="mybuddy-companion-cargo" />
          {accessoryLayers.map((layer, index) => renderAccessoryLayer(layer, `${layer.src}-${index}`))}
        </div>
        <span className="mybuddy-companion-walk-by-bubble">{t("myBuddy.bubbles.walkBy")}</span>
      </div>
      {bubbleKey && (
        <span
          className="mybuddy-companion-bubble"
          data-state={isBubbleLeaving ? "leaving" : "entering"}
        >
          <span className="mybuddy-companion-bubble-text">{t(bubbleKey)}</span>
        </span>
      )}
    </div>
  );
}
