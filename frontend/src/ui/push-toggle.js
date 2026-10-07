// "Notifications": a switch for motion alerts on this device, with a test once they're on.
// Hidden where push can't work at all (an unsupported browser, or push turned off on the hub).

import { describeError } from "../api/errors.js";
import { disablePush, enablePush, push, sendTestPush } from "../push.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { toast } from "./toast.js";

/** @type {Record<import("../push.js").PushState, string>} */
const HINT = {
  off: "Get alerts on this device.",
  on: "On for this device.",
  denied: "Blocked in this browser's settings for the site.",
  install: "Add Vision Hub to your Home Screen to get alerts.",
  unsupported: "",
  "hub-off": "",
};

let toggleId = 0;

export function pushToggle() {
  const id = `push-toggle-${++toggleId}`;
  const input = h("input", {
    class: "switch",
    attrs: { type: "checkbox", role: "switch", id, "aria-describedby": `${id}-hint` },
  });
  const hint = h("p", { class: "text-xs text-haze", attrs: { id: `${id}-hint` } });
  const test = h("button", {
    class: "btn btn-ghost btn-sm push-test",
    attrs: { type: "button" },
    text: "Send a test",
  });
  const element = h(
    "div",
    { class: "push-toggle" },
    h(
      "div",
      { class: "push-row" },
      h("span", { class: "push-icon", attrs: { "aria-hidden": "true" } }, icon("bell")),
      h(
        "div",
        { class: "min-w-0 flex-1" },
        h("label", {
          class: "block text-sm font-medium",
          attrs: { for: id },
          text: "Notifications",
        }),
        hint,
      ),
      input,
    ),
    test,
  );

  input.addEventListener("change", async () => {
    const on = input.checked;
    try {
      if (on) await enablePush();
      else await disablePush();
    } catch (error) {
      const { detail } = describeError(error);
      const title = on ? "Couldn't turn on notifications" : "Couldn't turn off notifications";
      toast({ tone: "alarm", title, message: detail });
    }
    render(); // the store may not have changed (permission dismissed): put the switch back
  });

  test.addEventListener("click", async () => {
    test.setAttribute("aria-busy", "true");
    try {
      const { delivered, failed } = await sendTestPush();
      toast(
        delivered
          ? { tone: "signal", title: "Test sent", message: "It should appear in a moment." }
          : failed
            ? {
                tone: "sodium",
                title: "The push service didn't take it",
                message: "Try again in a minute. The hub's log has the details.",
              }
            : {
                tone: "sodium",
                title: "Nothing to send to",
                message: "This device isn't subscribed any more. Turn notifications off and on.",
              },
      );
    } catch (error) {
      const { title, detail } = describeError(error);
      toast({ tone: "alarm", title, message: detail });
    } finally {
      test.removeAttribute("aria-busy");
    }
  });

  function render() {
    const { state, busy } = push.get();
    element.hidden = state === "unsupported" || state === "hub-off";
    input.checked = state === "on";
    input.disabled = busy || state === "denied" || state === "install";
    input.toggleAttribute("aria-busy", busy);
    hint.textContent = HINT[state];
    test.hidden = state !== "on";
  }

  const unsubscribe = push.subscribe(render);
  return { element, destroy: unsubscribe };
}
