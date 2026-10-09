// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { Link2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { buildAssetShareUrl } from "@/hooks/use-assets-deep-link";
import type { AssetRefType } from "@/lib/queries/asset-references";

/**
 * Copies a `?type=&id=` deep link to the asset so it can be pasted into chat /
 * docs and re-opened straight to this asset.
 */
export function CopyAssetLinkButton({
  type,
  id,
  className,
}: {
  type: AssetRefType;
  id: string;
  className?: string;
}) {
  const { t } = useTranslation();

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(buildAssetShareUrl(type, id));
      toast.success(t("assets.common.linkCopied"));
    } catch {
      toast.error(t("common.error"));
    }
  }

  return (
    <Button
      type="button"
      size="icon-sm"
      variant="ghost"
      onClick={handleCopy}
      aria-label={t("assets.common.copyLink")}
      title={t("assets.common.copyLink")}
      className={className}
    >
      <Link2 className="size-3.5" />
    </Button>
  );
}
