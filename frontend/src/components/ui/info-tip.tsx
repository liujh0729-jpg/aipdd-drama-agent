// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 术语提示：一枚 ℹ️ 小图标，鼠标悬停弹出说明。用于设置里专业
// 术语密集的字段旁（base_url / 渠道 / bucket / endpoint / TTL / embedding 等），
// 复用既有 @base-ui Tooltip 原语。说明文案走 i18n，由调用方传入已解析的字符串；
// 自身带 TooltipProvider，可随处直接嵌入，无需上层统一挂载。
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Info } from "lucide-react";

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

function InfoTip({
  text,
  side = "top",
  className,
}: {
  /** 已解析的说明文案（调用方 t(...) 的结果）；为空则整体不渲染。 */
  text?: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
}) {
  const { t } = useTranslation();
  if (!text) return null;
  return (
    <TooltipProvider delay={150}>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              type="button"
              aria-label={t("common.termsHintAriaLabel")}
              // 仅作提示、不可点触发任何动作：拦截默认行为避免误提交所在表单。
              onClick={(event) => event.preventDefault()}
              className={cn(
                "ml-1 inline-flex size-4 shrink-0 translate-y-0.5 items-center justify-center rounded-full align-middle text-muted-foreground/70 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring/50",
                className,
              )}
            />
          }
        >
          <Info className="size-3.5" aria-hidden />
        </TooltipTrigger>
        <TooltipContent
          side={side}
          showArrow={false}
          className="max-w-[16rem] items-start whitespace-normal text-left leading-relaxed"
        >
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export { InfoTip };
