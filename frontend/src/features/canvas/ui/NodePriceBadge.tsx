// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
type NodePriceBadgeProps = {
  label: string;
  title?: string;
};

export function NodePriceBadge({ label, title }: NodePriceBadgeProps) {
  return (
    <span
      title={title}
      className="mr-2 shrink-0 text-[14px] leading-none font-normal text-[rgba(15,23,42,0.68)] dark:text-white/55"
    >
      {label}
    </span>
  );
}

