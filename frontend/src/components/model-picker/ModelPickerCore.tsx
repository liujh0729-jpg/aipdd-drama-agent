// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] 模型下拉共享内核：画布 ProviderModelPicker 与工具页 ModelSelect
// 归一到同一份「触发器 + 弹层」实现。两者的差异全部由 props 参数化：
//   - 画布：portal 到 body + fixed 定位（节点缩放/平移下不被裁剪）、无 aria role
//     （保持既有 DOM 结构）、text-text-dark 色调、stopPropagation（防冒泡触发节点
//     选择/拖拽）、禁用项 hover 提示、打开回调（视频目录强制重拉）。
//   - 工具页：就地 absolute 弹层、combobox/listbox 全套 aria、text-text 色调、
//     分组标题、chevron 打开翻转。
// 两边的对外 API 与 class 常量各自留在薄适配器里，本文件只承担渲染与交互语义。
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown } from "lucide-react";

import { ModelChannelBadge } from "@/components/model-picker/ModelChannelBadge";
import { cn } from "@/lib/utils";

/** 一个候选项。value 是提交/回传用的稳定值；"" 是合法空值语义（如「默认引擎」）。 */
export interface ModelPickerEntry {
  value: string;
  label: string;
  /** 展示用渠道；undefined / 空串 = 不标注（胶囊组件内部判空）。 */
  channel?: string;
  /** 非 null 时灰显且不可点（hover 出提示）；已选中项不受禁。 */
  disabledReason?: string | null;
}

/** 一组候选项；无 label 时不渲染分组标题（画布为单组平铺）。 */
export interface ModelPickerEntryGroup {
  label?: string;
  options: ModelPickerEntry[];
}

export interface ModelPickerCoreProps {
  groups: ModelPickerEntryGroup[];
  selectedValue: string | null;
  onSelect: (value: string) => void;

  /** 触发器文案（调用方算好兜底链）。 */
  triggerLabel: string;
  triggerClassName: string;
  triggerLabelClassName?: string;
  triggerIcon?: ReactNode;
  /** 触发器上已选项的渠道胶囊；undefined / 空串不渲染。 */
  triggerChannel?: string;

  /** 开启 combobox / listbox / option / group 全套 aria 语义（工具页表单）。 */
  withListboxRole?: boolean;
  /** 无障碍名（触发器 + 列表共用）。 */
  ariaLabel?: string;
  disabled?: boolean;
  /** 触发器与弹层的点击不冒泡（画布节点内必须，防触发节点选择/拖拽）。 */
  stopPropagation?: boolean;
  /** 每次从关闭到打开时回调（画布用来「打开即重拉」视频目录）。 */
  onOpen?: () => void;

  chevronClassName: string;
  /** 打开时给 chevron 追加 rotate-180（工具页用；画布保持不转）。 */
  chevronRotateOnOpen?: boolean;

  popoverClassName: string;
  /** portal 到 body 的 fixed 弹层（画布：坐标夹取 + 视口跟随）；缺省就地 absolute。 */
  portal?: boolean;
  placement?: "top" | "bottom";
  popoverWidth?: number;
  /** 候选项列表的包裹层 class（画布 flex flex-col gap-0.5；工具页不包裹）。 */
  listClassName?: string;
  /** 候选项为空时的兜底内容（画布：加载中 / 空目录提示）。 */
  emptyContent?: ReactNode;

  /** 选项配色：canvas = text-text-dark 系；tools = text-text 系。 */
  optionTone?: "canvas" | "tools";

  /** 根容器附加 class（画布 min-w-0）；调用方 className 追加在后。 */
  rootClassName?: string;
  className?: string;
}

const DEFAULT_POPOVER_WIDTH = 260;
const OPTION_BASE_CLASS =
  "inline-flex h-8 w-full items-center gap-2 rounded-[6px] px-3 text-left text-xs font-medium transition-colors";
const OPTION_TONES = {
  canvas: {
    active: "bg-white/[0.13] text-text-dark ring-1 ring-white/24",
    disabled: "cursor-not-allowed text-text-muted/40",
    idle: "text-text-muted/95 hover:bg-white/[0.11] hover:text-text-dark",
  },
  tools: {
    active: "bg-white/[0.13] text-foreground ring-1 ring-white/24",
    disabled: "",
    idle: "text-muted-foreground/95 hover:bg-white/[0.11] hover:text-foreground",
  },
} as const;

export function ModelPickerCore({
  groups,
  selectedValue,
  onSelect,
  triggerLabel,
  triggerClassName,
  triggerLabelClassName,
  triggerIcon,
  triggerChannel,
  withListboxRole,
  ariaLabel,
  disabled,
  stopPropagation,
  onOpen,
  chevronClassName,
  chevronRotateOnOpen,
  popoverClassName,
  portal,
  placement = "top",
  popoverWidth,
  listClassName,
  emptyContent,
  optionTone = "canvas",
  rootClassName,
  className,
}: ModelPickerCoreProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [popoverPosition, setPopoverPosition] = useState<{ left: number; top: number } | null>(null);
  // 禁用项的 hover 提示：自渲染成 z 高于弹窗(z-[10001] > z-[10000])的浮层，锚定到
  // 当前项的右下角并 portal 到 body，避免被弹窗遮挡 / 被列表 overflow 裁剪。
  const [disabledTooltip, setDisabledTooltip] = useState<{
    reason: string;
    left: number;
    top: number;
  } | null>(null);
  const tone = OPTION_TONES[optionTone];

  const syncPopoverPosition = () => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const margin = 8;
    const width = popoverWidth ?? DEFAULT_POPOVER_WIDTH;
    const left = Math.min(Math.max(margin, rect.left), window.innerWidth - width - margin);
    const top = placement === "top" ? rect.top - 8 : rect.bottom + 8;
    setPopoverPosition({ left, top });
  };

  useEffect(() => {
    if (!open) return;
    if (portal) syncPopoverPosition();
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rootRef.current?.contains(target) || popoverRef.current?.contains(target)) return;
      setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    const onViewportChange = () => syncPopoverPosition();
    document.addEventListener("mousedown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown);
    if (portal) {
      window.addEventListener("resize", onViewportChange);
      window.addEventListener("scroll", onViewportChange, true);
    }
    return () => {
      document.removeEventListener("mousedown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown);
      if (portal) {
        window.removeEventListener("resize", onViewportChange);
        window.removeEventListener("scroll", onViewportChange, true);
      }
    };
    // syncPopoverPosition 每次 render 重建，但读取的 ref / state 都是最新值，无需入依赖。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, portal, placement]);

  const renderEntry = (entry: ModelPickerEntry) => {
    const isActive = entry.value === selectedValue;
    const disabledReason = entry.disabledReason ?? null;
    const isDisabled = disabledReason != null && !isActive;
    const optionClass = cn(
      OPTION_BASE_CLASS,
      isActive ? tone.active : isDisabled ? tone.disabled : tone.idle,
    );
    const inner = (
      <>
        {isActive ? (
          <Check className="h-3.5 w-3.5 shrink-0" />
        ) : (
          <span className="inline-block h-3.5 w-3.5 shrink-0" />
        )}
        <span className="min-w-0 truncate">{entry.label}</span>
        {entry.channel ? (
          <ModelChannelBadge
            channel={entry.channel}
            className={cn("ml-auto", isDisabled && "opacity-50")}
          />
        ) : null}
      </>
    );
    if (isDisabled && disabledReason != null) {
      return (
        <button
          key={entry.value}
          type="button"
          role={withListboxRole ? "option" : undefined}
          aria-selected={withListboxRole ? isActive : undefined}
          aria-disabled
          onClick={(event) => {
            if (stopPropagation) event.stopPropagation();
          }}
          onMouseEnter={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            // 锚定到当前项的右下角：水平从图标右侧起，垂直略压住项底边。
            setDisabledTooltip({
              reason: disabledReason,
              left: rect.left + 36,
              top: rect.bottom - 6,
            });
          }}
          onMouseLeave={() => setDisabledTooltip(null)}
          className={optionClass}
        >
          {inner}
        </button>
      );
    }
    return (
      <button
        key={entry.value}
        type="button"
        role={withListboxRole ? "option" : undefined}
        aria-selected={withListboxRole ? isActive : undefined}
        onClick={() => {
          onSelect(entry.value);
          setOpen(false);
        }}
        className={optionClass}
      >
        {inner}
      </button>
    );
  };

  const renderGroup = (group: ModelPickerEntryGroup, groupIndex: number) => {
    const key = `${group.label ?? "group"}-${groupIndex}`;
    const heading = group.label ? (
      <div className="px-3 pb-0.5 pt-1.5 text-[10px] font-medium uppercase tracking-wide text-text-muted/85">
        {group.label}
      </div>
    ) : null;
    if (withListboxRole) {
      return (
        <div key={key} role="group" aria-label={group.label}>
          {heading}
          {group.options.map(renderEntry)}
        </div>
      );
    }
    // 画布模式：单组、无标题时保持原实现的结构（不额外包一层）。
    if (!group.label) {
      return <Fragment key={key}>{group.options.map(renderEntry)}</Fragment>;
    }
    return (
      <div key={key}>
        {heading}
        {group.options.map(renderEntry)}
      </div>
    );
  };

  const popoverBody = (
    <>
      {groups.map(renderGroup)}
      {groups.every((group) => group.options.length === 0) && emptyContent ? emptyContent : null}
    </>
  );

  const renderPopover = () => (
    <div
      ref={popoverRef}
      role={withListboxRole ? "listbox" : undefined}
      aria-label={withListboxRole ? ariaLabel : undefined}
      className={popoverClassName}
      style={
        portal
          ? {
              left: popoverPosition?.left,
              top: popoverPosition?.top,
              transform: placement === "top" ? "translateY(-100%)" : undefined,
            }
          : undefined
      }
      onPointerDown={stopPropagation ? (event) => event.stopPropagation() : undefined}
      onClick={stopPropagation ? (event) => event.stopPropagation() : undefined}
    >
      {listClassName ? <div className={listClassName}>{popoverBody}</div> : popoverBody}
    </div>
  );

  return (
    <div ref={rootRef} className={cn("relative", rootClassName, className)}>
      <button
        ref={triggerRef}
        type="button"
        role={withListboxRole ? "combobox" : undefined}
        aria-expanded={withListboxRole ? open : undefined}
        aria-haspopup={withListboxRole ? "listbox" : undefined}
        aria-label={withListboxRole ? ariaLabel : undefined}
        disabled={disabled}
        onClick={(event) => {
          if (stopPropagation) event.stopPropagation();
          const nextOpen = !open;
          if (nextOpen) onOpen?.();
          setOpen(nextOpen);
        }}
        className={triggerClassName}
      >
        {triggerIcon}
        <span className={triggerLabelClassName ?? "min-w-0 truncate"}>{triggerLabel}</span>
        {triggerChannel ? <ModelChannelBadge channel={triggerChannel} /> : null}
        <ChevronDown className={cn(chevronClassName, chevronRotateOnOpen && open && "rotate-180")} />
      </button>
      {open
        ? portal
          ? popoverPosition
            ? createPortal(renderPopover(), document.body)
            : null
          : renderPopover()
        : null}
      {open && disabledTooltip
        ? createPortal(
            <div
              className="pointer-events-none fixed z-[10001] max-w-[240px] rounded-lg bg-neutral-800/95 px-3 py-2 text-xs leading-5 text-white shadow-lg"
              style={{ left: disabledTooltip.left, top: disabledTooltip.top }}
            >
              {disabledTooltip.reason}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}