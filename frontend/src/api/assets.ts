// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { apiCall } from "./client";

export interface CreateIdentityAssetPayload {
  source_url: string;
  character: string;
  identity_name: string;
  appearance_details?: string;
  face_prompt?: string;
  age_group?: string;
}

export interface CreateIdentityAssetResult {
  character: string;
  identity_id: string;
  identity_name: string;
  target_path: string;
  target_url: string;
}

export async function createIdentityAsset(
  projectId: string,
  payload: CreateIdentityAssetPayload,
): Promise<CreateIdentityAssetResult> {
  return await apiCall<CreateIdentityAssetResult>(
    `projects/${encodeURIComponent(projectId)}/freezone/assets/identities`,
    { method: "POST", json: payload },
  );
}
