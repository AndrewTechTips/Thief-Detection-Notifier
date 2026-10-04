// Toast notifications. Newest sits closest to the screen edge; siblings glide (FLIP) as toasts
// come and go. Timers pause while the pointer or focus is on a toast and while the tab is
// hidden, so an alert raised in the background is still there when you come back.

import { h } from "./dom.js";
import { icon } from "./icons.js";
import { EASE_OUT, enter, exit, flip, reducedMotion } from "./motion.js";

/**
 * @typedef {"iris" | "signal" | "sodium" | "alarm"} Tone
 * @typedef {{
 *   title: string,
 *   message?: string,
 *   tone?: Tone,
 *   duration?: number,
 *   key?: string,
 *   action?: { label: string, run: () => void },
 * }} ToastOptions
 * @typedef {{ dismiss: () => void }} ToastHandle
 */

const MAX_VISIBLE = 4;
const DEFAULT_MS = 5000;
const ALARM_MS = 9000;
const SWIPE_DISMISS_PX = 72;

/** @type {HTMLElement | undefined} */
let region;
/** @type {Map<string, Toast>} */
const byKey = new Map();
/** @type {Set<Toast>} */
const active = new Set();

/**
 * Shows a toast. With `key`, a toast that is already showing is updated instead of stacking a
 * duplicate. `duration: 0` keeps it until dismissed.
 * @param {ToastOptions} options
 * @returns {ToastHandle}
 */
export function toast(options) {
  const existing = options.key ? byKey.get(options.key) : undefined;
  if (existing) {
    existing.update(options);
    return existing;
  }
  const item = new Toast(options);
  if (active.size >= MAX_VISIBLE) [...active][0].dismiss();
  item.show();
  return item;
}

function getRegion() {
  if (!region) {
    region = h("div", {
      class: "toast-region",
      attrs: { role: "region", "aria-label": "Notifications", "aria-live": "polite" },
    });
    document.body.append(region);
    document.addEventListener("visibilitychange", () => {
      for (const item of active) item.setPaused("hidden", document.hidden);
    });
  }
  return region;
}

class Toast {
  /** @param {ToastOptions} options */
  constructor(options) {
    this.options = options;
    this.dot = h("span", { class: "dot", attrs: { "aria-hidden": "true" } });
    this.title = h("p", { class: "toast-title" });
    this.message = h("p", { class: "toast-message" });
    this.actions = h("div", { class: "toast-actions" });
    this.bar = h("span", { class: "toast-timer", attrs: { "aria-hidden": "true" } });
    const close = h(
      "button",
      {
        class: "btn btn-ghost btn-icon btn-sm -mt-1",
        attrs: { type: "button", "aria-label": "Dismiss" },
        on: { click: () => this.dismiss() },
      },
      icon("close"),
    );
    this.element = h(
      "div",
      { class: "toast" },
      this.dot,
      h("div", { class: "min-w-0" }, this.title, this.message, this.actions),
      close,
      this.bar,
    );

    /** @type {Set<string>} */
    this.pauses = new Set();
    this.remaining = 0;
    this.startedAt = 0;
    /** @type {number | undefined} */
    this.timeout = undefined;
    /** @type {Animation | undefined} */
    this.barAnimation = undefined;
    this.closed = false;

    this.render();
    this.listen();
  }

  render() {
    const { title, message, tone, action } = this.options;
    if (tone) this.element.dataset.tone = tone;
    else delete this.element.dataset.tone;
    // Alarms interrupt a screen reader; everything else waits for a pause.
    this.element.setAttribute("role", tone === "alarm" ? "alert" : "status");
    this.title.textContent = title;
    this.message.textContent = message ?? "";
    this.message.hidden = !message;
    this.actions.replaceChildren();
    this.actions.hidden = !action;
    if (action) {
      this.actions.append(
        h("button", {
          class: "btn btn-secondary btn-sm",
          attrs: { type: "button" },
          text: action.label,
          on: {
            click: () => {
              action.run();
              this.dismiss();
            },
          },
        }),
      );
    }
  }

  get duration() {
    const { duration, tone } = this.options;
    return duration ?? (tone === "alarm" ? ALARM_MS : DEFAULT_MS);
  }

  show() {
    const container = getRegion();
    if (this.options.key) byKey.set(this.options.key, this);
    active.add(this);
    flip(container.children, () => container.append(this.element));
    const y = getComputedStyle(container).getPropertyValue("--toast-enter-y").trim() || "16px";
    enter(this.element, [
      { opacity: 0, transform: `translateY(${y}) scale(0.97)` },
      { opacity: 1, transform: "none" },
    ]);
    if (document.hidden) this.pauses.add("hidden");
    this.restartTimer();
  }

  /** @param {ToastOptions} options */
  update(options) {
    this.options = options;
    this.render();
    this.restartTimer();
    enter(this.element, [{ transform: "scale(1.025)" }, { transform: "none" }], {
      duration: 260,
      composite: "add",
    });
  }

  restartTimer() {
    window.clearTimeout(this.timeout);
    this.barAnimation?.cancel();
    this.barAnimation = undefined;
    this.remaining = this.duration;
    this.bar.hidden = this.remaining <= 0 || reducedMotion();
    if (this.remaining <= 0) return;
    if (!this.bar.hidden) {
      this.barAnimation = this.bar.animate(
        [{ transform: "scaleX(1)" }, { transform: "scaleX(0)" }],
        { duration: this.remaining, easing: "linear", fill: "forwards" },
      );
    }
    if (this.pauses.size) this.barAnimation?.pause();
    else this.startTimer();
  }

  startTimer() {
    if (this.remaining <= 0 || this.closed) return;
    this.startedAt = performance.now();
    this.timeout = window.setTimeout(() => this.dismiss(), this.remaining);
    this.barAnimation?.play();
  }

  /**
   * @param {string} reason
   * @param {boolean} paused
   */
  setPaused(reason, paused) {
    const wasPaused = this.pauses.size > 0;
    if (paused) this.pauses.add(reason);
    else this.pauses.delete(reason);
    const isPaused = this.pauses.size > 0;
    if (wasPaused === isPaused || this.duration <= 0) return;
    if (isPaused) {
      window.clearTimeout(this.timeout);
      this.remaining -= performance.now() - this.startedAt;
      this.barAnimation?.pause();
    } else {
      this.startTimer();
    }
  }

  listen() {
    const el = this.element;
    el.addEventListener("pointerenter", () => this.setPaused("pointer", true));
    el.addEventListener("pointerleave", () => this.setPaused("pointer", false));
    el.addEventListener("focusin", () => this.setPaused("focus", true));
    el.addEventListener("focusout", (event) => {
      if (!el.contains(/** @type {Node | null} */ (event.relatedTarget))) {
        this.setPaused("focus", false);
      }
    });
    this.listenSwipe();
  }

  /** Horizontal swipe to dismiss (touch, pen or mouse drag). */
  listenSwipe() {
    const el = this.element;
    el.style.touchAction = "pan-y";
    let startX = 0;
    let dx = 0;
    let dragging = false;
    /** @type {number | null} */
    let pointer = null;

    el.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || /** @type {Element} */ (event.target).closest("button")) return;
      pointer = event.pointerId;
      startX = event.clientX;
      dx = 0;
      dragging = false;
    });
    el.addEventListener("pointermove", (event) => {
      if (event.pointerId !== pointer) return;
      dx = event.clientX - startX;
      if (!dragging && Math.abs(dx) > 6) {
        dragging = true;
        el.setPointerCapture(event.pointerId);
      }
      if (dragging) {
        el.style.transform = `translateX(${dx}px)`;
        el.style.opacity = String(Math.max(0.2, 1 - Math.abs(dx) / 220));
      }
    });
    const release = (/** @type {PointerEvent} */ event) => {
      if (event.pointerId !== pointer) return;
      pointer = null;
      if (!dragging) return;
      dragging = false;
      if (Math.abs(dx) >= SWIPE_DISMISS_PX) {
        this.dismiss(Math.sign(dx));
        return;
      }
      const from = el.style.transform;
      const fromOpacity = el.style.opacity;
      el.style.transform = "";
      el.style.opacity = "";
      enter(
        el,
        [
          { transform: from, opacity: fromOpacity },
          { transform: "none", opacity: 1 },
        ],
        {
          duration: 240,
          easing: EASE_OUT,
        },
      );
    };
    el.addEventListener("pointerup", release);
    el.addEventListener("pointercancel", release);
  }

  /** @param {number} [direction] swipe direction (-1 or 1); 0 fades out in place */
  dismiss(direction = 0) {
    if (this.closed) return;
    this.closed = true;
    window.clearTimeout(this.timeout);
    active.delete(this);
    if (this.options.key && byKey.get(this.options.key) === this) byKey.delete(this.options.key);

    const el = this.element;
    el.style.pointerEvents = "none";
    const from = el.style.transform || "none";
    const to = direction ? `translateX(${direction * 110}%)` : "scale(0.96)";
    exit(el, [
      { opacity: Number(el.style.opacity || 1), transform: from },
      { opacity: 0, transform: to },
    ]).then(() => {
      const container = getRegion();
      flip(container.children, () => el.remove());
    });
  }
}
