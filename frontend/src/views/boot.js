// Boot screen: shows whether the hub is reachable and keeps checking until it is.

import { checkHub } from "../api/health.js";
import { $ } from "../ui/dom.js";
import { enter } from "../ui/motion.js";

/** @typedef {import("../api/health.js").HubReport} HubReport */

const RECHECK_ONLINE_MS = 10_000;
const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 15_000;

/** @param {HTMLElement} root */
export function mountBoot(root) {
  const slot = (/** @type {string} */ name) => $(root, `[data-slot="${name}"]`);
  const report = slot("report");
  const title = slot("title");
  const detail = slot("detail");
  const command = slot("command");
  const retry = slot("retry");
  const countdown = slot("countdown");
  const retryButton = /** @type {HTMLButtonElement} */ ($(root, "[data-action=retry]"));
  const ripple = $(root, ".lens-ripple");

  let failures = 0;
  let checking = false;
  /** @type {number | undefined} */
  let timer;
  /** @type {number | undefined} */
  let ticker;
  let nextAt = 0;

  async function check() {
    if (checking) return;
    checking = true;
    stopTimers();
    retryButton.disabled = true;
    countdown.textContent = "Checking…";

    const result = await checkHub();
    checking = false;
    retryButton.disabled = false;
    failures = result.state === "online" ? 0 : failures + 1;
    show(result);
    schedule(result.state === "online" ? RECHECK_ONLINE_MS : backoff(failures));
  }

  /** @param {HubReport} result */
  function show(result) {
    const previous = root.dataset.state;
    retry.hidden = result.state === "online";
    const write = () => {
      title.textContent = result.title;
      detail.textContent = result.detail;
      command.textContent = result.command ?? "";
      command.hidden = !result.command;
    };
    if (previous === result.state && title.textContent === result.title) {
      write();
      return;
    }
    root.dataset.state = result.state;
    swapText(report, write);
    if (result.state === "online") pulse(ripple);
  }

  /** @param {number} delay */
  function schedule(delay) {
    // Hidden tabs don't poll; the visibilitychange handler checks again on return.
    if (document.hidden) {
      countdown.textContent = "";
      return;
    }
    nextAt = performance.now() + delay;
    timer = window.setTimeout(check, delay);
    if (root.dataset.state !== "online") {
      tick();
      ticker = window.setInterval(tick, 1000);
    }
  }

  function tick() {
    const seconds = Math.max(1, Math.ceil((nextAt - performance.now()) / 1000));
    countdown.textContent = `Retrying in ${seconds} s`;
  }

  function stopTimers() {
    window.clearTimeout(timer);
    window.clearInterval(ticker);
  }

  retryButton.addEventListener("click", check);
  window.addEventListener("online", check);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopTimers();
    else check();
  });

  check();
}

/** Exponential backoff with ±20 % jitter, so many tabs don't retry in lockstep.
 * @param {number} failures */
function backoff(failures) {
  const base = Math.min(RETRY_MAX_MS, RETRY_MIN_MS * 2 ** (failures - 1));
  return Math.round(base * (0.8 + Math.random() * 0.4));
}

/** Updates a block at once, then eases the new text in. The content never waits on an
 * animation, so throttled or paused tabs still show the current state.
 * @param {HTMLElement} element
 * @param {() => void} update */
function swapText(element, update) {
  update();
  element.getAnimations().forEach((animation) => animation.cancel());
  enter(element, [{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1 }], { duration: 320 });
}

/** One expanding ring around the lens when the hub comes online.
 * @param {HTMLElement} ring */
function pulse(ring) {
  enter(
    ring,
    [
      { opacity: 0.8, transform: "scale(0.45)" },
      { opacity: 0, transform: "scale(1.9)" },
    ],
    { duration: 1100 },
  );
}
