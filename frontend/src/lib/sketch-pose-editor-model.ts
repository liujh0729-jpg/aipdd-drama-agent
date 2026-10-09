// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { PosePoint, PoseSkeleton } from "@/lib/queries/sketch-pose-editor";

export interface PoseJointHit {
  skeletonIndex: number;
  jointKey: string;
}

export interface PoseDragState extends PoseJointHit {
  bodyDrag: boolean;
  startPoint: PosePoint;
  startJoints: Record<string, PosePoint>;
}

export function hitTestPoseJoint(
  skeletons: PoseSkeleton[],
  point: PosePoint,
  threshold: number,
): PoseJointHit | null {
  const ordered = skeletons
    .map((skeleton, index) => ({ skeleton, index }))
    .filter(({ skeleton }) => skeleton.visible)
    .sort((a, b) => Number(b.skeleton.active === true) - Number(a.skeleton.active === true));

  for (const { skeleton, index } of ordered) {
    let bestKey = "";
    let bestDistance = threshold;
    for (const [jointKey, joint] of Object.entries(skeleton.joints)) {
      const distance = Math.hypot(joint.x - point.x, joint.y - point.y);
      if (distance < bestDistance) {
        bestKey = jointKey;
        bestDistance = distance;
      }
    }
    if (bestKey) return { skeletonIndex: index, jointKey: bestKey };
  }
  return null;
}

export function movePoseDrag(
  skeletons: PoseSkeleton[],
  drag: PoseDragState,
  point: PosePoint,
  width: number,
  height: number,
): PoseSkeleton[] {
  return skeletons.map((skeleton, index) => {
    if (index !== drag.skeletonIndex) return skeleton;
    const nextJoints = { ...skeleton.joints };
    if (drag.bodyDrag) {
      const dx = point.x - drag.startPoint.x;
      const dy = point.y - drag.startPoint.y;
      for (const [key, start] of Object.entries(drag.startJoints)) {
        nextJoints[key] = {
          x: clamp(start.x + dx, 0, width),
          y: clamp(start.y + dy, 0, height),
        };
      }
    } else {
      nextJoints[drag.jointKey] = {
        x: clamp(point.x, 0, width),
        y: clamp(point.y, 0, height),
      };
    }
    return { ...skeleton, joints: nextJoints, visible: true, active: true };
  });
}

export function addSkeletonToFrame(
  skeletons: PoseSkeleton[],
  identityId: string,
): PoseSkeleton[] {
  return skeletons.map((skeleton) => ({
    ...skeleton,
    visible: skeleton.identityId === identityId ? true : skeleton.visible,
    active: skeleton.identityId === identityId,
  }));
}

export function removeSkeletonFromFrame(
  skeletons: PoseSkeleton[],
  identityId: string,
): PoseSkeleton[] {
  const next = skeletons.map((skeleton) =>
    skeleton.identityId === identityId
      ? { ...skeleton, visible: false, active: false }
      : skeleton,
  );
  if (next.some((skeleton) => skeleton.active)) return next;
  const firstVisible = next.findIndex((skeleton) => skeleton.visible);
  return firstVisible >= 0
    ? next.map((skeleton, index) => ({
        ...skeleton,
        active: index === firstVisible,
      }))
    : next;
}

export function setActiveSkeleton(
  skeletons: PoseSkeleton[],
  identityId: string,
): PoseSkeleton[] {
  return skeletons.map((skeleton) => ({
    ...skeleton,
    active: skeleton.identityId === identityId,
  }));
}

export function resetSkeletonPoses(
  skeletons: PoseSkeleton[],
  initialSkeletons: PoseSkeleton[],
): PoseSkeleton[] {
  const initialById = new Map(
    initialSkeletons.map((skeleton) => [skeleton.identityId, skeleton]),
  );
  return skeletons.map((skeleton) => {
    const initial = initialById.get(skeleton.identityId);
    if (!initial) return skeleton;
    return {
      ...skeleton,
      joints: cloneJoints(initial.joints),
      lineWidth: initial.lineWidth,
      headRadius: initial.headRadius,
    };
  });
}

export function cloneJoints(
  joints: Record<string, PosePoint>,
): Record<string, PosePoint> {
  return Object.fromEntries(
    Object.entries(joints).map(([key, point]) => [
      key,
      { x: point.x, y: point.y },
    ]),
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
