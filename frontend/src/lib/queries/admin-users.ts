// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] CE 多用户：超管的账户管理 + 首次初始化。
// 后端端点见 src/novelvideo/api/routes/admin_users.py 与 routes/auth.py。
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { jsonWithBackendError } from "@/lib/api-errors";
import { queryKeys } from "@/lib/query-keys";
import type { ErrorResponse, OkResponse } from "@/types/api";

export type AdminUserRole = "owner" | "member";
export type AdminUserStatus = "active" | "suspended";

export interface AdminUserRecord {
  id: string;
  username: string;
  role: AdminUserRole;
  status: AdminUserStatus;
  created_at: string;
  updated_at: string;
  /** 是否为当前登录账户：UI 用它锁掉"自己降级/禁用"这类自锁操作。 */
  is_self: boolean;
}

export interface SetupStatus {
  needs_setup: boolean;
  /** ST_CE_MULTI_USER 是否开启；单用户模式下“用户管理”页没有意义。 */
  multi_user: boolean;
}

export interface AdminUserCreateInput {
  username: string;
  password: string;
  role: AdminUserRole;
}

export interface AdminUserPatchInput {
  userId: string;
  role?: AdminUserRole;
  status?: AdminUserStatus;
}

/** 首次初始化探测：登录页进场用，不需要登录态。 */
export function useSetupStatus(enabled = true) {
  return useQuery({
    queryKey: queryKeys.setupStatus(),
    queryFn: () =>
      jsonWithBackendError<OkResponse<SetupStatus>>(
        api.get("api/v1/auth/setup-status", { throwHttpErrors: false }),
      ),
    enabled,
    staleTime: 30_000,
    retry: 1,
  });
}

export function useAdminUsers(enabled = true) {
  return useQuery({
    queryKey: queryKeys.adminUsers(),
    queryFn: () =>
      jsonWithBackendError<OkResponse<AdminUserRecord[]>>(
        api.get("api/v1/admin/users", { throwHttpErrors: false }),
      ),
    enabled,
  });
}

export function useCreateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: AdminUserCreateInput) =>
      jsonWithBackendError<OkResponse<AdminUserRecord> | ErrorResponse>(
        api.post("api/v1/admin/users", { json: input, throwHttpErrors: false }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.adminUsers() });
    },
  });
}

export function useUpdateAdminUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, ...body }: AdminUserPatchInput) =>
      jsonWithBackendError<OkResponse<AdminUserRecord> | ErrorResponse>(
        api.patch(`api/v1/admin/users/${encodeURIComponent(userId)}`, {
          json: body,
          throwHttpErrors: false,
        }),
      ),
    onSuccess: () => {
      // 角色/状态都会反映到 /auth/me（超管改自己所在系统的账户时要立刻重取）
      qc.invalidateQueries({ queryKey: queryKeys.adminUsers() });
      qc.invalidateQueries({ queryKey: queryKeys.currentUser() });
    },
  });
}

export function useResetAdminUserPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, password }: { userId: string; password: string }) =>
      jsonWithBackendError<
        OkResponse<{ revoked_sessions: number; is_self: boolean }> | ErrorResponse
      >(
        api.post(
          `api/v1/admin/users/${encodeURIComponent(userId)}/password`,
          { json: { password }, throwHttpErrors: false },
        ),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.adminUsers() });
    },
  });
}

// [aipdd fork delta] 本人自助改密：校验旧口令、改后 DB 权威、保留当前会话。
// 后端 body 为 snake_case（old_password / new_password）。
export interface ChangePasswordInput {
  oldPassword: string;
  newPassword: string;
}

export function useChangePassword() {
  return useMutation({
    mutationFn: ({ oldPassword, newPassword }: ChangePasswordInput) =>
      jsonWithBackendError<OkResponse<{ ok: boolean }> | ErrorResponse>(
        api.post("api/v1/auth/change-password", {
          json: { old_password: oldPassword, new_password: newPassword },
          throwHttpErrors: false,
        }),
      ),
  });
}
