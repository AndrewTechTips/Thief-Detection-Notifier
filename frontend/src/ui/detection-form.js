// Detection settings in plain words. The hub thinks in thresholds ("min_motion_area: 0.01");
// people think in "how sensitive" and "how long until it counts". Each control says what it
// changes; the raw value stays visible for anyone tuning precisely.

import { h } from "./dom.js";

/** @typedef {import("../api/types.js").DetectionConfig} DetectionConfig */

// Sensitivity runs on a log scale: from 5 % of the frame (only big movements) down to 0.1 %
// (a cat at the far end of the garden).
const AREA_LEAST = 0.05;
const AREA_MOST = 0.001;

/** @param {number} area fraction of the frame @returns {number} 0..100, 100 = most sensitive */
export function sensitivityOf(area) {
  const t = (Math.log(area) - Math.log(AREA_LEAST)) / (Math.log(AREA_MOST) - Math.log(AREA_LEAST));
  return Math.round(Math.min(1, Math.max(0, t)) * 100);
}

/** @param {number} sensitivity 0..100 @returns {number} fraction of the frame */
export function areaOf(sensitivity) {
  const t = sensitivity / 100;
  const area = Math.exp(Math.log(AREA_LEAST) + t * (Math.log(AREA_MOST) - Math.log(AREA_LEAST)));
  return Number(area.toPrecision(2));
}

/** @param {number} sensitivity */
function sensitivityLabel(sensitivity) {
  if (sensitivity < 25) return "Low";
  if (sensitivity < 50) return "Medium";
  if (sensitivity < 75) return "High";
  return "Very high";
}

/** @param {number} fraction */
function percent(fraction) {
  const value = fraction * 100;
  return `${value < 1 ? value.toFixed(1) : Math.round(value)} %`;
}

/**
 * @param {{
 *   config: DetectionConfig,
 *   onChange: (config: DetectionConfig) => void,
 *   disabled?: boolean,
 * }} options
 */
export function detectionForm({ config: initial, onChange, disabled = false }) {
  let config = { ...initial };

  /**
   * One labelled slider with a live description.
   * @param {{
   *   id: string,
   *   label: string,
   *   min: number,
   *   max: number,
   *   step: number,
   *   read: (config: DetectionConfig) => number,
   *   write: (value: number) => Partial<DetectionConfig>,
   *   describe: (value: number) => { value: string, hint: string },
   * }} spec
   */
  function slider(spec) {
    const input = h("input", {
      class: "slider",
      attrs: {
        type: "range",
        id: spec.id,
        min: String(spec.min),
        max: String(spec.max),
        step: String(spec.step),
        "aria-describedby": `${spec.id}-hint`,
        disabled,
      },
    });
    const value = h("span", { class: "text-sm tabular-nums text-moon" });
    const hint = h("p", { class: "hint", attrs: { id: `${spec.id}-hint` } });
    const element = h(
      "div",
      { class: "field" },
      h(
        "div",
        { class: "flex items-baseline justify-between gap-3" },
        h("label", { class: "label", attrs: { for: spec.id }, text: spec.label }),
        value,
      ),
      input,
      hint,
    );
    function render() {
      const current = spec.read(config);
      input.value = String(current);
      const words = spec.describe(current);
      value.textContent = words.value;
      input.setAttribute("aria-valuetext", words.value);
      hint.textContent = words.hint;
      input.style.setProperty("--fill", `${((current - spec.min) / (spec.max - spec.min)) * 100}%`);
    }
    input.addEventListener("input", () => {
      config = { ...config, ...spec.write(Number(input.value)) };
      render();
      onChange({ ...config });
    });
    render();
    return { element, render };
  }

  const controls = [
    slider({
      id: "detect-sensitivity",
      label: "Sensitivity",
      min: 0,
      max: 100,
      step: 1,
      read: (c) => sensitivityOf(c.min_motion_area),
      write: (v) => ({ min_motion_area: areaOf(v) }),
      describe: (v) => ({
        value: sensitivityLabel(v),
        hint: `Reacts to movement covering ${percent(areaOf(v))} of the picture or more.`,
      }),
    }),
    slider({
      id: "detect-threshold",
      label: "Ignore small changes",
      min: 10,
      max: 120,
      step: 1,
      read: (c) => c.pixel_threshold,
      write: (v) => ({ pixel_threshold: v }),
      describe: (v) => ({
        value: String(v),
        hint: "How much a pixel must change to count. Raise it if shadows, rain or flicker set off alerts.",
      }),
    }),
    slider({
      id: "detect-confirm",
      label: "Confirm before alerting",
      min: 1,
      max: 10,
      step: 1,
      read: (c) => c.min_motion_frames,
      write: (v) => ({ min_motion_frames: v }),
      describe: (v) => ({
        value: `${v} ${v === 1 ? "frame" : "frames"}`,
        hint: "Motion must show in this many analysed frames in a row before an event starts.",
      }),
    }),
    slider({
      id: "detect-grace",
      label: "End after stillness",
      min: 0.5,
      max: 15,
      step: 0.5,
      read: (c) => c.motion_end_grace_seconds,
      write: (v) => ({ motion_end_grace_seconds: v }),
      describe: (v) => ({
        value: `${v} s`,
        hint: "An event ends after this long without motion. Longer keeps one visit as one event.",
      }),
    }),
  ];

  return {
    element: h(
      "div",
      { class: "grid gap-5" },
      controls.map((control) => control.element),
    ),
    /** Shows another config without reporting a change (after saving or resetting).
     * @param {DetectionConfig} next */
    set(next) {
      config = { ...next };
      controls.forEach((control) => control.render());
    },
  };
}
