// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { useTranslation } from "react-i18next";
import "./brand-holiday-badge.css";

export function BrandHolidayBadge() {
  const { t } = useTranslation();
  const label = t("header.partyFounding105");

  return (
    <div className="brand-holiday-badge" aria-label={label}>
      <video
        src="/brand/party-founding-105.mp4"
        aria-hidden="true"
        autoPlay
        loop
        muted
        playsInline
        preload="metadata"
        className="brand-holiday-badge__image"
      />
      <span className="brand-holiday-badge__text">{label}</span>
    </div>
  );
}
