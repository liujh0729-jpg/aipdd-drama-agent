// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 渠道名旁的「打开官网」直达链接：设置面板三处渠道渲染点
// （渠道连接卡 / 高级配置渠道行 / 渠道管理目录弹窗）共用同一组件，URL 真源在
// @/lib/provider-labels 的 PROVIDER_OFFICIAL_SITES。没登记官网的渠道直接不渲染，
// 避免出现一个点开是空地址的死图标。

import { useTranslation } from "react-i18next";
import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { providerOfficialSiteUrl } from "@/lib/provider-labels";

function ProviderOfficialSiteLink({
  provider,
  brand,
  className,
}: {
  provider: string;
  /** 无障碍名称里的渠道名：调用方给当前界面语言下的展示名，缺省回落 provider id。 */
  brand?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const url = providerOfficialSiteUrl(provider);
  if (!url) return null;
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      // title 直接把官网地址摊给用户：想知道「官网到底是哪个域名」不用先点开。
      title={url}
      aria-label={t("settings.modelConfig.providerOfficialSite", {
        brand: brand || provider,
      })}
      className={cn(
        "inline-flex shrink-0 items-center text-muted-foreground transition-colors hover:text-foreground",
        className,
      )}
    >
      <ExternalLink className="size-3" aria-hidden />
    </a>
  );
}

export { ProviderOfficialSiteLink };
