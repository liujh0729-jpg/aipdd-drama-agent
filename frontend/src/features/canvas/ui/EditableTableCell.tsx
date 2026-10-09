// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useEffect, useRef } from 'react';

interface EditableTableCellProps {
  value: string;
  onCommit: (nextValue: string) => void;
  /** 占位符（cell 为空时显示，跟只读模式的「-」对齐）。 */
  emptyPlaceholder?: string;
}

/**
 * 分镜 / 脚本类表格里的单格 inline 编辑器。uncontrolled contentEditable
 * —— 输入时不要 re-render，外部 value 变化（重新生成 / 别处编辑）才同步
 * 回 DOM；blur 时 commit；Esc 取消；paste 强制纯文本；keydown 阻止冒泡，
 * 防 React Flow 用 Backspace/Delete 误删节点。
 */
export function EditableTableCell({ value, onCommit, emptyPlaceholder = '-' }: EditableTableCellProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (el.innerText !== value) {
      el.innerText = value;
    }
  }, [value]);

  return (
    <div
      ref={ref}
      contentEditable
      suppressContentEditableWarning
      spellCheck={false}
      data-empty-placeholder={emptyPlaceholder}
      className="editable-table-cell nodrag nowheel -mx-1 block min-h-[1.2em] cursor-text whitespace-pre-wrap break-words rounded px-1 leading-snug outline-none focus:bg-bg-dark/60 focus:ring-1 focus:ring-[rgb(var(--accent-rgb)/0.4)]"
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
      onBlur={(event) => {
        const next = event.currentTarget.innerText;
        if (next !== value) {
          onCommit(next);
        }
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape') {
          if (ref.current) {
            ref.current.innerText = value;
          }
          event.currentTarget.blur();
        }
      }}
      onPaste={(event) => {
        event.preventDefault();
        const plain = event.clipboardData.getData('text/plain');
        document.execCommand('insertText', false, plain);
      }}
    />
  );
}
