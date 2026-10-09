// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta req47] Seedance 私域素材库 / 真人活体认证的查询层。
//
// 关键口径：素材库**不是「连了 NewAPI 就有」**。状态接口回的是探测结论
// （supported / unsupported / unauthorized / ambiguous / unconfigured / unreachable），
// 面板与这些查询都以探测结论为前提——不支持时连列表请求都不该发出去，
// 免得用户看到一排「未知错误」，而真正的答案只有一句「这台网关没有这组接口」。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { queryKeys } from "@/lib/query-keys";
import type { OkResponse } from "@/types/api";

export type SeedanceAssetLibraryState =
  | "supported"
  | "unsupported"
  | "unauthorized"
  | "ambiguous"
  | "unconfigured"
  | "unreachable";

export type SeedanceAssetGroupType = "AIGC" | "LivenessFace";
export type SeedanceAssetType = "Image" | "Video" | "Audio";
export type SeedanceAssetStatus = "Processing" | "Active" | "Failed";

export interface SeedanceAssetLibraryStatus {
  supported: boolean;
  state: SeedanceAssetLibraryState;
  reason: string;
  baseUrl: string;
  credentialSource: string;
  keyPreview: string;
  httpStatus: number;
  upstreamCode: string;
  upstreamMessage: string;
  warning: string;
  checkedAt: string;
  cached: boolean;
}

export interface SeedanceAssetGroup {
  Id: string;
  Name?: string;
  Description?: string;
  GroupType?: SeedanceAssetGroupType;
  ProjectName?: string;
  CreateTime?: string;
  UpdateTime?: string;
}

export interface SeedanceAsset {
  Id: string;
  Name?: string;
  URL?: string;
  GroupId?: string;
  AssetType?: SeedanceAssetType;
  Status?: SeedanceAssetStatus;
  Moderation?: { Strategy?: string };
  Error?: { Code?: string; Message?: string };
  ProjectName?: string;
  CreateTime?: string;
  UpdateTime?: string;
}

export interface SeedanceAssetGroupList {
  Items?: SeedanceAssetGroup[];
  NextToken?: string;
  TotalCount?: number;
  PageNumber?: number;
  PageSize?: number;
}

export interface SeedanceAssetList {
  Items?: SeedanceAsset[];
  NextToken?: string;
  TotalCount?: number;
}

export interface SeedanceValidateSession {
  BytedToken: string;
  H5Link: string;
  callbackUrl?: string;
}

export interface SeedanceGroupListParams {
  groupType: SeedanceAssetGroupType;
  name?: string;
}

export interface SeedanceAssetListParams {
  groupType: SeedanceAssetGroupType;
  groupIds?: string[];
  statuses?: SeedanceAssetStatus[];
}

export interface CreateSeedanceAssetInput {
  groupId: string;
  assetType: SeedanceAssetType;
  url: string;
  name?: string;
}

export interface CreateSeedanceValidateSessionInput {
  callbackUrl?: string;
}

export function useSeedanceAssetLibraryStatus() {
  return useQuery({
    queryKey: queryKeys.seedanceAssetLibrary(),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/seedance-assets/status", { signal, retry: 0 })
        .json<OkResponse<SeedanceAssetLibraryStatus>>(),
    // 探测要打一次上游只读请求；一分钟新鲜度够面板用，手动「重新探测」走 refresh 接口。
    staleTime: 60_000,
    retry: false,
  });
}

export function useSeedanceAssetGroups(params: SeedanceGroupListParams, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.seedanceAssetGroups(params.groupType, params.name ?? ""),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/seedance-assets/groups", {
          signal,
          searchParams: {
            groupType: params.groupType,
            name: params.name ?? "",
            maxResults: 100,
          },
          retry: 0,
        })
        .json<OkResponse<SeedanceAssetGroupList>>(),
    enabled,
    staleTime: 15_000,
    retry: false,
  });
}

export function useSeedanceAssets(params: SeedanceAssetListParams, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.seedanceAssets(
      params.groupType,
      (params.groupIds ?? []).join(","),
      (params.statuses ?? []).join(","),
    ),
    queryFn: ({ signal }) =>
      api
        .get("api/v1/model-gateway/seedance-assets/assets", {
          signal,
          searchParams: {
            groupType: params.groupType,
            groupIds: (params.groupIds ?? []).join(","),
            statuses: (params.statuses ?? []).join(","),
            maxResults: 100,
          },
          retry: 0,
        })
        .json<OkResponse<SeedanceAssetList>>(),
    enabled: enabled && (params.groupIds?.length ?? 0) > 0,
    staleTime: 15_000,
    retry: false,
  });
}

export function useCreateSeedanceAsset() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: CreateSeedanceAssetInput) =>
      (
        await api
          .post("api/v1/model-gateway/seedance-assets/assets", {
            json: {
              groupId: input.groupId,
              assetType: input.assetType,
              url: input.url,
              name: input.name ?? "",
            },
            retry: 0,
          })
          .json<OkResponse<{ Id: string }>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["seedance-assets", "assets"] });
    },
  });
}

export function useCreateSeedanceValidateSession() {
  return useMutation({
    mutationFn: async (input: CreateSeedanceValidateSessionInput) =>
      (
        await api
          .post("api/v1/model-gateway/seedance-assets/validate-sessions", {
            json: { callbackUrl: input.callbackUrl ?? "" },
            retry: 0,
          })
          .json<OkResponse<SeedanceValidateSession>>()
      ).data,
  });
}

export function useSeedanceValidateResult() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (token: string) =>
      (
        await api
          .get("api/v1/model-gateway/seedance-assets/validate-results", {
            searchParams: { bytedToken: token },
            retry: 0,
          })
          .json<OkResponse<{ GroupId: string }>>()
      ).data,
    // 认证完成后会多出一个真人组：把组列表与探测结论一起作废，面板立刻能看到新组。
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["seedance-assets"] });
    },
  });
}

export function useRefreshSeedanceAssetProbe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      (
        await api
          .post("api/v1/model-gateway/seedance-assets/probe/refresh", { retry: 0 })
          .json<OkResponse<SeedanceAssetLibraryStatus>>()
      ).data,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["seedance-assets"] });
    },
  });
}
