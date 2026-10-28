// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [重构] 设置对话框壳：仅保留页面骨架、导航与存储页；模型网关区（models 页）已移出
// 至 @/features/settings/model-gateway/（含死 UI OfficialGatewayPanel 清理）。

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  AlertTriangle,
  Clapperboard,
  Cookie,
  Cpu,
  DatabaseBackup,
  FileText,
  HardDrive,
  KeyRound,
  Library,
  Loader2,
  MonitorDown,
  Palette,
  ShieldCheck,
  Stethoscope,
  Users,
} from "lucide-react";
// [aipdd fork delta] API 密钥：统一密钥（外部 MCP + 模型广场），所有登录用户可见。
import { ApiKeysSection } from "@/components/settings/api-keys-section";
// [aipdd fork delta] 账户安全：本人自助改密 + operator 口令入库（多用户模式下所有登录用户可见）。
import {
  AccountSecuritySection,
} from "@/components/settings/account-security-section";
import {
  PromptLibrarySection,
} from "@/components/settings/prompt-library-section";
// [aipdd fork delta] 第四页：用户管理（仅 ST_CE_MULTI_USER + 超管可见）。
import {
  UserManagementSection,
} from "@/components/settings/user-management-section";
// [aipdd fork delta] 风格管理：逐个风格上传图墙封面（仅超管可见）。
import {
  StyleGallerySection,
} from "@/components/settings/style-gallery-section";
// [aipdd fork delta] 剪映草稿：配置 VectCut 服务的草稿存储根目录（仅超管可见）。
import { VectcutDraftSection } from "@/components/settings/vectcut-draft-section";
// [aipdd fork delta] 抖音 Cookie：文案提取纯 HTTP 主链路的登录态凭据（仅超管可见）。
import { DouyinCookieSection } from "@/components/settings/douyin-cookie-section";
// [aipdd fork delta] 环境安装：部署环境真实依赖探测 + 3D 世界按需安装（融入本页，仅超管可见）。
import { EnvSetupSection } from "@/components/settings/env-setup-section";
// [aipdd fork delta] 系统整体自检：环境 / 模型渠道 / 默认模型 / 存储一键只读体检（仅超管）。
import { DiagnosticsSection } from "@/components/settings/diagnostics-section";
// [aipdd fork delta] 配置备份 / 恢复：整盘 settings.db 导出与回灌（仅超管）。
import { ConfigBackupSection } from "@/components/settings/config-backup-section";
import { useSetupStatus } from "@/lib/queries/admin-users";
import { useAuthStore } from "@/stores/auth-store";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { InfoTip } from "@/components/ui/info-tip";
import { cn } from "@/lib/utils";
import {
  useModelGatewayConfig,
  useSaveMediaRelayConfig,
} from "@/lib/queries/model-gateway";
import {
  useSettingsStore,
  type AliyunOssStorageConfig,
  type AwsS3StorageConfig,
  type CloudinaryStorageConfig,
  type MediaStorageProvider,
} from "@/stores/settingsStore";
import {
  FieldRow,
  ModelConfigSection,
  getRequestErrorMessage,
} from "@/features/settings/model-gateway";
// [aipdd fork delta req47] Seedance 素材库页（能力探测门控在组件内部）。
import { SeedanceAssetLibrarySection } from "@/components/settings/seedance-asset-library-section";

// [重构] 兼容导出：以下工具被测试直接从本模块导入（official-register-link /
// settings-store-feature-model-config），实现已移至 @/features/settings/model-gateway。
export {
  officialSiteUrl,
  providersSupportingCapability,
} from "@/features/settings/model-gateway";

interface SettingsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

// [aipdd fork delta] 本地存储作为默认首选档位（参考图内联，无需 OSS/Cloudinary）。
// [aipdd fork delta req44] 新增 aipdd_cloud：本地素材上传后拿永久公网直链。两档都必需
// AIPDD API Key（本地档的参考视频/音频也靠它转公网直链）——实测上游受理带 data URL
// 的参考视频却不去拉素材，产出的就是一段与原视频不相干的片子。
const MEDIA_STORAGE_PROVIDERS: MediaStorageProvider[] = [
  "local",
  "aipdd_cloud",
  "aliyun_oss",
  "aws_s3",
  "cloudinary",
];

// [aipdd fork delta req44] 档位标签/按钮文案收进映射，避免再加一层三元。
const MEDIA_STORAGE_PROVIDER_LABEL_KEYS: Record<MediaStorageProvider, string> = {
  local: "settings.mediaStorage.providerLocal",
  aipdd_cloud: "settings.mediaStorage.providerAipddCloud",
  aliyun_oss: "settings.mediaStorage.providerAliyunOss",
  aws_s3: "settings.mediaStorage.providerAwsS3",
  cloudinary: "settings.mediaStorage.providerCloudinary",
};

const MEDIA_STORAGE_SAVE_LABEL_KEYS: Record<MediaStorageProvider, string> = {
  local: "settings.mediaStorage.saveLocal",
  aipdd_cloud: "settings.mediaStorage.saveAipddCloud",
  aliyun_oss: "settings.mediaStorage.save",
  aws_s3: "settings.mediaStorage.saveAwsS3",
  cloudinary: "settings.mediaStorage.saveCloudinary",
};

// Codex 本地桥接暂时隐藏（保留组件代码，后端就绪后改回 true 即可恢复）。
const SHOW_CODEX_BRIDGE = false;

export function SettingsDialog({ open, onOpenChange }: SettingsDialogProps) {
  const { t } = useTranslation();
  const [page, setPage] = useState<
    | "models"
    | "storage"
    | "prompts"
    | "apiKeys"
    | "security"
    | "users"
    | "styles"
    | "vectcut"
    | "douyin"
    | "seedanceAssets"
    | "envSetup"
    | "diagnostics"
    | "configBackup"
  >("models");
  // [aipdd fork delta] 用户管理页只对开了多用户模式的超管开放。
  const role = useAuthStore((state) => state.role);
  const setupStatusQuery = useSetupStatus(open);
  const multiUser = setupStatusQuery.data?.data?.multi_user === true;
  const showUsersPage = multiUser && role === "owner";
  // [aipdd fork delta] 账户安全页：多用户模式下任何登录用户都能自助改密
  // （operator 口令区在组件内部只对超管展示）。
  const showSecurityPage = multiUser;
  // [aipdd fork delta] 风格管理封面上传只对超管开放（不依赖多用户模式）。
  const showStyleGalleryPage = role === "owner";
  // [aipdd fork delta] 环境安装（含 3D 世界按需安装）只对超管开放：它探测/操作的是
  // 部署容器的真实运行环境，属运维职责，普通成员无需看到。
  const showEnvSetupPage = role === "owner";
  // [aipdd fork delta] 系统整体自检与配置备份/恢复都是运维职责，只对超管开放（与环境安装一致）。
  const showDiagnosticsPage = role === "owner";
  const showConfigBackupPage = role === "owner";
  // [aipdd fork delta] 剪映草稿目录只对超管开放（保存路由走网关管理权限守卫）。
  const showVectcutDraftPage = role === "owner";
  // [aipdd fork delta] 抖音登录态 Cookie 属部署级凭据，保存同样走网关管理守卫，仅超管可见。
  const showDouyinCookiePage = role === "owner";
  const statusQuery = useModelGatewayConfig(open);
  const settingsStatus = statusQuery.data?.data;
  const modelConfigured = Boolean(settingsStatus?.effective.configured);
  const mediaStorageConfigured = Boolean(
    settingsStatus?.mediaRelay?.configured,
  );
  // [aipdd fork delta] 剪映草稿目录配置状态（设置 → 剪映草稿导航徽标）。
  const vectcutDraftConfigured = Boolean(settingsStatus?.vectcut?.configured);

  const pageStatus = (configured: boolean, label: string) => {
    if (statusQuery.isLoading) {
      return (
        <Loader2
          className="absolute top-1 right-1 size-3 animate-spin text-muted-foreground sm:static sm:ml-auto sm:size-3.5"
          aria-hidden
        />
      );
    }
    if (configured) {
      return (
        <span
          className="absolute top-1 right-1 size-2 shrink-0 rounded-full bg-emerald-400 sm:static sm:ml-auto"
          aria-label={t("settings.statusConfigured", { page: label })}
          title={t("settings.statusConfigured", { page: label })}
        />
      );
    }
    return (
      <AlertTriangle
        className="absolute top-1 right-1 size-3.5 shrink-0 text-amber-400 sm:static sm:ml-auto sm:size-4"
        aria-label={t("settings.statusNotConfigured", { page: label })}
      />
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex h-[min(82vh,760px)] max-w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden rounded-lg border border-border bg-black p-0 ring-0 sm:max-w-[1120px]">
        <DialogHeader className="border-b border-border px-5 py-4">
          <DialogTitle>{t("settings.title")}</DialogTitle>
        </DialogHeader>

        <div className="flex min-h-0 flex-1">
          <nav
            aria-label={t("settings.navigationLabel")}
            className="flex w-14 shrink-0 flex-col gap-1 border-r border-border px-2 py-4 sm:w-44 sm:px-3"
          >
            <button
              type="button"
              aria-current={page === "models" ? "page" : undefined}
              onClick={() => setPage("models")}
              className={cn(
                "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                page === "models"
                  ? "bg-white/[0.09] text-foreground"
                  : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
              )}
            >
              <Cpu className="size-4" aria-hidden />
              <span className="hidden sm:inline">
                {t("settings.pages.models")}
              </span>
              {pageStatus(modelConfigured, t("settings.pages.models"))}
            </button>
            {/* [aipdd fork delta req47] Seedance 素材库：私域素材 + 真人活体认证。
                这一页能不能用取决于「这台 LocalAPI 有没有暴露素材库接口」，探测结论在
                面板内部读，不在此处加一次上游探活请求。 */}
            <button
              type="button"
              aria-current={page === "seedanceAssets" ? "page" : undefined}
              onClick={() => setPage("seedanceAssets")}
              className={cn(
                "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                page === "seedanceAssets"
                  ? "bg-white/[0.09] text-foreground"
                  : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
              )}
            >
              <Library className="size-4" aria-hidden />
              <span className="hidden sm:inline">
                {t("settings.pages.seedanceAssets")}
              </span>
            </button>
            <button
              type="button"
              aria-current={page === "storage" ? "page" : undefined}
              onClick={() => setPage("storage")}
              className={cn(
                "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                page === "storage"
                  ? "bg-white/[0.09] text-foreground"
                  : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
              )}
            >
              <HardDrive className="size-4" aria-hidden />
              <span className="hidden sm:inline">
                {t("settings.pages.storage")}
              </span>
              {pageStatus(mediaStorageConfigured, t("settings.pages.storage"))}
            </button>
            {/* [aipdd fork delta] 第三页：提示词库（内置提示词查看 / 修改 / 恢复默认）。 */}
            <button
              type="button"
              aria-current={page === "prompts" ? "page" : undefined}
              onClick={() => setPage("prompts")}
              className={cn(
                "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                page === "prompts"
                  ? "bg-white/[0.09] text-foreground"
                  : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
              )}
            >
              <FileText className="size-4" aria-hidden />
              <span className="hidden sm:inline">
                {t("settings.pages.prompts")}
              </span>
            </button>
            {/* [aipdd fork delta] API 密钥页：一把 key 统一承载外部 MCP 与模型广场调用。 */}
            <button
              type="button"
              aria-current={page === "apiKeys" ? "page" : undefined}
              onClick={() => setPage("apiKeys")}
              className={cn(
                "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                page === "apiKeys"
                  ? "bg-white/[0.09] text-foreground"
                  : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
              )}
            >
              <KeyRound className="size-4" aria-hidden />
              <span className="hidden sm:inline">
                {t("settings.pages.apiKeys")}
              </span>
            </button>
            {/* [aipdd fork delta] 账户安全页：本人自助改密 + operator 口令（多用户模式下可见）。 */}
            {showSecurityPage ? (
              <button
                type="button"
                aria-current={page === "security" ? "page" : undefined}
                onClick={() => setPage("security")}
                className={cn(
                  "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                  page === "security"
                    ? "bg-white/[0.09] text-foreground"
                    : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                )}
              >
                <ShieldCheck className="size-4" aria-hidden />
                <span className="hidden sm:inline">
                  {t("settings.pages.security")}
                </span>
              </button>
            ) : null}
            {/* [aipdd fork delta] 第四页：用户管理，只在多用户模式且当前账户是超管时出现。 */}
            {showUsersPage ? (
              <button
                type="button"
                aria-current={page === "users" ? "page" : undefined}
                onClick={() => setPage("users")}
                className={cn(
                  "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                  page === "users"
                    ? "bg-white/[0.09] text-foreground"
                    : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                )}
              >
                <Users className="size-4" aria-hidden />
                <span className="hidden sm:inline">
                  {t("settings.pages.users")}
                </span>
              </button>
            ) : null}
            {/* [aipdd fork delta] 风格管理页：仅超管可见，为每个风格上传图墙封面。 */}
            {showStyleGalleryPage ? (
              <button
                type="button"
                aria-current={page === "styles" ? "page" : undefined}
                onClick={() => setPage("styles")}
                className={cn(
                  "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                  page === "styles"
                    ? "bg-white/[0.09] text-foreground"
                    : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                )}
              >
                <Palette className="size-4" aria-hidden />
                <span className="hidden sm:inline">
                  {t("settings.pages.styles")}
                </span>
              </button>
            ) : null}
            {/* [aipdd fork delta] 剪映草稿页：仅超管可见，配置 VectCut 草稿目录。 */}
            {showVectcutDraftPage ? (
              <button
                type="button"
                aria-current={page === "vectcut" ? "page" : undefined}
                onClick={() => setPage("vectcut")}
                className={cn(
                  "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                  page === "vectcut"
                    ? "bg-white/[0.09] text-foreground"
                    : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                )}
              >
                <Clapperboard className="size-4" aria-hidden />
                <span className="hidden sm:inline">
                  {t("settings.pages.vectcutDraft")}
                </span>
                {pageStatus(
                  vectcutDraftConfigured,
                  t("settings.pages.vectcutDraft"),
                )}
              </button>
            ) : null}
            {/* [aipdd fork delta] 抖音 Cookie 页：仅超管可见，配置文案提取的纯 HTTP 凭据。 */}
            {showDouyinCookiePage ? (
              <button
                type="button"
                aria-current={page === "douyin" ? "page" : undefined}
                onClick={() => setPage("douyin")}
                className={cn(
                  "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                  page === "douyin"
                    ? "bg-white/[0.09] text-foreground"
                    : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                )}
              >
                <Cookie className="size-4" aria-hidden />
                <span className="hidden sm:inline">
                  {t("settings.pages.douyinCookie")}
                </span>
                {pageStatus(
                  Boolean(settingsStatus?.douyinCookie?.configured),
                  t("settings.pages.douyinCookie"),
                )}
              </button>
            ) : null}
            {/* [aipdd fork delta] 环境安装页：部署环境真实依赖 + 3D 世界按需安装（仅超管可见）。 */}
            {showEnvSetupPage ? (
              <button
                type="button"
                aria-current={page === "envSetup" ? "page" : undefined}
                onClick={() => setPage("envSetup")}
                className={cn(
                  "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                  page === "envSetup"
                    ? "bg-white/[0.09] text-foreground"
                    : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                )}
              >
                <MonitorDown className="size-4" aria-hidden />
                <span className="hidden sm:inline">
                  {t("settings.pages.envSetup")}
                </span>
              </button>
            ) : null}
            {/* [aipdd fork delta] 系统整体自检（仅超管）。 */}
            {showDiagnosticsPage ? (
              <button
                type="button"
                aria-current={page === "diagnostics" ? "page" : undefined}
                onClick={() => setPage("diagnostics")}
                className={cn(
                  "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                  page === "diagnostics"
                    ? "bg-white/[0.09] text-foreground"
                    : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                )}
              >
                <Stethoscope className="size-4" aria-hidden />
                <span className="hidden sm:inline">
                  {t("settings.pages.diagnostics")}
                </span>
              </button>
            ) : null}
            {/* [aipdd fork delta] 配置备份 / 恢复（仅超管）。 */}
            {showConfigBackupPage ? (
              <button
                type="button"
                aria-current={page === "configBackup" ? "page" : undefined}
                onClick={() => setPage("configBackup")}
                className={cn(
                  "relative flex h-10 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium transition-colors sm:justify-start sm:px-3",
                  page === "configBackup"
                    ? "bg-white/[0.09] text-foreground"
                    : "text-muted-foreground hover:bg-white/[0.05] hover:text-foreground",
                )}
              >
                <DatabaseBackup className="size-4" aria-hidden />
                <span className="hidden sm:inline">
                  {t("settings.pages.configBackup")}
                </span>
              </button>
            ) : null}
          </nav>

          {page === "models" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <ModelConfigSection open={open && page === "models"} />
                {SHOW_CODEX_BRIDGE && <CodexBridgeSection />}
              </ScrollArea>
            </div>
          ) : page === "storage" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <MediaStorageSection />
              </ScrollArea>
            </div>
          ) : page === "seedanceAssets" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <SeedanceAssetLibrarySection />
              </ScrollArea>
            </div>
          ) : page === "apiKeys" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <ApiKeysSection />
              </ScrollArea>
            </div>
          ) : page === "security" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <AccountSecuritySection />
              </ScrollArea>
            </div>
          ) : page === "users" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <UserManagementSection />
              </ScrollArea>
            </div>
          ) : page === "styles" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <StyleGallerySection />
              </ScrollArea>
            </div>
          ) : page === "vectcut" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <VectcutDraftSection />
              </ScrollArea>
            </div>
          ) : page === "douyin" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <DouyinCookieSection />
              </ScrollArea>
            </div>
          ) : page === "envSetup" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <EnvSetupSection />
              </ScrollArea>
            </div>
          ) : page === "diagnostics" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <DiagnosticsSection onNavigate={setPage} />
              </ScrollArea>
            </div>
          ) : page === "configBackup" ? (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <ConfigBackupSection />
              </ScrollArea>
            </div>
          ) : (
            <div className="min-w-0 flex-1">
              <ScrollArea className="h-full [&_[data-slot=scroll-area-scrollbar]]:!w-1 [&_[data-slot=scroll-area-scrollbar]]:!border-l-0 [&_[data-slot=scroll-area-scrollbar]]:!p-0">
                <PromptLibrarySection />
              </ScrollArea>
            </div>
          )}
        </div>

        <div className="flex justify-end border-t border-border px-5 py-3.5">
          <DialogClose
            render={
              <Button
                variant="outline"
                size="sm"
              />
            }
          >
            {t("settings.close")}
          </DialogClose>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function MediaStorageSection() {
  const { t } = useTranslation();
  const configQuery = useModelGatewayConfig(true);
  const mediaRelay = configQuery.data?.data.mediaRelay;
  const mediaStorage = useSettingsStore((s) => s.mediaStorage);
  const setProvider = useSettingsStore((s) => s.setMediaStorageProvider);
  const updateCloudinary = useSettingsStore(
    (s) => s.updateCloudinaryStorageConfig,
  );
  const updateAliyunOss = useSettingsStore(
    (s) => s.updateAliyunOssStorageConfig,
  );
  const updateAwsS3 = useSettingsStore((s) => s.updateAwsS3StorageConfig);
  const saveMediaRelay = useSaveMediaRelayConfig();

  const { provider, cloudinary, aliyunOss, awsS3 } = mediaStorage;
  const [ttlSeconds, setTtlSeconds] = useState("1800");
  // [aipdd fork delta req44] AIPDD 参考媒体中转（本地档与数字资产云档共用）：
  // Key 必填 + 可选站点地址/前缀/是否登记为数字资产。
  const [aipddApiKey, setAipddApiKey] = useState("");
  const [aipddBaseUrl, setAipddBaseUrl] = useState("");
  const [aipddPrefix, setAipddPrefix] = useState("");
  const [aipddRegisterAsset, setAipddRegisterAsset] = useState(false);
  const mediaRelayKey = JSON.stringify(mediaRelay ?? {});
  useEffect(() => {
    if (!mediaRelay) return;
    if (
      mediaRelay.provider === "aliyun_oss" ||
      mediaRelay.provider === "aws_s3" ||
      mediaRelay.provider === "cloudinary" ||
      mediaRelay.provider === "aipdd_cloud" ||
      mediaRelay.provider === "local"
    ) {
      setProvider(mediaRelay.provider as MediaStorageProvider);
    }
    if (mediaRelay.endpoint || mediaRelay.bucket) {
      updateAliyunOss({
        endpoint: mediaRelay.endpoint || aliyunOss.endpoint,
        bucket: mediaRelay.bucket || aliyunOss.bucket,
        ...(mediaRelay.configured
          ? { accessKeyId: "", accessKeySecret: "" }
          : {}),
      });
    }
    // [aipdd fork delta] AWS S3 回显：region/bucket/endpoint 以后端为真源；完整密钥
    // 不下发（只回脱敏 preview），已配时清空输入框避免误以为还是空。
    if (mediaRelay.s3Region || mediaRelay.s3Bucket) {
      updateAwsS3({
        region: mediaRelay.s3Region || awsS3.region,
        bucket: mediaRelay.s3Bucket || awsS3.bucket,
        endpoint: mediaRelay.s3EndpointUrl || awsS3.endpoint,
        ...(mediaRelay.provider === "aws_s3" && mediaRelay.configured
          ? { accessKeyId: "", accessKeySecret: "", sessionToken: "" }
          : {}),
      });
    }
    if (mediaRelay.cloudName || mediaRelay.apiFolder) {
      updateCloudinary({
        cloudName: mediaRelay.cloudName || cloudinary.cloudName,
        apiFolder: mediaRelay.apiFolder || cloudinary.apiFolder,
        ...(mediaRelay.provider === "cloudinary" && mediaRelay.configured
          ? { apiKey: "", apiSecret: "" }
          : {}),
      });
    }
    if (mediaRelay.ttlSeconds) {
      setTtlSeconds((current) =>
        current === String(mediaRelay.ttlSeconds)
          ? current
          : String(mediaRelay.ttlSeconds),
      );
    }
    // [aipdd fork delta req44] 站点地址/前缀/登记开关以后端为真源回显；完整 Key 不下
    // 发前端（只回脱敏 preview），留空即「沿用已保存值」。
    setAipddBaseUrl(mediaRelay.aipddBaseUrl ?? "");
    if (mediaRelay.aipddApiKeyPreview) setAipddApiKey("");
    setAipddPrefix(mediaRelay.aipddPrefix ?? "");
    setAipddRegisterAsset(Boolean(mediaRelay.aipddRegisterAsset));
    // Full AccessKey values must never be kept after the backend has a saved config.
    // Users re-enter them only when creating/updating the OSS relay credentials.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mediaRelayKey]);

  // [aipdd fork delta req44] 就绪判定收拢到后端：本地档现在也绑 AIPDD 中转，
  // 没 Key 就照旧弹黄条（参考视频/音频跑不通）。
  const hasConfiguredMediaRelay = Boolean(mediaRelay?.configured);
  const configuredProvider = hasConfiguredMediaRelay
    ? (mediaRelay?.provider as MediaStorageProvider)
    : provider;
  const handleSave = async () => {
    const ttl = Number(ttlSeconds.trim() || "0");
    if (!Number.isFinite(ttl) || ttl <= 0) {
      toast.error(t("settings.mediaStorage.validation.ttlSeconds"));
      return;
    }
    try {
      // [aipdd fork delta req44] 本地存储 / 数字资产云两档共用同一份 AIPDD 中转凭据：
      // Key 留空 = 沿用已存值（后端还能回落已连接的 AIPDD 渠道 Key）。
      if (provider === "local" || provider === "aipdd_cloud") {
        // 本地档只渲染 Key 一个字段，其余三项**根本不进 payload**：后端的合并语义
        // 是 ``None=不改动``，传空串会把「数字资产云」档存下的前缀 / 登记开关 / 站点
        // 地址抹掉（Key 因为是密钥语义才恰好不会）。
        const relayFields =
          provider === "local"
            ? {}
            : {
                aipddBaseUrl: aipddBaseUrl.trim(),
                aipddPrefix: aipddPrefix.trim(),
                registerAsset: aipddRegisterAsset,
              };
        const res = await saveMediaRelay.mutateAsync({
          provider,
          ttlSeconds: Math.trunc(ttl),
          ...(aipddApiKey.trim() ? { aipddApiKey: aipddApiKey.trim() } : {}),
          ...relayFields,
        });
        if (!res.ok) {
          toast.error(res.error);
          return;
        }
        setAipddApiKey("");
        toast.success(
          t(
            provider === "local"
              ? "settings.mediaStorage.localSaveSuccess"
              : "settings.mediaStorage.aipddCloudSaveSuccess",
          ),
        );
        return;
      }
      // [aipdd fork delta] AWS S3 档：region/bucket/endpoint 为非密钥字段（空串=清空），
      // 总传；AK/SK/Token 为密钥，留空不进 payload（后端 None=不改）。
      if (provider === "aws_s3") {
        const res = await saveMediaRelay.mutateAsync({
          provider: "aws_s3",
          ttlSeconds: Math.trunc(ttl),
          s3Region: awsS3.region.trim(),
          s3Bucket: awsS3.bucket.trim(),
          s3EndpointUrl: awsS3.endpoint.trim(),
          ...(awsS3.accessKeyId.trim()
            ? { s3AccessKeyId: awsS3.accessKeyId.trim() }
            : {}),
          ...(awsS3.accessKeySecret.trim()
            ? { s3AccessKeySecret: awsS3.accessKeySecret.trim() }
            : {}),
          ...(awsS3.sessionToken.trim()
            ? { s3SessionToken: awsS3.sessionToken.trim() }
            : {}),
        });
        if (!res.ok) {
          toast.error(res.error);
          return;
        }
        updateAwsS3({ accessKeyId: "", accessKeySecret: "", sessionToken: "" });
        toast.success(t("settings.mediaStorage.awsS3SaveSuccess"));
        return;
      }
      const res = await saveMediaRelay.mutateAsync(
        provider === "cloudinary"
          ? {
              provider: "cloudinary",
              ttlSeconds: Math.trunc(ttl),
              cloudName: cloudinary.cloudName.trim(),
              apiFolder: cloudinary.apiFolder.trim(),
              ...(cloudinary.apiKey.trim()
                ? { apiKey: cloudinary.apiKey.trim() }
                : {}),
              ...(cloudinary.apiSecret.trim()
                ? { apiSecret: cloudinary.apiSecret.trim() }
                : {}),
            }
          : {
              provider: "aliyun_oss",
              ttlSeconds: Math.trunc(ttl),
              endpoint: aliyunOss.endpoint.trim(),
              bucket: aliyunOss.bucket.trim(),
              ...(aliyunOss.accessKeyId.trim()
                ? { accessKeyId: aliyunOss.accessKeyId.trim() }
                : {}),
              ...(aliyunOss.accessKeySecret.trim()
                ? { accessKeySecret: aliyunOss.accessKeySecret.trim() }
                : {}),
            },
      );
      if (!res.ok) {
        toast.error(res.error);
        return;
      }
      if (provider === "cloudinary") {
        updateCloudinary({ apiKey: "", apiSecret: "" });
      } else {
        updateAliyunOss({ accessKeyId: "", accessKeySecret: "" });
      }
      toast.success(
        provider === "cloudinary"
          ? t("settings.mediaStorage.cloudinarySaveSuccess")
          : t("settings.mediaStorage.saveSuccess"),
      );
    } catch (error) {
      toast.error(
        await getRequestErrorMessage(
          error,
          t("settings.mediaStorage.saveFailed"),
        ),
      );
    }
  };

  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "size-1.5 rounded-full",
            hasConfiguredMediaRelay ? "bg-emerald-400" : "bg-amber-400",
          )}
        />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.mediaStorage.title")}
        </h3>
        {!hasConfiguredMediaRelay ? (
          <AlertTriangle
            className="size-3.5 text-amber-400"
            aria-label={t("settings.mediaStorage.warningIconLabel")}
          />
        ) : null}
        <span className="ml-1 rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
          {t("settings.mediaStorage.currentPlan")}:{" "}
          {t(MEDIA_STORAGE_PROVIDER_LABEL_KEYS[configuredProvider])}
        </span>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.mediaStorage.description")}
      </p>

      <p className="mt-3 text-xs text-muted-foreground">
        {t("settings.mediaStorage.status")}:{" "}
        <span
          className={
            hasConfiguredMediaRelay ? "text-emerald-400" : "text-amber-300"
          }
        >
          {hasConfiguredMediaRelay
            ? t("settings.mediaStorage.configured")
            : t("settings.mediaStorage.notConfigured")}
        </span>
        {hasConfiguredMediaRelay && mediaRelay?.source ? (
          <span className="ml-2 text-[11px] text-muted-foreground/80">
            {t("settings.mediaStorage.source", { source: mediaRelay.source })}
          </span>
        ) : null}
      </p>
      {!hasConfiguredMediaRelay ? (
        <div className="mt-3 flex gap-2 rounded-md border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-100">
          <AlertTriangle
            className="mt-0.5 size-3.5 shrink-0 text-amber-300"
            aria-hidden
          />
          <p>
            {provider === "local" || provider === "aipdd_cloud"
              ? t("settings.mediaStorage.aipddRelayMissingImpact")
              : t("settings.mediaStorage.notConfiguredImpact")}
          </p>
        </div>
      ) : null}

      <div className="mt-4 flex items-center gap-3">
        <span className="inline-flex w-[64px] shrink-0 items-center text-xs text-muted-foreground">
          {t("settings.mediaStorage.provider")}
          <InfoTip text={t("settings.mediaStorage.tips.provider")} side="right" />
        </span>
        <Tabs
          value={provider}
          onValueChange={(value) => setProvider(value as MediaStorageProvider)}
        >
          <TabsList>
            {MEDIA_STORAGE_PROVIDERS.map((p) => (
              <TabsTrigger key={p} value={p}>
                {t(MEDIA_STORAGE_PROVIDER_LABEL_KEYS[p])}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      <div className="mt-4 space-y-2.5">
        {provider === "local" ? (
          <>
            <div className="rounded-md border border-emerald-500/25 bg-emerald-500/[0.06] px-3 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
              <p>{t("settings.mediaStorage.localHint")}</p>
            </div>
            {/* [aipdd fork delta req44] 本地档的参考视频/音频也要中转，所以 Key 同样必填。
                站点地址 / 前缀 / 登记开关不在本地档暴露（用户口径）：本地档只关心中转
                凭据，地址留空由后端回落已连接的 AIPDD 渠道，要自定义这些去「数字资产云」档。 */}
            <AipddRelayFields
              apiKey={aipddApiKey}
              onApiKeyChange={setAipddApiKey}
              apiKeyPreview={mediaRelay?.aipddApiKeyPreview ?? ""}
              relayReady={Boolean(mediaRelay?.aipddChannelConfigured)}
            />
          </>
        ) : provider === "aipdd_cloud" ? (
          <AipddRelayFields
            apiKey={aipddApiKey}
            onApiKeyChange={setAipddApiKey}
            baseUrl={aipddBaseUrl}
            onBaseUrlChange={setAipddBaseUrl}
            apiKeyPreview={mediaRelay?.aipddApiKeyPreview ?? ""}
            relayReady={Boolean(mediaRelay?.aipddChannelConfigured)}
            prefix={aipddPrefix}
            onPrefixChange={setAipddPrefix}
            registerAsset={aipddRegisterAsset}
            onRegisterAssetChange={setAipddRegisterAsset}
          />
        ) : provider === "aws_s3" ? (
          <AwsS3Fields
            config={awsS3}
            onChange={updateAwsS3}
            ttlSeconds={ttlSeconds}
            onTtlSecondsChange={setTtlSeconds}
            accessKeyIdPreview={mediaRelay?.s3AccessKeyIdPreview ?? ""}
            accessKeySecretPreview={mediaRelay?.s3AccessKeySecretPreview ?? ""}
            sessionTokenPreview={mediaRelay?.s3SessionTokenPreview ?? ""}
          />
        ) : provider === "cloudinary" ? (
          <CloudinaryFields
            config={cloudinary}
            onChange={updateCloudinary}
            apiKeyPreview={mediaRelay?.cloudinaryApiKeyPreview ?? ""}
            apiSecretPreview={mediaRelay?.cloudinaryApiSecretPreview ?? ""}
          />
        ) : (
          <AliyunOssFields
            config={aliyunOss}
            onChange={updateAliyunOss}
            ttlSeconds={ttlSeconds}
            onTtlSecondsChange={setTtlSeconds}
            accessKeyIdPreview={mediaRelay?.accessKeyIdPreview ?? ""}
            accessKeySecretPreview={mediaRelay?.accessKeySecretPreview ?? ""}
          />
        )}
      </div>

      <div className="mt-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <p className="text-[11px] leading-relaxed text-muted-foreground">
            {provider === "aliyun_oss" ? (
              t("settings.mediaStorage.fieldsHint")
            ) : provider === "aws_s3" ? (
              t("settings.mediaStorage.awsS3FieldsHint")
            ) : provider === "cloudinary" ? (
              <>
                {t("settings.mediaStorage.cloudinaryFieldsHint")}{" "}
                <a
                  href="https://cloudinary.com/users/register/free"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-cyan-400 hover:text-cyan-300"
                >
                  {t("settings.mediaStorage.cloudinaryRegisterLink")}
                </a>
              </>
            ) : (
              t("settings.mediaStorage.aipddRelayFieldsHint")
            )}
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          className="shrink-0"
          onClick={handleSave}
          disabled={saveMediaRelay.isPending || configQuery.isLoading}
        >
          {saveMediaRelay.isPending ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : null}
          {t(MEDIA_STORAGE_SAVE_LABEL_KEYS[provider])}
        </Button>
      </div>
    </section>
  );
}

// [aipdd fork delta req44] AIPDD 参考媒体中转表单：「本地存储」与「AIPDD 数字资产云」
// 两档共用（本地档的参考视频/音频也靠它转公网直链）。Key 是必填项：留空 = 沿用已存
// 值，后端再回落已连接 AIPDD 渠道自身的 Key；站点地址一般不用填（取渠道 baseUrl 的
// 站点根），只在上传口与渠道不同源时才需要显式指定。数字资产云档额外能配前缀与
// 「同时登记为数字资产」。
function AipddRelayFields({
  apiKey,
  onApiKeyChange,
  baseUrl,
  onBaseUrlChange,
  apiKeyPreview,
  relayReady,
  prefix,
  onPrefixChange,
  registerAsset,
  onRegisterAssetChange,
}: {
  apiKey: string;
  onApiKeyChange: (value: string) => void;
  baseUrl?: string;
  /** 不传就不渲染「站点地址」输入（本地档只要 Key；地址由渠道 baseUrl 推导）。 */
  onBaseUrlChange?: (value: string) => void;
  apiKeyPreview: string;
  relayReady: boolean;
  prefix?: string;
  onPrefixChange?: (value: string) => void;
  registerAsset?: boolean;
  onRegisterAssetChange?: (value: boolean) => void;
}) {
  const { t } = useTranslation();
  const hasAssetOptions = Boolean(onPrefixChange && onRegisterAssetChange);
  const hasBaseUrlField = Boolean(onBaseUrlChange);
  return (
    <>
      <div className="rounded-md border border-emerald-500/25 bg-emerald-500/[0.06] px-3 py-2.5 text-[11px] leading-relaxed text-muted-foreground">
        <p>{t("settings.mediaStorage.aipddRelayHint")}</p>
      </div>
      {!relayReady ? (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/35 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-100">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-300" aria-hidden />
          <p>{t("settings.mediaStorage.aipddRelayMissing")}</p>
        </div>
      ) : null}
      <FieldRow
        secret
        name="aipdd-relay-api-key"
        label={t("settings.mediaStorage.fields.aipddApiKey")}
        value={apiKey}
        onChange={onApiKeyChange}
        placeholder={apiKeyPreview || undefined}
        savedPreview={apiKeyPreview}
        hint={t("settings.mediaStorage.tips.aipddApiKey")}
      />
      {hasBaseUrlField ? (
        <FieldRow
          label={t("settings.mediaStorage.fields.aipddBaseUrl")}
          value={baseUrl ?? ""}
          onChange={(value) => onBaseUrlChange?.(value)}
          placeholder={t("settings.mediaStorage.aipddBaseUrlPlaceholder")}
          hint={t("settings.mediaStorage.tips.aipddBaseUrl")}
        />
      ) : null}
      {hasAssetOptions ? (
        <>
          <FieldRow
            label={t("settings.mediaStorage.aipddRelayFields.prefix")}
            value={prefix ?? ""}
            onChange={(value) => onPrefixChange?.(value)}
            placeholder="assets/dramaclaw"
            hint={t("settings.mediaStorage.tips.aipddPrefix")}
          />
          <label className="flex items-start gap-2.5 rounded-[8px] border border-border bg-muted/40 p-2.5">
            <Checkbox
              className="mt-0.5"
              checked={Boolean(registerAsset)}
              onCheckedChange={(checked) =>
                onRegisterAssetChange?.(checked === true)
              }
            />
            <span className="space-y-0.5">
              <span className="block text-xs font-medium text-foreground">
                {t("settings.mediaStorage.aipddRelayFields.registerAsset")}
              </span>
              <span className="block text-[11px] leading-relaxed text-muted-foreground">
                {t(
                  "settings.mediaStorage.aipddRelayFields.registerAssetHint",
                )}
              </span>
            </span>
          </label>
        </>
      ) : null}
    </>
  );
}

function CloudinaryFields({
  config,
  onChange,
  apiKeyPreview,
  apiSecretPreview,
}: {
  config: CloudinaryStorageConfig;
  onChange: (patch: Partial<CloudinaryStorageConfig>) => void;
  apiKeyPreview: string;
  apiSecretPreview: string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <FieldRow
        label={t("settings.mediaStorage.fields.cloudName")}
        value={config.cloudName}
        onChange={(v) => onChange({ cloudName: v })}
        hint={t("settings.mediaStorage.tips.cloudName")}
      />
      <FieldRow
        secret
        name="cloudinary-api-key"
        label={t("settings.mediaStorage.fields.apiKey")}
        value={config.apiKey}
        onChange={(v) => onChange({ apiKey: v })}
        placeholder={apiKeyPreview || undefined}
        savedPreview={apiKeyPreview}
        hint={t("settings.mediaStorage.tips.cloudinaryApiKey")}
      />
      <FieldRow
        secret
        name="cloudinary-api-secret"
        label={t("settings.mediaStorage.fields.apiSecret")}
        value={config.apiSecret}
        onChange={(v) => onChange({ apiSecret: v })}
        placeholder={apiSecretPreview || undefined}
        savedPreview={apiSecretPreview}
        hint={t("settings.mediaStorage.tips.cloudinaryApiSecret")}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.apiFolder")}
        value={config.apiFolder}
        onChange={(v) => onChange({ apiFolder: v })}
        hint={t("settings.mediaStorage.tips.apiFolder")}
      />
    </>
  );
}

function AliyunOssFields({
  config,
  onChange,
  ttlSeconds,
  onTtlSecondsChange,
  accessKeyIdPreview,
  accessKeySecretPreview,
}: {
  config: AliyunOssStorageConfig;
  onChange: (patch: Partial<AliyunOssStorageConfig>) => void;
  ttlSeconds: string;
  onTtlSecondsChange: (value: string) => void;
  accessKeyIdPreview: string;
  accessKeySecretPreview: string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <FieldRow
        name="aliyun-oss-access-key-id"
        label={t("settings.mediaStorage.fields.accessKeyId")}
        value={config.accessKeyId}
        onChange={(v) => onChange({ accessKeyId: v })}
        placeholder={accessKeyIdPreview || undefined}
        savedPreview={accessKeyIdPreview}
        hint={t("settings.mediaStorage.tips.accessKeyId")}
      />
      <FieldRow
        secret
        name="aliyun-oss-access-key-secret"
        label={t("settings.mediaStorage.fields.accessKeySecret")}
        value={config.accessKeySecret}
        onChange={(v) => onChange({ accessKeySecret: v })}
        placeholder={accessKeySecretPreview || undefined}
        savedPreview={accessKeySecretPreview}
        hint={t("settings.mediaStorage.tips.accessKeySecret")}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.bucket")}
        value={config.bucket}
        onChange={(v) => onChange({ bucket: v })}
        hint={t("settings.mediaStorage.tips.bucket")}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.endpoint")}
        value={config.endpoint}
        onChange={(v) => onChange({ endpoint: v })}
        hint={t("settings.mediaStorage.tips.endpoint")}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.ttlSeconds")}
        value={ttlSeconds}
        onChange={onTtlSecondsChange}
        hint={t("settings.mediaStorage.tips.ttlSeconds")}
      />
    </>
  );
}

// [aipdd fork delta] AWS S3 档表单：AccessKey ID/Secret + Bucket + Region 必填，
// Endpoint 仅自建 S3 兼容网关才填（留空按 Region 解官方端点），Session Token 仅临时凭据。
// 与阿里云 OSS 同构：密钥留空=沿用已存值（后端 None=不改），不回落 env。
function AwsS3Fields({
  config,
  onChange,
  ttlSeconds,
  onTtlSecondsChange,
  accessKeyIdPreview,
  accessKeySecretPreview,
  sessionTokenPreview,
}: {
  config: AwsS3StorageConfig;
  onChange: (patch: Partial<AwsS3StorageConfig>) => void;
  ttlSeconds: string;
  onTtlSecondsChange: (value: string) => void;
  accessKeyIdPreview: string;
  accessKeySecretPreview: string;
  sessionTokenPreview: string;
}) {
  const { t } = useTranslation();
  return (
    <>
      <FieldRow
        secret
        name="aws-s3-access-key-id"
        label={t("settings.mediaStorage.fields.awsAccessKeyId")}
        value={config.accessKeyId}
        onChange={(v) => onChange({ accessKeyId: v })}
        placeholder={accessKeyIdPreview || undefined}
        savedPreview={accessKeyIdPreview}
        hint={t("settings.mediaStorage.tips.accessKeyId")}
      />
      <FieldRow
        secret
        name="aws-s3-access-key-secret"
        label={t("settings.mediaStorage.fields.awsSecretAccessKey")}
        value={config.accessKeySecret}
        onChange={(v) => onChange({ accessKeySecret: v })}
        placeholder={accessKeySecretPreview || undefined}
        savedPreview={accessKeySecretPreview}
        hint={t("settings.mediaStorage.tips.accessKeySecret")}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.awsRegion")}
        value={config.region}
        onChange={(v) => onChange({ region: v })}
        placeholder="us-east-1"
        hint={t("settings.mediaStorage.tips.region")}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.bucket")}
        value={config.bucket}
        onChange={(v) => onChange({ bucket: v })}
        hint={t("settings.mediaStorage.tips.bucket")}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.s3Endpoint")}
        value={config.endpoint}
        onChange={(v) => onChange({ endpoint: v })}
        placeholder={t("settings.mediaStorage.awsS3EndpointPlaceholder")}
        hint={t("settings.mediaStorage.tips.endpoint")}
      />
      <FieldRow
        secret
        name="aws-s3-session-token"
        label={t("settings.mediaStorage.fields.awsSessionToken")}
        value={config.sessionToken}
        onChange={(v) => onChange({ sessionToken: v })}
        placeholder={sessionTokenPreview || undefined}
        savedPreview={sessionTokenPreview}
        hint={t("settings.mediaStorage.tips.sessionToken")}
      />
      <FieldRow
        label={t("settings.mediaStorage.fields.ttlSeconds")}
        value={ttlSeconds}
        onChange={onTtlSecondsChange}
        hint={t("settings.mediaStorage.tips.ttlSeconds")}
      />
    </>
  );
}

function CodexBridgeSection() {
  const { t } = useTranslation();
  return (
    <section className="px-5 py-5">
      <div className="flex items-center gap-2">
        <span className="size-1.5 rounded-full bg-emerald-400" />
        <h3 className="font-heading text-sm font-medium text-foreground">
          {t("settings.codexBridge.title")}
        </h3>
        <span className="rounded bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
          {t("settings.codexBridge.badge")}
        </span>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
        {t("settings.codexBridge.description")}
      </p>

      <div className="mt-3 space-y-2 text-xs">
        <div className="flex items-center gap-3">
          <span className="w-[48px] shrink-0 text-muted-foreground">
            {t("settings.codexBridge.statusLabel")}
          </span>
          <span className="inline-flex items-center gap-1.5 text-emerald-400">
            <span className="size-1.5 rounded-full bg-emerald-400" />
            {t("settings.codexBridge.statusConnected")}
          </span>
        </div>
        <div className="flex items-center gap-3">
          <span className="w-[48px] shrink-0 text-muted-foreground">
            {t("settings.codexBridge.authLabel")}
          </span>
          <span className="text-foreground">
            {t("settings.codexBridge.authReady")}
          </span>
        </div>
      </div>
    </section>
  );
}
