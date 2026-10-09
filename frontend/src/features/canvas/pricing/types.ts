// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export const PRICE_DISPLAY_CURRENCY_MODES = ['auto', 'cny', 'usd'] as const;
export type PriceDisplayCurrencyMode = (typeof PRICE_DISPLAY_CURRENCY_MODES)[number];

export const PRICE_CURRENCIES = ['CNY', 'USD'] as const;
export type PriceCurrency = (typeof PRICE_CURRENCIES)[number];

export interface GrsaiCreditTierDefinition {
  id: string;
  priceCny: number;
  credits: number;
}

export const GRSAI_CREDIT_TIERS = [
  { id: 'tier-10', priceCny: 10, credits: 100000 },
  { id: 'tier-20', priceCny: 20, credits: 250000 },
  { id: 'tier-49', priceCny: 49, credits: 750000 },
  { id: 'tier-99', priceCny: 99, credits: 1600000 },
  { id: 'tier-499', priceCny: 499, credits: 9000000 },
  { id: 'tier-999', priceCny: 999, credits: 20000000 },
] as const satisfies readonly GrsaiCreditTierDefinition[];

export type GrsaiCreditTierId = (typeof GRSAI_CREDIT_TIERS)[number]['id'];

export const DEFAULT_GRSAI_CREDIT_TIER_ID: GrsaiCreditTierId = 'tier-10';

export interface PricingSettingsSnapshot {
  displayCurrencyMode: PriceDisplayCurrencyMode;
  usdToCnyRate: number;
  preferDiscountedPrice: boolean;
  grsaiCreditTierId: GrsaiCreditTierId;
}

export interface PriceComputationContext {
  resolution: string;
  extraParams?: Record<string, unknown>;
  settings: PricingSettingsSnapshot;
}

export interface ModelPriceQuote {
  amount: number;
  currency: PriceCurrency;
  originalAmount?: number;
  originalCurrency?: PriceCurrency;
  pointsCost?: number;
  metadata?: Record<string, string | number | boolean>;
}

export interface ModelPricingDefinition {
  quote: (context: PriceComputationContext) => ModelPriceQuote | null;
}

