// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import type { MyBuddyAction } from "@/features/companion/mybuddy-actions";
import type { MyBuddySpecialAction } from "@/features/companion/use-mybuddy-companion-controller";
import type { PikoAccessoryId } from "@/features/companion/piko-accessories";
import type { PikoFestivalPhase, PikoFestivalSkin } from "@/features/companion/piko-festivals";
import { PikoActionFigure } from "@/features/companion/PikoActionFigure";

const ACTION_FADE_MS = 220;

type PikoActionTransitionProps = {
  action: MyBuddyAction;
  accessory?: PikoAccessoryId;
  festivalSkin?: PikoFestivalSkin | null;
  festivalPhase?: PikoFestivalPhase | null;
  bubbleKey?: string | null;
  isBubbleLeaving?: boolean;
  specialAction?: MyBuddySpecialAction | null;
  className?: string;
  style?: CSSProperties;
};

type LeavingAction = {
  action: MyBuddyAction;
  accessory?: PikoAccessoryId;
  festivalSkin?: PikoFestivalSkin | null;
  festivalPhase?: PikoFestivalPhase | null;
  key: number;
};

function shouldCrossfade(from: MyBuddyAction, to: MyBuddyAction) {
  return from !== to && from !== "peek" && to !== "peek";
}

export function PikoActionTransition({
  action,
  accessory = "none",
  festivalSkin,
  festivalPhase,
  bubbleKey,
  isBubbleLeaving = false,
  specialAction,
  className,
  style,
}: PikoActionTransitionProps) {
  const previousActionRef = useRef(action);
  const previousAccessoryRef = useRef(accessory);
  const previousFestivalSkinRef = useRef(festivalSkin);
  const previousFestivalPhaseRef = useRef(festivalPhase);
  const transitionKeyRef = useRef(0);
  const [leavingAction, setLeavingAction] = useState<LeavingAction | null>(null);
  const [enteringKey, setEnteringKey] = useState(0);

  useEffect(() => {
    const previousAction = previousActionRef.current;
    const previousAccessory = previousAccessoryRef.current;
    const previousFestivalSkin = previousFestivalSkinRef.current;
    const previousFestivalPhase = previousFestivalPhaseRef.current;

    if (previousAction !== action) {
      if (shouldCrossfade(previousAction, action)) {
        transitionKeyRef.current += 1;
        const nextKey = transitionKeyRef.current;
        setLeavingAction({
          action: previousAction,
          accessory: previousAccessory,
          festivalSkin: previousFestivalSkin,
          festivalPhase: previousFestivalPhase,
          key: nextKey,
        });
        setEnteringKey(nextKey);

        const timeout = window.setTimeout(() => {
          setLeavingAction((current) => (current?.key === nextKey ? null : current));
        }, ACTION_FADE_MS);

        previousActionRef.current = action;
        previousAccessoryRef.current = accessory;
        previousFestivalSkinRef.current = festivalSkin;
        previousFestivalPhaseRef.current = festivalPhase;
        return () => window.clearTimeout(timeout);
      }

      setLeavingAction(null);
      setEnteringKey(0);
    }

    previousActionRef.current = action;
    previousAccessoryRef.current = accessory;
    previousFestivalSkinRef.current = festivalSkin;
    previousFestivalPhaseRef.current = festivalPhase;
  }, [accessory, action, festivalPhase, festivalSkin]);

  return (
    <>
      {leavingAction && (
        <PikoActionFigure
          key={`leaving-${leavingAction.key}`}
          action={leavingAction.action}
          accessory={leavingAction.accessory}
          festivalSkin={leavingAction.festivalSkin}
          festivalPhase={leavingAction.festivalPhase}
          className={className}
          style={style}
          transitionState="leaving"
        />
      )}
      <PikoActionFigure
        key={`current-${action}-${accessory}-${enteringKey}`}
        action={action}
        accessory={accessory}
        festivalSkin={festivalSkin}
        festivalPhase={festivalPhase}
        bubbleKey={bubbleKey}
        isBubbleLeaving={isBubbleLeaving}
        specialAction={specialAction}
        className={className}
        style={style}
        transitionState={enteringKey > 0 ? "entering" : undefined}
      />
    </>
  );
}
