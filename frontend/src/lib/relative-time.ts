// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import dayjs from "dayjs";
import type { TFunction } from "i18next";

/**
 * Format a timestamp as a localized relative string with named buckets:
 * "just now", "3 minutes ago", "2 hours ago", "yesterday", "3 days ago",
 * "last week", "2 weeks ago", "last month", "3 months ago", "last year".
 *
 * Boundaries use calendar-day differences (not raw elapsed milliseconds), so
 * a timestamp from 11pm yesterday reads "yesterday" at 1am today — not "2
 * hours ago".
 */
export function formatRelativeTime(
  input: string | number | Date,
  t: TFunction,
  now: Date = new Date(),
): string {
  const then = dayjs(input);
  const current = dayjs(now);
  if (!then.isValid()) return "";

  const diffSec = current.diff(then, "second");
  if (diffSec < 45) return t("time.justNow");

  const diffMin = current.diff(then, "minute");
  if (diffMin < 60) return t("time.minutesAgo", { count: diffMin });

  const startOfToday = current.startOf("day");
  const startOfThen = then.startOf("day");
  const dayDelta = startOfToday.diff(startOfThen, "day");

  if (dayDelta === 0) {
    const hours = Math.max(1, current.diff(then, "hour"));
    return t("time.hoursAgo", { count: hours });
  }

  if (dayDelta === 1) return t("time.yesterday");
  if (dayDelta < 7) return t("time.daysAgo", { count: dayDelta });

  const weekDelta = Math.floor(dayDelta / 7);
  if (weekDelta === 1) return t("time.lastWeek");
  if (weekDelta < 4) return t("time.weeksAgo", { count: weekDelta });

  const monthDelta = current.diff(then, "month");
  if (monthDelta <= 1) return t("time.lastMonth");
  if (monthDelta < 12) return t("time.monthsAgo", { count: monthDelta });

  const yearDelta = current.diff(then, "year");
  if (yearDelta <= 1) return t("time.lastYear");
  return t("time.yearsAgo", { count: yearDelta });
}
