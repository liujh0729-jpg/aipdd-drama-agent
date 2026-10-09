// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 「连接器MCP」对话框：Agent 页右上角入口，内嵌 MCP 服务器管理器
//（与 MCP 栏目共用同一组件），并提供前往 MCP 栏目管理的跳转。
import { useNavigate } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";
import { ArrowRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import { PROJECT_SECTION_ROUTES } from "@/components/layout/project-navigation-routes";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { AgentMcpServerManager } from "@/features/mcp/AgentMcpServerManager";

export function AgentConnectorsDialog({
  open,
  onClose,
  project,
}: {
  open: boolean;
  onClose: () => void;
  project: string;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("agent.connectors.title")}</DialogTitle>
          <DialogDescription>{t("agent.connectors.description")}</DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto pr-1">
          {/* 标题由本对话框提供，管理器只渲染操作行与列表。 */}
          <AgentMcpServerManager showHeading={false} />
        </div>
        <DialogFooter className="p-0 pt-2">
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            {t("agent.connectors.close")}
          </Button>
          <Button
            type="button"
            size="sm"
            className="gap-1.5"
            onClick={() => {
              onClose();
              void navigate({ to: PROJECT_SECTION_ROUTES.mcp, params: { project } });
            }}
          >
            {t("agent.connectors.manage")}
            <ArrowRight className="size-3.5" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}