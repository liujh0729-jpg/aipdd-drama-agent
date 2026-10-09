// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// Pan-canvas device icons for the shortcuts panel. One per input method:
// keyboard (Space + drag), trackpad (two-finger), mouse (middle button).
// Cyan accents use the canvas highlight color (#25FFE9).

interface PanIconProps {
  className?: string;
}

/** Keyboard: hold Space and drag — mouse body with a cyan cursor hint. */
export function KeyboardPanIcon({ className }: PanIconProps) {
  return (
    <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden className={className}>
      <rect x="9.5" y="6.5" width="13" height="19" rx="5.5" stroke="white" strokeOpacity="0.8" />
      <path
        d="M16 9.4V13.7534C16 14.3931 15.4076 14.8684 14.7831 14.7296L12.2831 14.174C11.8255 14.0723 11.5 13.6665 11.5 13.1978V12.1C11.5 10.1118 13.1118 8.5 15.1 8.5C15.5971 8.5 16 8.90294 16 9.4Z"
        fill="#25FFE9"
      />
    </svg>
  );
}

/** Trackpad: two-finger pan — hand on the surface with directional arrows. */
export function TrackpadPanIcon({ className }: PanIconProps) {
  return (
    <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden className={className}>
      <path
        d="M19.7033 5.51074C20.7117 5.6132 21.4992 6.46433 21.4992 7.5V14.0166L23.6633 14.3398C25.5431 14.6207 26.8902 16.3561 26.6799 18.249C26.47 20.1382 26.1604 22.0769 25.217 23.54C24.2383 25.0578 22.6247 25.9999 19.9992 26C16.9387 25.9999 14.9855 25.2712 13.343 24.0439C12.5343 23.4397 11.8113 22.723 11.0793 21.9434C10.3364 21.1521 9.60271 20.3175 8.71895 19.4111C7.96011 18.6319 7.9267 17.3648 8.71504 16.5762C9.42451 15.8667 10.5546 15.8043 11.3381 16.4307L14.4992 18.959V7.5C14.4992 6.39556 15.3948 5.50022 16.4992 5.5L16.7033 5.51074C17.2184 5.56308 17.6749 5.81172 17.9992 6.17969C18.3656 5.76372 18.9013 5.50012 19.4992 5.5L19.7033 5.51074ZM18.4992 15C18.4992 15.2761 18.2754 15.5 17.9992 15.5C17.7231 15.5 17.4992 15.2761 17.4992 15V7.5C17.4992 6.98265 17.1059 6.55642 16.6018 6.50488L16.4992 6.5C15.9471 6.50022 15.4992 6.94785 15.4992 7.5V19.7129C15.4988 20.2506 14.8774 20.5432 14.4621 20.2109L10.7131 17.2119C10.3274 16.9037 9.77113 16.9341 9.42207 17.2832C9.03975 17.6658 9.03931 18.3068 9.43574 18.7139C10.3228 19.6236 11.099 20.5027 11.8088 21.2588C12.5294 22.0264 13.2036 22.6924 13.9406 23.2432C15.39 24.3262 17.1275 24.9999 19.9992 25L20.426 24.9902C22.4988 24.8963 23.6585 24.1109 24.3762 22.998C25.1769 21.7563 25.4743 20.0411 25.6857 18.1377C25.836 16.783 24.8668 15.5299 23.5148 15.3281L20.4992 14.8779V7.5C20.4992 6.98265 20.1059 6.55642 19.6018 6.50488L19.4992 6.5C18.9471 6.50022 18.4992 6.94785 18.4992 7.5V15Z"
        fill="white"
        fillOpacity="0.8"
      />
      <g clipPath="url(#clip0_trackpad_pan_shortcut)">
        <path d="M7 2.83594V11.1693" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M8.25 9.91406L7 11.1641L5.75 9.91406" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M9.91797 5.75L11.168 7L9.91797 8.25" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M2.83203 7H11.1654" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M4.08203 5.75L2.83203 7L4.08203 8.25" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M5.75 4.08594L7 2.83594L8.25 4.08594" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
      </g>
      <defs>
        <clipPath id="clip0_trackpad_pan_shortcut">
          <rect width="10" height="10" fill="white" transform="translate(2 2)" />
        </clipPath>
      </defs>
    </svg>
  );
}

/** Mouse: middle-button drag — mouse body with a cyan wheel and arrows. */
export function MousePanIcon({ className }: PanIconProps) {
  return (
    <svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden className={className}>
      <rect x="13.5" y="8.5" width="13" height="19" rx="5.5" stroke="white" strokeOpacity="0.8" />
      <rect x="19" y="12" width="2" height="5" rx="1" fill="#25FFE9" />
      <g clipPath="url(#clip0_mouse_pan_shortcut)">
        <path d="M8 3.83594V12.1693" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M9.25 10.9141L8 12.1641L6.75 10.9141" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M10.918 6.75L12.168 8L10.918 9.25" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M3.83203 8H12.1654" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M5.08203 6.75L3.83203 8L5.08203 9.25" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M6.75 5.08594L8 3.83594L9.25 5.08594" stroke="#25FFE9" strokeLinecap="round" strokeLinejoin="round" />
      </g>
      <defs>
        <clipPath id="clip0_mouse_pan_shortcut">
          <rect width="10" height="10" fill="white" transform="translate(3 3)" />
        </clipPath>
      </defs>
    </svg>
  );
}
