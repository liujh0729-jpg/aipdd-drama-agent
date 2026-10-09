// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from "react-i18next";
import { PRODUCT_MANUAL_URL } from "@/lib/product-manual";

interface CanvasHelpMenuProps {
  onClose: () => void;
}

interface HelpMenuItem {
  key: string;
  labelKey: string;
  href: string;
}

const HELP_MENU_ITEMS: HelpMenuItem[] = [
  {
    key: "tutorial",
    labelKey: "canvas.quickbar.helpMenu.tutorial",
    href: PRODUCT_MANUAL_URL,
  },
];

export function CanvasHelpMenu({ onClose }: CanvasHelpMenuProps) {
  const { t } = useTranslation();

  return (
    <div className="nopan nowheel min-w-[176px] overflow-hidden rounded-[12px] border border-white/[0.08] bg-[#11151d]/95 py-1.5 shadow-[0_14px_36px_rgba(0,0,0,0.42)] backdrop-blur-md">
      {HELP_MENU_ITEMS.map((item) => (
        <a
          key={item.key}
          href={item.href}
          target="_blank"
          rel="noopener noreferrer"
          onClick={onClose}
          className="block px-4 py-2.5 text-sm text-white/72 transition-colors hover:bg-white/[0.08] hover:text-white"
        >
          {t(item.labelKey)}
        </a>
      ))}
    </div>
  );
}
