// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta req47] 设置 → Seedance 素材库（私域素材 + 真人活体认证）。
//
// 面板的存在理由就一条口径：**素材库只随「支持素材库的那台 NewAPI」提供**，不是连了
// NewAPI 就有。所以进场先读探测结论，不支持时：横幅说清为什么、发出去的请求一个都不加，
// 所有写操作（登记素材、起真人认证）禁用——宁可灰着，也不让用户去撞一个上游根本没挂的路由。
//
// 上游返回的错误原文（含 ResponseMetadata.Error 的 Code）一律直接贴出，不改写成笼统失败：
// 「无效的令牌」和「该账号未开通此模型」是两件完全不同的要修的事。

import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ExternalLink, Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { getRequestErrorMessage } from "@/features/settings/model-gateway";
import {
  useCreateSeedanceAsset,
  useCreateSeedanceValidateSession,
  useRefreshSeedanceAssetProbe,
  useSeedanceAssetGroups,
  useSeedanceAssetLibraryStatus,
  useSeedanceAssets,
  useSeedanceValidateResult,
  type SeedanceAssetGroupType,
  type SeedanceAssetStatus,
  type SeedanceAssetType,
} from "@/lib/queries/seedance-assets";
import { cn } from "@/lib/utils";

const GROUP_TYPES: SeedanceAssetGroupType[] = ["AIGC", "LivenessFace"];
const ASSET_TYPES: SeedanceAssetType[] = ["Image", "Video", "Audio"];

// 状态点颜色：绿=可用，琥珀=待确认（认证没做完 / Key 被拒 / 判不准），灰=未配置或不支持。
const STATUS_DOT_CLASS: Record<string, string> = {
  supported: "bg-emerald-400",
  unauthorized: "bg-amber-400",
  ambiguous: "bg-amber-400",
  unreachable: "bg-amber-400",
  unsupported: "bg-muted-foreground/50",
  unconfigured: "bg-muted-foreground/50",
};

const ASSET_STATUS_CLASS: Record<SeedanceAssetStatus, string> = {
  Processing: "bg-amber-400/10 text-amber-400",
  Active: "bg-emerald-400/10 text-emerald-400",
  Failed: "bg-red-400/10 text-red-400",
};

const CHIP_CLASS = "rounded px-1.5 py-0.5 text-xs font-medium";

function SeedanceAssetLibrarySection() {
  const { t } = useTranslation();
  const statusQuery = useSeedanceAssetLibraryStatus();
  const status = statusQuery.data?.data;
  const state = status?.state ?? "unconfigured";
  const supported = Boolean(status?.supported);

  const refreshProbe = useRefreshSeedanceAssetProbe();
  const [groupType, setGroupType] = useState<SeedanceAssetGroupType>("AIGC");
  const [pickedGroupId, setPickedGroupId] = useState("");

  const groupsQuery = useSeedanceAssetGroups({ groupType }, supported);
  const groups = groupsQuery.data?.data?.Items ?? [];
  // 选中项在刷新后可能不存在了（真人组刚认证完才多出来），回落到首条而不是留个空选。
  const activeGroupId = useMemo(() => {
    if (groups.some((group) => group.Id === pickedGroupId)) return pickedGroupId;
    return groups[0]?.Id ?? "";
  }, [groups, pickedGroupId]);

  const assetsQuery = useSeedanceAssets(
    { groupType, groupIds: activeGroupId ? [activeGroupId] : [] },
    supported,
  );
  const assets = assetsQuery.data?.data?.Items ?? [];

  const createAsset = useCreateSeedanceAsset();
  const createSession = useCreateSeedanceValidateSession();
  const queryValidateResult = useSeedanceValidateResult();

  const [assetUrl, setAssetUrl] = useState("");
  const [assetType, setAssetType] = useState<SeedanceAssetType>("Image");
  const [assetName, setAssetName] = useState("");
  const [callbackUrl, setCallbackUrl] = useState("");
  const [session, setSession] = useState<{ BytedToken: string; H5Link: string } | null>(
    null,
  );

  const reportError = async (error: unknown, fallbackKey: string) => {
    toast.error(await getRequestErrorMessage(error, t(fallbackKey)), { duration: 9000 });
  };

  const handleRefreshProbe = () => {
    refreshProbe.mutate(undefined, {
      onSuccess: (data) => {
        toast.success(
          data.supported
            ? t("settings.seedanceAssets.probe.ready")
            : t("settings.seedanceAssets.probe.notReady"),
        );
      },
      onError: (error) => void reportError(error, "settings.seedanceAssets.probe.failed"),
    });
  };

  const handleCreateAsset = () => {
    const url = assetUrl.trim();
    if (!activeGroupId) {
      toast.error(t("settings.seedanceAssets.form.groupRequired"));
      return;
    }
    if (!url) {
      toast.error(t("settings.seedanceAssets.form.urlRequired"));
      return;
    }
    createAsset.mutate(
      { groupId: activeGroupId, assetType, url, name: assetName.trim() },
      {
        onSuccess: (data) => {
          setAssetUrl("");
          setAssetName("");
          toast.success(t("settings.seedanceAssets.form.submitted", { id: data.Id }));
        },
        onError: (error) => void reportError(error, "settings.seedanceAssets.form.failed"),
      },
    );
  };

  const handleCreateSession = () => {
    createSession.mutate(
      { callbackUrl: callbackUrl.trim() },
      {
        onSuccess: (data) => {
          setSession({ BytedToken: data.BytedToken, H5Link: data.H5Link });
          toast.success(t("settings.seedanceAssets.face.created"));
        },
        onError: (error) => void reportError(error, "settings.seedanceAssets.face.failed"),
      },
    );
  };

  const handleQueryResult = () => {
    if (!session?.BytedToken) return;
    queryValidateResult.mutate(session.BytedToken, {
      onSuccess: (data) => {
        toast.success(t("settings.seedanceAssets.face.verified", { groupId: data.GroupId }));
        setSession(null);
        setGroupType("LivenessFace");
      },
      // 没做完也正常：本人还没在手机上完成活体，上游就是查不到结果。原文贴出来，
      // 用户知道是「还没做完」而不是「坏了」。
      onError: (error) => void reportError(error, "settings.seedanceAssets.face.pending"),
    });
  };

  // [aipdd fork delta req47] 把库内素材折成生成页能直接用的引用。递的是 ``asset://{Id}``
  // 而不是列表里的 URL：那个直链是临时的（约 12 小时过期），而引用指的就是库里那份已过审的素材。
  const handleCopyReference = async (assetId: string) => {
    const reference = `asset://${assetId}`;
    try {
      await navigator.clipboard.writeText(reference);
      toast.success(t("settings.seedanceAssets.assets.copied", { ref: reference }));
    } catch {
      // 剪贴板不可用（非安全上下文 / 浏览器拒权）就明说手动复制，不假装成功。
      toast.error(t("settings.seedanceAssets.assets.copyFailed"));
    }
  };

  const busy =
    createAsset.isPending ||
    createSession.isPending ||
    queryValidateResult.isPending ||
    refreshProbe.isPending;

  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 rounded-full",
            STATUS_DOT_CLASS[state] ?? "bg-muted-foreground/50",
          )}
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.seedanceAssets.title")}
        </h3>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="ml-auto h-8 gap-1.5 text-xs"
          onClick={handleRefreshProbe}
          disabled={busy}
        >
          {refreshProbe.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <RefreshCw className="size-3.5" />
          )}
          {t("settings.seedanceAssets.probe.refresh")}
        </Button>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.seedanceAssets.description")}
      </p>

      {/* 能力横幅：这是整页的第一优先级信息，不支持时下面所有东西都不该出现。 */}
      <div
        className={cn(
          "mt-3 rounded-md border px-3 py-2.5 text-xs leading-relaxed",
          supported
            ? "border-emerald-500/25 bg-emerald-500/[0.06] text-muted-foreground"
            : "border-amber-500/25 bg-amber-500/[0.06] text-muted-foreground",
        )}
      >
        <p className="font-medium text-foreground">
          {t(`settings.seedanceAssets.state.${state}`)}
        </p>
        {status?.reason ? <p className="mt-1.5">{status.reason}</p> : null}
        {status?.upstreamMessage ? (
          <p className="mt-1.5">
            {t("settings.seedanceAssets.upstreamError")}
            {status.upstreamCode ? `[${status.upstreamCode}] ` : ""}
            {status.upstreamMessage}
          </p>
        ) : null}
        {status?.warning ? (
          <p className="mt-1.5">{t("settings.seedanceAssets.warning")}：{status.warning}</p>
        ) : null}
        {status?.baseUrl ? (
          <p className="mt-1.5">
            {t("settings.seedanceAssets.target")}：{status.baseUrl}
            {status.credentialSource ? ` · ${status.credentialSource}` : ""}
            {status.keyPreview ? ` · ${status.keyPreview}` : ""}
          </p>
        ) : null}
      </div>

      {!supported ? null : (
        <>
          <div className="mt-4">
            <Tabs
              value={groupType}
              onValueChange={(value) => setGroupType(value as SeedanceAssetGroupType)}
            >
              <TabsList>
                {GROUP_TYPES.map((type) => (
                  <TabsTrigger key={type} value={type}>
                    {t(`settings.seedanceAssets.groupType.${type}`)}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          </div>

          <div className="mt-3 grid gap-2">
            <Label htmlFor="seedance-asset-group-url" className="text-sm font-medium">
              {t("settings.seedanceAssets.groups.title")}
            </Label>
            {groupsQuery.isLoading ? (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("settings.seedanceAssets.groups.loading")}
              </p>
            ) : groups.length === 0 ? (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("settings.seedanceAssets.groups.empty")}
              </p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {groups.map((group) => (
                  <button
                    key={group.Id}
                    type="button"
                    onClick={() => setPickedGroupId(group.Id)}
                    aria-pressed={group.Id === activeGroupId}
                    className={cn(
                      "rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors",
                      group.Id === activeGroupId
                        ? "border-white/25 bg-white/[0.09] text-foreground"
                        : "border-border/60 bg-white/[0.02] text-muted-foreground hover:bg-white/[0.05]",
                    )}
                  >
                    <span className="block max-w-[220px] truncate font-medium">
                      {group.Name || group.Id}
                    </span>
                    <span className="mt-0.5 block truncate text-muted-foreground">
                      {group.Id}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 登记素材：只接受公网直链。本地图要先过媒体中转拿直链（见「媒体存储」页）。 */}
          <div className="mt-5 grid gap-2.5">
            <Label htmlFor="seedance-asset-url" className="text-sm font-medium">
              {t("settings.seedanceAssets.form.title")}
            </Label>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {t("settings.seedanceAssets.form.description")}
            </p>
            <Input
              id="seedance-asset-url"
              name="seedance-asset-url"
              value={assetUrl}
              placeholder={t("settings.seedanceAssets.form.urlPlaceholder")}
              onChange={(event) => setAssetUrl(event.target.value)}
              className="h-8"
            />
            <div className="flex items-center gap-2">
              <Select
                value={assetType}
                onValueChange={(value) => setAssetType(value as SeedanceAssetType)}
              >
                <SelectTrigger size="sm" className="h-8 w-[140px]">
                  <SelectValue placeholder={t("settings.seedanceAssets.form.typeLabel")} />
                </SelectTrigger>
                <SelectContent alignItemWithTrigger={false}>
                  {ASSET_TYPES.map((type) => (
                    <SelectItem key={type} value={type}>
                      {t(`settings.seedanceAssets.assetType.${type}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                name="seedance-asset-name"
                value={assetName}
                placeholder={t("settings.seedanceAssets.form.namePlaceholder")}
                onChange={(event) => setAssetName(event.target.value)}
                className="h-8 flex-1"
              />
              <Button
                type="button"
                size="sm"
                className="h-8 shrink-0"
                onClick={handleCreateAsset}
                disabled={busy || !activeGroupId}
              >
                {createAsset.isPending ? (
                  <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                ) : null}
                {t("settings.seedanceAssets.form.submit")}
              </Button>
            </div>
          </div>

          <div className="mt-5 grid gap-2">
            <Label className="text-sm font-medium">
              {t("settings.seedanceAssets.assets.title")}
            </Label>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {t("settings.seedanceAssets.assets.useHint")}
            </p>
            {!activeGroupId ? (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("settings.seedanceAssets.assets.pickGroup")}
              </p>
            ) : assetsQuery.isLoading ? (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("settings.seedanceAssets.assets.loading")}
              </p>
            ) : assets.length === 0 ? (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("settings.seedanceAssets.assets.empty")}
              </p>
            ) : (
              <ul className="grid gap-1.5">
                {assets.map((asset) => {
                  const assetStatus = asset.Status ?? "Processing";
                  return (
                    <li
                      key={asset.Id}
                      className="rounded-md border border-border/60 bg-white/[0.02] px-2.5 py-2"
                    >
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-medium text-foreground">
                          {asset.Name || asset.Id}
                        </span>
                        <span
                          className={cn(
                            CHIP_CLASS,
                            ASSET_STATUS_CLASS[assetStatus],
                          )}
                        >
                          {t(`settings.seedanceAssets.assetStatus.${assetStatus}`)}
                        </span>
                        <span className="ml-auto shrink-0 text-muted-foreground">
                          {asset.AssetType}
                        </span>
                        {assetStatus === "Active" ? (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-7 shrink-0 text-xs"
                            onClick={() => void handleCopyReference(asset.Id)}
                          >
                            {t("settings.seedanceAssets.assets.copyRef")}
                          </Button>
                        ) : null}
                      </div>
                      <p className="mt-1 truncate text-muted-foreground">{asset.URL}</p>
                      {asset.Error?.Message ? (
                        <p className="mt-1 text-red-400">
                          {asset.Error.Code ? `[${asset.Error.Code}] ` : ""}
                          {asset.Error.Message}
                        </p>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* 真人活体认证：必须本人用自己账号在手机上完成，一组锁一个人，结果只回一个 GroupId。 */}
          <div className="mt-5 grid gap-2.5">
            <Label htmlFor="seedance-face-callback" className="text-sm font-medium">
              {t("settings.seedanceAssets.face.title")}
            </Label>
            <p className="text-xs leading-relaxed text-muted-foreground">
              {t("settings.seedanceAssets.face.description")}
            </p>
            <Input
              id="seedance-face-callback"
              name="seedance-face-callback"
              value={callbackUrl}
              placeholder={t("settings.seedanceAssets.face.callbackPlaceholder")}
              onChange={(event) => setCallbackUrl(event.target.value)}
              className="h-8"
            />
            <div className="flex items-center gap-2">
              <Button
                type="button"
                size="sm"
                className="h-8"
                onClick={handleCreateSession}
                disabled={busy}
              >
                {createSession.isPending ? (
                  <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                ) : null}
                {t("settings.seedanceAssets.face.create")}
              </Button>
              {session ? (
                <>
                  <a
                    href={session.H5Link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border/60 px-2.5 text-xs text-foreground transition-colors hover:bg-white/[0.05]"
                  >
                    <ExternalLink className="size-3.5" />
                    {t("settings.seedanceAssets.face.openH5")}
                  </a>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-8"
                    onClick={handleQueryResult}
                    disabled={busy}
                  >
                    {queryValidateResult.isPending ? (
                      <Loader2 className="mr-1.5 size-3.5 animate-spin" />
                    ) : null}
                    {t("settings.seedanceAssets.face.queryResult")}
                  </Button>
                </>
              ) : null}
            </div>
            {session ? (
              <p className="text-xs leading-relaxed text-muted-foreground">
                {t("settings.seedanceAssets.face.tokenHint", {
                  token: session.BytedToken,
                })}
              </p>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}

export { SeedanceAssetLibrarySection };
