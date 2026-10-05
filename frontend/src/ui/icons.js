// Stroke icons on a 24 px grid; they take the text colour. Decorative by default: the button or
// label around them carries the accessible name.

import { svg } from "./dom.js";

const PATHS = {
  close: '<path d="M6 6l12 12M18 6 6 18"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
  eyeOff:
    '<path d="M9.9 5.7A9.8 9.8 0 0 1 12 5.5c6 0 9.5 6.5 9.5 6.5a17 17 0 0 1-2.6 3.4M6.6 6.6C3.9 8.3 2.5 12 2.5 12S6 18.5 12 18.5a9.3 9.3 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2M4 4l16 16"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.34-5.66L20 8.5"/><path d="M20 4v4.5h-4.5"/>',
  live: '<rect x="3" y="4.5" width="8" height="6.5" rx="1.75"/><rect x="13" y="4.5" width="8" height="6.5" rx="1.75"/><rect x="3" y="13" width="8" height="6.5" rx="1.75"/><rect x="13" y="13" width="8" height="6.5" rx="1.75"/>',
  events:
    '<path d="M6 16.5V11a6 6 0 1 1 12 0v5.5l1.5 2h-15L6 16.5Z"/><path d="M10 20.5a2 2 0 0 0 4 0"/>',
  activity: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  back: '<path d="M14.5 6 8.5 12l6 6"/>',
  forward: '<path d="m9.5 6 6 6-6 6"/>',
  bell: '<path d="M6 16.5V11a6 6 0 0 1 12 0v5.5l1.5 2h-15Z"/><path d="M10 20.5a2.2 2.2 0 0 0 4 0"/>',
  download: '<path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19.5h14"/>',
  signOut:
    '<path d="M14 4.5h3.5a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H14"/><path d="M10 16.5 5.5 12 10 7.5"/><path d="M5.5 12h9.5"/>',
  camera:
    '<rect x="2.5" y="6.5" width="13" height="11" rx="2.5"/><path d="m15.5 10.5 6-3.5v10l-6-3.5"/>',
};

/** @typedef {keyof typeof PATHS} IconName */

/** @param {IconName} name */
export function icon(name) {
  return svg(
    `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${PATHS[name]}</svg>`,
  );
}

/** The Vision Hub mark: a lens whose iris takes the current text colour. */
export function logo() {
  return svg(
    `<svg viewBox="0 0 32 32" aria-hidden="true" focusable="false"><circle cx="16" cy="16" r="14" fill="#0a1020" stroke="#8ab4ff" stroke-width="2.5"/><circle cx="16" cy="16" r="8" fill="none" stroke="#8ab4ff" stroke-opacity=".45" stroke-width="2"/><circle cx="16" cy="16" r="3.5" fill="currentColor"/></svg>`,
  );
}
