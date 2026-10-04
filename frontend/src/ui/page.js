// Building blocks shared by page views.

import { h } from "./dom.js";

/**
 * Page title row. The heading takes focus after navigation (see router.js).
 * @param {{ title: string, description?: string, actions?: Node[] }} options
 */
export function pageHeader({ title, description, actions = [] }) {
  return h(
    "header",
    { class: "page-header" },
    h(
      "div",
      { class: "min-w-0" },
      h("h1", { attrs: { tabindex: "-1" }, text: title }),
      description ? h("p", { text: description }) : null,
    ),
    actions.length ? h("div", { class: "flex gap-2" }, actions) : null,
  );
}

/**
 * Stand-in for a section that a later roadmap task builds.
 * @param {string} text
 */
export function notBuiltYet(text) {
  return h("div", { class: "card px-6 py-10 text-center text-haze" }, h("p", { text }));
}
