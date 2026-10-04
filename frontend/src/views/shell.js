// App shell: sidebar on wide screens, top bar + bottom tab bar on phones. Pages render into the
// outlet; the router tells the shell which page is active.

import { connectionPill } from "../ui/connection.js";
import { h } from "../ui/dom.js";
import { icon, logo } from "../ui/icons.js";

/**
 * @typedef {import("../ui/icons.js").IconName} IconName
 * @typedef {{ path: string, label: string, icon: IconName, matches: (path: string) => boolean }} NavItem
 */

/** @type {NavItem[]} */
const NAV = [
  {
    path: "/",
    label: "Live",
    icon: "live",
    matches: (path) => path === "/" || path.startsWith("/devices/"),
  },
  {
    path: "/events",
    label: "Events",
    icon: "events",
    matches: (path) => path.startsWith("/events"),
  },
  {
    path: "/activity",
    label: "Activity",
    icon: "activity",
    matches: (path) => path.startsWith("/activity"),
  },
];

function brand() {
  return h(
    "a",
    { class: "brand", attrs: { href: "/", "aria-label": "Vision Hub, live view" } },
    h("span", { class: "brand-mark" }, logo()),
    h("span", { class: "brand-name", text: "Vision Hub" }),
  );
}

/**
 * @param {"side" | "tab"} kind
 * @param {string} label
 */
function navigation(kind, label) {
  const links = NAV.map((item) =>
    h(
      "a",
      { class: `${kind}-link`, attrs: { href: item.path } },
      icon(item.icon),
      h("span", { text: item.label }),
    ),
  );
  const indicator = h("span", { class: `${kind}-indicator`, attrs: { "aria-hidden": "true" } });
  const nav = h(
    "nav",
    { class: `${kind}-nav`, attrs: { "aria-label": label, style: `--count: ${NAV.length}` } },
    indicator,
    links,
  );
  return { nav, links };
}

export function createShell() {
  const sidePill = connectionPill();
  const topPill = connectionPill();
  const side = navigation("side", "Main");
  const tabs = navigation("tab", "Main");
  const outlet = h("div", { class: "shell-outlet" });

  const element = h(
    "div",
    { class: "shell" },
    h("a", { class: "skip-link", attrs: { href: "#main" }, text: "Skip to content" }),
    h(
      "aside",
      { class: "sidebar panel" },
      brand(),
      side.nav,
      h(
        "div",
        { class: "sidebar-footer" },
        h("span", { class: "text-sm text-haze", text: "Hub" }),
        sidePill.element,
      ),
    ),
    h("header", { class: "topbar" }, brand(), topPill.element),
    h("main", { class: "shell-main", attrs: { id: "main", tabindex: "-1" } }, outlet),
    tabs.nav,
  );

  /** Marks the active page in both navigations and slides their indicators to it.
   * @param {string} pathname */
  function setActive(pathname) {
    const index = NAV.findIndex((item) => item.matches(pathname));
    for (const { nav, links } of [side, tabs]) {
      nav.style.setProperty("--active", String(Math.max(index, 0)));
      nav.toggleAttribute("data-none", index < 0);
      links.forEach((link, i) => {
        if (i === index) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });
      // The indicator starts on the first page without sliding there; later changes glide.
      if (!nav.hasAttribute("data-animate")) {
        void nav.offsetWidth;
        nav.setAttribute("data-animate", "");
      }
    }
  }

  return { element, outlet, setActive };
}
