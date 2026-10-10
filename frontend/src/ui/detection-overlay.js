// Boxes over a live picture, drawn from the detections that come with each frame (AD-23), so
// they match the picture exactly and the video itself stays clean. The layer covers exactly the
// picture (the view takes the frame's aspect ratio), so fractions of the frame become
// percentages. Boxes jump with the picture; only their fading out is animated (AD-20).

import { boxesShown, onBoxesChange } from "./boxes.js";
import { h } from "./dom.js";

/** @typedef {import("../realtime/mjpeg.js").Detections} Detections */

/** More boxes than this is noise (rain, a lighting flicker): the largest are kept. */
const MAX_BOXES = 24;

export function detectionOverlay() {
  const element = h("div", {
    class: "detection-layer",
    attrs: { "aria-hidden": "true", "data-idle": "" },
  });
  /** @type {HTMLElement[]} */
  const pool = [];

  function apply(/** @type {boolean} */ shown) {
    element.hidden = !shown;
  }
  apply(boxesShown());
  const unsubscribe = onBoxesChange(apply);

  /** Fades the boxes out where they are; the next detections bring them back. */
  function idle() {
    element.toggleAttribute("data-idle", true);
  }

  return {
    element,
    /** @param {Detections | null} detections what was detected on the frame just painted */
    render(detections) {
      const boxes = layout(detections);
      if (boxes.length === 0) {
        idle();
        return;
      }
      element.toggleAttribute("data-idle", false);
      element.toggleAttribute("data-person", (detections?.person ?? null) !== null);
      while (pool.length < boxes.length) {
        const box = h("div", { class: "detection-box" });
        pool.push(box);
        element.append(box);
      }
      pool.forEach((box, i) => {
        const place = boxes[i];
        box.hidden = !place;
        if (place) Object.assign(box.style, place);
      });
    },
    idle,
    destroy: unsubscribe,
  };
}

/**
 * Where each box goes, as CSS percentages of the picture: the largest boxes first, at most
 * MAX_BOXES, clipped to the picture.
 * @param {Detections | null} detections
 * @returns {{ left: string, top: string, width: string, height: string }[]}
 */
export function layout(detections) {
  return [...(detections?.boxes ?? [])]
    .sort((a, b) => b[2] * b[3] - a[2] * a[3])
    .slice(0, MAX_BOXES)
    .map(([x, y, width, height]) => {
      const left = clamp(x);
      const top = clamp(y);
      return {
        left: percent(left),
        top: percent(top),
        width: percent(clamp(x + width) - left),
        height: percent(clamp(y + height) - top),
      };
    });
}

/** @param {number} fraction */
const clamp = (fraction) => Math.min(Math.max(fraction, 0), 1);

/** @param {number} fraction */
const percent = (fraction) => `${(fraction * 100).toFixed(2)}%`;
