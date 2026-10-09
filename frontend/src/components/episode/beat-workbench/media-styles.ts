// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
export const MEDIA_GRID_CLASS = "grid grid-cols-[minmax(0,2fr)_minmax(220px,3fr)] gap-3";

export const MEDIA_PREVIEW_CLASS =
  "flex min-h-[150px] cursor-zoom-in items-center justify-center overflow-hidden rounded-lg border border-white/[0.08] bg-white/[0.035] transition-[border-color,background-color,opacity] hover:border-white/[0.14] hover:bg-white/[0.05] hover:opacity-95";

export const MEDIA_PREVIEW_IMAGE_CLASS = "max-h-[180px] w-full object-contain";

export const MEDIA_EMPTY_CLASS =
  "flex h-[150px] items-center justify-center rounded-lg border border-dashed border-white/[0.08] bg-white/[0.025] text-xs text-muted-foreground";

export const MEDIA_THUMB_CLASS =
  "relative shrink-0 overflow-hidden rounded-[5px] border bg-white/[0.03] transition-[border-color,background-color,box-shadow,opacity] disabled:opacity-60";

export const MEDIA_THUMB_ACTIVE_CLASS =
  "border-primary/70 bg-primary/[0.06] shadow-[0_0_0_1px_rgba(20,184,166,0.24)]";

export const MEDIA_THUMB_IDLE_CLASS = "border-white/[0.08] hover:border-primary/55 hover:bg-white/[0.05]";

export const MEDIA_THUMB_NEW_CLASS =
  "absolute left-0 top-0 rounded-br bg-amber-500/95 px-1 text-[8px] font-semibold uppercase leading-4 text-white";

export const MEDIA_THUMB_TIME_CLASS =
  "absolute bottom-0 left-0 rounded-tr bg-black/75 px-1.5 py-0.5 text-[11px] font-medium leading-none tabular-nums text-white/90";

export const MEDIA_THUMB_ACTIVE_MARK_CLASS =
  "absolute bottom-0 right-0 rounded-tl bg-primary px-1 text-[9px] leading-4 text-primary-foreground";

export const CROP_DIALOG_SAVE_BUTTON_CLASS =
  "gap-1 rounded-[8px] border border-cyan-100/25 !bg-[#2ED7E5] !text-[#061316] shadow-[0_0_0_1px_rgba(103,232,249,0.18)] hover:!bg-[#5EEAF3] hover:!text-[#061316] disabled:border-white/[0.06] disabled:!bg-white/[0.08] disabled:!text-white/35 disabled:shadow-none [&_svg]:!text-[#061316] disabled:[&_svg]:!text-white/35";

export const MEDIA_PRIMARY_ACTION_BUTTON_CLASS =
  "gap-1 rounded-[7px] border-white/[0.115] bg-white/[0.032] text-foreground/82 shadow-none transition-[background-color,border-color,color,transform] hover:border-white/[0.18] hover:bg-white/[0.055] hover:text-foreground active:scale-95 disabled:border-white/[0.07] disabled:bg-white/[0.018] disabled:text-muted-foreground/45 dark:border-white/[0.115] dark:bg-white/[0.032] dark:hover:border-white/[0.18] dark:hover:bg-white/[0.055]";
