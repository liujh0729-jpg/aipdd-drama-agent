// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 场景头自动补全的可编辑预览对话框：消费「默认文本 LLM」
// 产出的增强文本，人工核对/编辑后确认导入（不改动原始上传文件）。
import { useTranslation } from "react-i18next";
import { Loader2, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";

type AutoSceneHeadersDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: string;
  onChange: (value: string) => void;
  /** 后台任务仍在补全中。 */
  running: boolean;
  /** 确认写入增强文件并导入中。 */
  confirming: boolean;
  onRedetect: () => void;
  onConfirm: () => void;
};

export function AutoSceneHeadersDialog({
  open,
  onOpenChange,
  value,
  onChange,
  running,
  confirming,
  onRedetect,
  onConfirm,
}: AutoSceneHeadersDialogProps) {
  const { t } = useTranslation();
  const busy = running || confirming;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl sm:max-w-2xl rounded-lg bg-black">
        <DialogHeader>
          <DialogTitle>{t("ingest.autoSceneHeaders.title")}</DialogTitle>
          <DialogDescription>
            {t("ingest.autoSceneHeaders.description")}
          </DialogDescription>
        </DialogHeader>

        {running ? (
          <div className="flex items-center gap-2 rounded-md border bg-muted px-3 py-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            {t("ingest.autoSceneHeaders.running")}
          </div>
        ) : null}

        <Textarea
          value={value}
          onChange={(event) => onChange(event.target.value)}
          readOnly={running}
          spellCheck={false}
          className="min-h-[46vh] max-h-[60vh] resize-y whitespace-pre-wrap font-mono text-xs leading-5"
        />

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={onRedetect}
            disabled={busy}
          >
            <RefreshCw className="size-3.5" />
            {t("ingest.autoSceneHeaders.redetect")}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={confirming}
          >
            {t("ingest.autoSceneHeaders.cancel")}
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={busy || value.trim().length === 0}
          >
            {confirming ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {t("ingest.autoSceneHeaders.confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
