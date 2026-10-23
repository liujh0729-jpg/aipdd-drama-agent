// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 自 settings-dialog.tsx 物理拆分（纯移动，DOM/样式/i18n 不变）。
// 职责：本地网关（LocalAPI）只读状态面板。
//
// [aipdd fork delta] 网关初始化改为纯内部调用：后端启动时自动建 root、签发中继 Key
// 并激活 custom（见 model_gateway/_common.bootstrap_local_gateway）。这里不再提供任何
// 用户名/密码输入与「初始化」按钮——用户只在外层首次登录页建一次应用超管，永远不接
// 触网关账号。面板只回显就绪状态。

import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import type { ModelGatewayConfig } from "@/lib/queries/model-gateway";

function CustomGatewayPanel({
  config,
}: {
  config: ModelGatewayConfig | undefined;
}) {
  const { t } = useTranslation();
  const databaseStatus = config?.provisioner?.database;
  const databaseReady = Boolean(databaseStatus?.available);
  const customConfigured = Boolean(config?.custom?.configured);

  return (
    <div className="space-y-3">
      <p className="text-xs leading-relaxed text-muted-foreground">
        {t("settings.modelConfig.custom.description")}
      </p>

      <div className="rounded-md border border-border/70 p-3">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            {t("settings.modelConfig.custom.localStatusTitle")}
          </p>
          <span
            className={cn(
              "text-[11px]",
              customConfigured ? "text-emerald-400" : "text-amber-300",
            )}
          >
            {customConfigured
              ? t("settings.modelConfig.custom.localReady")
              : t("settings.modelConfig.custom.autoInitializing")}
          </span>
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted-foreground">
          {databaseReady
            ? t("settings.modelConfig.custom.sqliteReady")
            : t("settings.modelConfig.custom.sqliteWaiting")}
        </p>
      </div>
    </div>
  );
}

export {
  CustomGatewayPanel,
};
