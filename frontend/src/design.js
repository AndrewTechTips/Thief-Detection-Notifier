// Component gallery (dev only): renders swatches and wires the interactive demos.

import "./styles/main.css";

import { $, h } from "./ui/dom.js";
import { passwordToggle } from "./ui/forms.js";
import { icon } from "./ui/icons.js";
import { toast } from "./ui/toast.js";

const SWATCHES = {
  Surfaces: [
    ["ink-950", "Page"],
    ["ink-900", "Wells, inputs"],
    ["ink-800", "Opaque panels"],
    ["ink-700", "Raised controls"],
    ["ink-600", "Strong borders"],
  ],
  Text: [
    ["moon", "Primary"],
    ["haze", "Secondary"],
    ["dusk", "Placeholder, disabled"],
  ],
  Meaning: [
    ["iris", "Actions, focus"],
    ["signal", "Online, healthy"],
    ["sodium", "Motion, attention"],
    ["alarm", "Offline, errors"],
  ],
};

const styles = getComputedStyle(document.documentElement);
$(document, "[data-swatches]").append(
  ...Object.entries(SWATCHES).map(([group, colours]) =>
    h(
      "div",
      {},
      h("h3", { class: "text-sm font-medium text-haze", text: group }),
      h(
        "ul",
        { class: "mt-3 space-y-2.5" },
        colours.map(([name, job]) =>
          h(
            "li",
            { class: "flex items-center gap-3" },
            h("span", {
              class: "size-9 flex-none rounded-[var(--radius-control)] border border-white/10",
              attrs: { style: `background: var(--color-${name})` },
            }),
            h(
              "span",
              { class: "min-w-0" },
              h("span", { class: "block text-sm font-medium", text: name }),
              h("span", {
                class: "block text-xs text-haze",
                text: `${job}, ${styles.getPropertyValue(`--color-${name}`).trim()}`,
              }),
            ),
          ),
        ),
      ),
    ),
  ),
);

for (const button of document.querySelectorAll("[data-icon]")) {
  button.append(
    icon(/** @type {import("./ui/icons.js").IconName} */ (button.getAttribute("data-icon"))),
  );
}

const busy = $(document, "[data-demo=busy]");
busy.addEventListener("click", () => {
  busy.setAttribute("aria-busy", "true");
  setTimeout(() => busy.removeAttribute("aria-busy"), 2000);
});

passwordToggle($(document, "#demo-password"));
$(document, "[data-demo=form]").addEventListener("submit", (event) => event.preventDefault());

let repeats = 0;
/** @type {Record<string, () => void>} */
const demos = {
  motion: () =>
    toast({
      tone: "sodium",
      title: "Motion on Demo porch",
      message: "Started 14:32. Watching for it to end.",
    }),
  repeat: () => {
    repeats += 1;
    toast({
      key: "motion:demo-garage",
      tone: "sodium",
      title: "Motion on Demo garage",
      message: repeats > 1 ? `${repeats} events in the last minute.` : "Started just now.",
    });
  },
  online: () => toast({ tone: "signal", title: "Demo porch is back online" }),
  offline: () =>
    toast({
      key: "hub",
      tone: "alarm",
      title: "Can't reach the hub",
      message: "Live view and alerts are paused until it's back.",
      duration: 0,
      action: { label: "Retry now", run: () => toast({ title: "Retrying…", duration: 2000 }) },
    }),
  saved: () => toast({ tone: "iris", title: "Detection settings saved" }),
};

for (const button of document.querySelectorAll("[data-toast]")) {
  button.addEventListener("click", () => demos[button.getAttribute("data-toast") ?? ""]?.());
}
