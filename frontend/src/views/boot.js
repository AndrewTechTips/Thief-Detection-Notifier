// Boot screen: shown until the hub answers. Renders the shared hub monitor (state/hub.js).

import { checkHubNow, hub } from "../state/hub.js";
import { $ } from "../ui/dom.js";
import { enter } from "../ui/motion.js";

/** @typedef {import("../state/hub.js").HubStatus} HubStatus */

/**
 * @param {HTMLElement} root
 * @returns {() => void} cleanup
 */
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

  /** @type {HubStatus | undefined} */
  let status;

  function tick() {
    if (!status || status.checking) countdown.textContent = "Checking…";
    else if (status.nextCheckAt === null) countdown.textContent = "";
    else {
      const seconds = Math.max(1, Math.ceil((status.nextCheckAt - Date.now()) / 1000));
      countdown.textContent = `Retrying in ${seconds} s`;
    }
  }

  const unsubscribe = hub.subscribe((next) => {
    const previous = status;
    status = next;
    retry.hidden = next.state === "online" || next.state === "checking";
    retryButton.disabled = next.checking;
    tick();
    if (previous && previous.state === next.state && previous.title === next.title) return;

    root.dataset.state = next.state;
    title.textContent = next.title;
    detail.textContent = next.detail;
    command.textContent = next.command ?? "";
    command.hidden = !next.command;
    if (previous) {
      report.getAnimations().forEach((animation) => animation.cancel());
      enter(report, [{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1 }], {
        duration: 320,
      });
    }
    if (next.state === "online") pulse(ripple);
  });

  const ticker = window.setInterval(tick, 1000);
  retryButton.addEventListener("click", checkHubNow);

  return () => {
    unsubscribe();
    window.clearInterval(ticker);
    retryButton.removeEventListener("click", checkHubNow);
  };
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
