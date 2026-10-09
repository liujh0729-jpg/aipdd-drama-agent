// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { PushTarget, PushTargetKind } from "@/api/push";
import { coerceSlotTarget } from "@/features/canvas/domain/mainlineNodeTypes";

/** 来自 `__freezone_source` / 资产 source 的来源描述。 */
export type FreezoneSource = {
  kind?: string;
  role?: string;
  meta?: Record<string, unknown>;
};

export function isScenePushTargetKind(kind: PushTargetKind): boolean {
  return normalizeScenePushTargetKind(kind) !== null;
}

function normalizeScenePushTargetKind(kind: string): PushTargetKind | null {
  if (kind === "scene_360") return "scene_director_pano_360";
  return (
    kind === "scene_master" ||
    kind === "scene_reverse_master" ||
    kind === "scene_spatial_layout" ||
    kind === "scene_director_world" ||
    kind === "scene_director_pano_360" ||
    kind === "scene_3gs_master_ply" ||
    kind === "scene_3gs_reverse_ply" ||
    kind === "scene_3gs_pano_ply" ||
    kind === "scene_3gs_custom_scene"
  ) ? kind as PushTargetKind : null;
}

export function isPlyOrGlbPushTargetKind(kind: PushTargetKind): boolean {
  return (
    kind === "scene_3gs_master_ply" ||
    kind === "scene_3gs_reverse_ply" ||
    kind === "scene_3gs_pano_ply" ||
    kind === "scene_3gs_custom_scene"
  );
}

/** 从来源推断出一个(可能不完整的)推送目标。 */
export function inferDefaultTarget(
  source: FreezoneSource | undefined,
): (Partial<PushTarget> & { kind: PushTargetKind }) | undefined {
  if (!source?.kind) return undefined;
  const meta = source.meta ?? {};
  const kind = source.kind as string;
  const role = typeof source.role === "string" ? source.role : "";
  if (
    kind === "frame" ||
    kind === "sketch" ||
    kind === "director_render" ||
    kind === "selected_background" ||
    kind === "video"
  ) {
    if (typeof meta.episode === "number" && typeof meta.beat === "number") {
      return {
        kind: kind as PushTargetKind,
        episode: meta.episode as number,
        beat: meta.beat as number,
      };
    }
  }
  // 音频节点 → beat_audio(后端 canonical kind)。源 kind 是媒体类型 "audio"
  // 或资产 role 是 "current_audio",都映射到 beat 的音频 slot。
  if (kind === "audio" || kind === "beat_audio" || role === "current_audio") {
    if (typeof meta.episode === "number" && typeof meta.beat === "number") {
      return {
        kind: "beat_audio",
        episode: meta.episode as number,
        beat: meta.beat as number,
      };
    }
  }
  if (
    kind === "identity" ||
    kind === "identity_costume" ||
    kind === "identity_portrait"
  ) {
    if (typeof meta.character === "string") {
      const identityId =
        typeof meta.identity_id === "string" ? meta.identity_id : "";
      if ((role === "identity_portrait" || kind === "identity_portrait") && identityId) {
        return {
          kind: "identity_portrait",
          character: meta.character,
          identity_id: identityId,
        };
      }
      // 后端把角色 portrait 标成 kind=identity / role=character_portrait,但没有
      // 具体 identity_id。这种情况下提交目标其实是角色 portrait。
      if (!identityId && role.includes("portrait")) {
        return { kind: "portrait", character: meta.character };
      }
      if (role === "identity_costume" || kind === "identity_costume") {
        return {
          kind: "identity_costume",
          character: meta.character,
          identity_id: identityId,
        };
      }
      return {
        kind: "identity",
        character: meta.character,
        identity_id: identityId,
      };
    }
  }
  if (kind === "portrait") {
    if (typeof meta.character === "string") {
      return { kind: "portrait", character: meta.character };
    }
  }
  const sceneKind = normalizeScenePushTargetKind(kind);
  if (sceneKind) {
    if (typeof meta.scene_id === "string") {
      return { kind: sceneKind, scene_id: meta.scene_id };
    }
  }
  if (kind === "scene") {
    const sceneId = typeof meta.scene_id === "string" ? meta.scene_id : "";
    if (sceneId && role === "scene_director_world") {
      return { kind: "scene_director_world", scene_id: sceneId };
    }
    // 关键: 先做精确匹配 (isScenePushTargetKind),不然 role.includes("master")
    // 这种 catch-all 会把 "scene_3gs_master_ply" 错误归到 "scene_master"
    // (image slot) — 用户 commit PLY 默认到 master.png,这是 bug。
    const roleSceneKind = normalizeScenePushTargetKind(role);
    if (sceneId && roleSceneKind) {
      return { kind: roleSceneKind, scene_id: sceneId };
    }
    // 模糊匹配 fallback (role 不是标准 PushTargetKind 名时兜底)。
    if (sceneId && role.includes("director_pano_360")) {
      return { kind: "scene_director_pano_360", scene_id: sceneId };
    }
    if (sceneId && role === "scene_reverse_master") {
      return { kind: "scene_reverse_master", scene_id: sceneId };
    }
    if (sceneId && role === "scene_spatial_layout") {
      return { kind: "scene_spatial_layout", scene_id: sceneId };
    }
    // role 单独包含 "master" 时只可能是 scene_master (其他 master 角色
    // 例如 scene_3gs_master_ply 上面已精确匹配掉了)。
    if (sceneId && role.includes("master")) {
      return { kind: "scene_master", scene_id: sceneId };
    }
  }
  if (kind === "director") {
    const sceneId = typeof meta.scene_id === "string" ? meta.scene_id : "";
    if (sceneId && role === "scene_director_world") {
      return { kind: "scene_director_world", scene_id: sceneId };
    }
    const roleSceneKind = normalizeScenePushTargetKind(role);
    if (sceneId && roleSceneKind) {
      return { kind: roleSceneKind, scene_id: sceneId };
    }
  }
  if (kind === "prop_ref" || kind === "prop") {
    if (typeof meta.prop_id === "string") {
      return { kind: "prop_ref", prop_id: meta.prop_id };
    }
  }
  return undefined;
}

/** 把推断出的(部分)目标补全为可推送的完整目标;不完整则返回 null。 */
export function completeTarget(
  partial: (Partial<PushTarget> & { kind: PushTargetKind }) | undefined,
): PushTarget | null {
  if (!partial) return null;
  if (
    partial.kind === "frame" ||
    partial.kind === "sketch" ||
    partial.kind === "director_render" ||
    partial.kind === "selected_background" ||
    partial.kind === "video" ||
    partial.kind === "beat_audio"
  ) {
    if (typeof partial.episode === "number" && typeof partial.beat === "number") {
      return { kind: partial.kind, episode: partial.episode, beat: partial.beat };
    }
    return null;
  }
  if (
    partial.kind === "identity" ||
    partial.kind === "identity_costume" ||
    partial.kind === "identity_portrait"
  ) {
    if (
      typeof partial.character === "string" &&
      typeof partial.identity_id === "string" &&
      partial.identity_id
    ) {
      return {
        kind: partial.kind,
        character: partial.character,
        identity_id: partial.identity_id,
      };
    }
    return null;
  }
  if (partial.kind === "portrait") {
    if (typeof partial.character === "string") {
      return { kind: "portrait", character: partial.character };
    }
    return null;
  }
  const sceneKind = normalizeScenePushTargetKind(partial.kind);
  if (sceneKind) {
    const value = partial as Record<string, unknown>;
    if (typeof value.scene_id === "string" && value.scene_id) {
      return { kind: sceneKind, scene_id: value.scene_id } as PushTarget;
    }
    return null;
  }
  if (partial.kind === "prop_ref") {
    if (typeof partial.prop_id === "string" && partial.prop_id) {
      return { kind: "prop_ref", prop_id: partial.prop_id };
    }
    return null;
  }
  return null;
}

/**
 * 把侧栏资产的 source 解析为可推送的完整目标。
 * 优先用后端直接给出的 `slot_target`(canonical,免去前端按 kind/role 猜),
 * 校验通过即用;缺失或非法时再回退到本地 role/kind 推断。
 */
export function assetToPushTarget(
  source: Record<string, unknown> | undefined,
): PushTarget | null {
  const backendTarget = (source as { slot_target?: unknown } | undefined)?.slot_target;
  const coerced = coerceSlotTarget(backendTarget);
  if (coerced) {
    return coerced;
  }
  return completeTarget(inferDefaultTarget(source as FreezoneSource | undefined));
}
