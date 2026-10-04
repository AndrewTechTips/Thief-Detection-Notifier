// Animation helpers (Web Animations API). Rules (AD-20): only transform and opacity move, the
// DOM is always updated first, and nothing waits on an animation without a timeout, because
// hidden or throttled tabs may never finish one.

export const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)";
export const EASE_IN = "cubic-bezier(0.55, 0, 1, 0.45)";

const reduced = matchMedia("(prefers-reduced-motion: reduce)");

export function reducedMotion() {
  return reduced.matches;
}

/**
 * Plays an entrance on an element that is already in its final state.
 * @param {Element} element
 * @param {Keyframe[]} keyframes
 * @param {KeyframeAnimationOptions} [options]
 */
export function enter(element, keyframes, options = {}) {
  if (reducedMotion()) return;
  element.animate(keyframes, { duration: 360, easing: EASE_OUT, ...options });
}

/**
 * Plays an exit and resolves when it ends, or after its duration if the tab is not animating.
 * @param {Element} element
 * @param {Keyframe[]} keyframes
 * @param {KeyframeAnimationOptions & { duration?: number }} [options]
 * @returns {Promise<void>}
 */
export function exit(element, keyframes, options = {}) {
  if (reducedMotion()) return Promise.resolve();
  const duration = options.duration ?? 180;
  const animation = element.animate(keyframes, {
    duration,
    easing: EASE_IN,
    fill: "forwards",
    ...options,
  });
  const timeout = new Promise((resolve) => setTimeout(resolve, duration + 100));
  return Promise.race([animation.finished, timeout]).then(
    () => undefined,
    () => undefined, // cancelled
  );
}

/**
 * FLIP: runs `mutate`, then slides each element from its old position to its new one, so
 * siblings glide instead of jumping when an item is added or removed.
 * @param {Iterable<Element>} elements
 * @param {() => void} mutate
 */
export function flip(elements, mutate) {
  const before = new Map(
    [...elements].map((element) => [element, element.getBoundingClientRect()]),
  );
  mutate();
  if (reducedMotion()) return;
  for (const [element, first] of before) {
    if (!element.isConnected) continue;
    const last = element.getBoundingClientRect();
    const dx = first.left - last.left;
    const dy = first.top - last.top;
    if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
    element.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], {
      duration: 280,
      easing: EASE_OUT,
      composite: "add",
    });
  }
}
