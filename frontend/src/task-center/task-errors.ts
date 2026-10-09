// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import type { TFunction } from "i18next";
import {
  backendErrorCodeToastMessage,
  humanizeTaskError,
} from "@/lib/api-errors";
import type { TaskState } from "./types";

export function taskErrorMessage(task: TaskState, t: TFunction): string {
  const localized = backendErrorCodeToastMessage(
    task.error_code,
    task.error || t("common.error"),
    t,
  );
  if (localized) return localized;
  // [aipdd fork delta] WP-D: 积分体系拆除后，任务失败里真正属于“额度”的那一类
  // 是上游账户配额跑光（error_code = UPSTREAM_QUOTA_EXCEEDED）。
  if (task.error_code === "UPSTREAM_QUOTA_EXCEEDED") {
    return t("common.upstreamQuotaExceeded", {
      defaultValue: task.error || t("common.error"),
    });
  }
  if (task.error_code === "BILLING_RULE_NOT_CONFIGURED") {
    return t("common.billingRuleNotConfigured", {
      defaultValue: task.error || t("common.error"),
    });
  }
  return humanizeTaskError(task.error, t);
}
