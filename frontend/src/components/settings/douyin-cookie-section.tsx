// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 设置 → 抖音 Cookie：抖音文案提取「纯 HTTP 主链路」的登录态凭据。
//
// 安全口径（与后端一致，别在这里打折扣）：
// - Cookie 是**完整登录态**，不是可局部掩码的 API Key。后端状态接口只回
//   条数 / Cookie 名清单 / 长度，**永不回传原文**；所以输入框不做「回填」，
//   保存成功后还要把本地内容清空，不让凭据残留在 DOM 里。
// - 未配置不是错误态：提取链路会自动回落宿主机 Chrome CDP，因此这里用中性
//   提示而不是像别的凭据那样弹琥珀色告警。
// [aipdd fork delta req45] 「首次处理后自动记录」现在真的落地了：提取走 Chrome 兜底
// 成功一次，后端就把那份抖音登录态写进设置库；本页另给「从 Chrome 抓取」的显式按钮。
// 抓不到时后端回 400 + 成因（端口没开 / 那个 Chrome 没登录抖音），原样贴出即可操作。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Download, Loader2, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  useCaptureDouyinCookie,
  useModelGatewayConfig,
  useSaveDouyinCookie,
} from "@/lib/queries/model-gateway";
import { cn } from "@/lib/utils";

const CHIP_CLASS = "rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium";

export function DouyinCookieSection() {
  const { t } = useTranslation();
  const statusQuery = useModelGatewayConfig();
  const saveCookie = useSaveDouyinCookie();
  // [aipdd fork delta req45] 显式从宿主 Chrome 抓登录态（与提取链路的自动记录同一条通路）。
  const captureCookie = useCaptureDouyinCookie();

  const status = statusQuery.data?.data?.douyinCookie;
  const [cookie, setCookie] = useState("");
  // 保存中锁重复提交；输入框本就从不回填后端值（也回不到），所以无需 seed 标记。
  // 抓取也归进 busy：两个写动作共指同一份凭据，不锁住会互相覆盖。
  const busy = saveCookie.isPending || captureCookie.isPending;

  const configured = status?.configured ?? false;
  const names = status?.cookieNames ?? [];

  const handleSave = () => {
    const value = cookie.trim();
    if (!value) return;
    saveCookie.mutate(
      { cookie: value },
      {
        onSuccess: () => {
          // 关键：抹掉刚提交的凭据，不留存在页面输入框/受控状态里。
          setCookie("");
          toast.success(t("settings.douyinCookie.saved"));
        },
        onError: (err) =>
          toast.error(
            err instanceof Error ? err.message : t("settings.douyinCookie.saveFailed"),
          ),
      },
    );
  };

  const handleClear = () => {
    saveCookie.mutate(
      { cookie: "", clear: true },
      {
        onSuccess: () => {
          setCookie("");
          toast.success(t("settings.douyinCookie.resetDone"));
        },
        onError: (err) =>
          toast.error(
            err instanceof Error ? err.message : t("settings.douyinCookie.saveFailed"),
          ),
      },
    );
  };

  const handleCapture = () => {
    captureCookie.mutate(undefined, {
      onSuccess: (resp) => {
        // 抓取条数取自响应（接口出错分支下退到 0）：状态接口本身已被 invalidate，
        // 这里只是让提示落在「本次真的记下来几条」上。
        const count = "data" in resp ? (resp.data?.cookieCount ?? 0) : 0;
        toast.success(t("settings.douyinCookie.captureDone", { count }));
      },
      // 后端把「为什么抓不到」写在 400 的 detail 里（调试端口没开 / 那个 Chrome
      // 未登录抖音），直接贴出来就是最可操作的指引，不该再换成一句笼统的失败文案。
      onError: (err) =>
        toast.error(
          err instanceof Error && err.message
            ? err.message
            : t("settings.douyinCookie.captureFailed"),
          { duration: 9000 },
        ),
    });
  };

  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 rounded-full",
            configured ? "bg-emerald-400" : "bg-muted-foreground/50",
          )}
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.douyinCookie.title")}
        </h3>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.douyinCookie.description")}
      </p>

      {statusQuery.error ? (
        <div className="mt-6 flex items-center gap-3 text-sm text-muted-foreground">
          <span>{statusQuery.error.message || t("settings.douyinCookie.loadFailed")}</span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => void statusQuery.refetch()}
          >
            {t("common.retry")}
          </Button>
        </div>
      ) : !status ? (
        <div className="mt-6 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" aria-hidden />
          {t("settings.douyinCookie.loading")}
        </div>
      ) : (
        <>
          <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
            <span>
              {t("settings.douyinCookie.statusLabel")}
              {": "}
            </span>
            {configured ? (
              <>
                <span className={cn(CHIP_CLASS, "text-emerald-300")}>
                  {t("settings.douyinCookie.statusSummary", {
                    count: status.cookieCount,
                    length: status.length,
                  })}
                </span>
                {names.length > 0 ? (
                  <span className="flex flex-wrap items-center gap-1">
                    {names.map((name) => (
                      <span key={name} className={cn(CHIP_CLASS, "text-muted-foreground")}>
                        {name}
                      </span>
                    ))}
                  </span>
                ) : null}
              </>
            ) : (
              <span className="text-muted-foreground">
                {t("settings.douyinCookie.statusUnset")}
              </span>
            )}
          </p>

          <div className="mt-4 space-y-3 rounded-lg border border-border bg-white/[0.02] p-4">
            <div className="grid grid-cols-[120px_1fr] items-start gap-3">
              <Label
                htmlFor="douyin-cookie-input"
                className="mt-1.5 justify-start text-[11px] font-normal tracking-wide text-muted-foreground uppercase"
              >
                {t("settings.douyinCookie.fieldLabel")}
              </Label>
              <div className="space-y-1">
                <Textarea
                  id="douyin-cookie-input"
                  value={cookie}
                  rows={4}
                  spellCheck={false}
                  autoComplete="off"
                  placeholder={t("settings.douyinCookie.placeholder")}
                  className="font-mono text-[11px]"
                  onChange={(event) => setCookie(event.target.value)}
                />
                <p className="text-[11px] text-muted-foreground/80">
                  {t("settings.douyinCookie.fieldHint")}
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {/* [aipdd fork delta req45] 显式从那个已登录抖音的宿主 Chrome 抓凭据：
                  用户不必再回 DevTools 里手工复制整串 Cookie。 */}
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={handleCapture}
              >
                {captureCookie.isPending ? (
                  <Loader2 className="size-3.5 animate-spin" aria-hidden />
                ) : (
                  <Download className="size-3.5" aria-hidden />
                )}
                {captureCookie.isPending
                  ? t("settings.douyinCookie.capturing")
                  : t("settings.douyinCookie.captureFromChrome")}
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={!cookie.trim() || busy}
                onClick={handleSave}
              >
                {saveCookie.isPending ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : null}
                {t("settings.douyinCookie.save")}
              </Button>
              {configured ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="text-muted-foreground"
                  disabled={busy}
                  onClick={handleClear}
                >
                  <RotateCcw className="size-3.5" aria-hidden />
                  {t("settings.douyinCookie.reset")}
                </Button>
              ) : null}
              <span className="text-[11px] text-muted-foreground">
                {t("settings.douyinCookie.securityNote")}
              </span>
            </div>
          </div>

          <div className="mt-4 space-y-1.5 text-xs leading-relaxed text-muted-foreground">
            <p>{t("settings.douyinCookie.usageHttp")}</p>
            <p>{t("settings.douyinCookie.usageCdp")}</p>
            {/* [aipdd fork delta req45] 自动记录是这条需求的主路；按钮只是补上显式动作。 */}
            <p>{t("settings.douyinCookie.autoRecord")}</p>
          </div>
        </>
      )}
    </section>
  );
}
