// App shell: sidebar on wide screens, top bar + bottom tab bar on phones. Pages render into the
// outlet; the router tells the shell which page is active.

import { appPath, href } from "../paths.js";
import { session } from "../state/auth.js";
import { accountMenu, accountRow } from "../ui/account.js";
import { connectionPill } from "../ui/connection.js";
import { h } from "../ui/dom.js";
import { icon, logo } from "../ui/icons.js";
import { installButton } from "../ui/install-button.js";
import { pushToggle } from "../ui/push-toggle.js";

/**
 * @typedef {import("../ui/icons.js").IconName} IconName
 * @typedef {{
 *   path: string,
 *   label: string,
 *   icon: IconName,
 *   matches: (path: string) => boolean,
 *   adminOnly?: boolean,
 * }} NavItem
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
    adminOnly: true, // the hub's audit log is admins only
  },
];

/** Jumps past the navigation. Moves focus itself: browsers don't reliably focus a fragment
 * target, and the point is that the next Tab continues from the page content. */
function skipLink() {
  return h("a", {
    class: "skip-link",
    attrs: { href: "#main" },
    text: "Skip to content",
    on: {
      click: (event) => {
        event.preventDefault();
        document.getElementById("main")?.focus();
      },
    },
  });
}

function brand() {
  return h(
    "a",
    { class: "brand", attrs: { href: href("/"), "aria-label": "Vision Hub, live view" } },
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
      { class: `${kind}-link`, attrs: { href: href(item.path) } },
      icon(item.icon),
      h("span", { text: item.label }),
    ),
  );
  const indicator = h("span", { class: `${kind}-indicator`, attrs: { "aria-hidden": "true" } });
  const nav = h("nav", { class: `${kind}-nav`, attrs: { "aria-label": label } }, indicator, links);
  return { nav, links };
}

export function createShell() {
  const sidePill = connectionPill();
  const topPill = connectionPill();
  const account = accountRow();
  const menu = accountMenu();
  const side = navigation("side", "Main");
  const tabs = navigation("tab", "Main");
  const outlet = h("div", { class: "shell-outlet" });
  let pathname = appPath(location.pathname) ?? "/";

  const element = h(
    "div",
    { class: "shell" },
    skipLink(),
    h(
      "aside",
      { class: "sidebar panel" },
      brand(),
      side.nav,
      h(
        "div",
        { class: "sidebar-footer" },
        installButton("btn btn-ghost btn-sm justify-start").element,
        pushToggle().element,
        account.element,
        h(
          "div",
          { class: "flex items-center justify-between" },
          h("span", { class: "text-sm text-haze", text: "Hub" }),
          sidePill.element,
        ),
      ),
    ),
    h(
      "header",
      { class: "topbar" },
      brand(),
      h("div", { class: "flex items-center gap-2" }, topPill.element, menu.element),
    ),
    h("main", { class: "shell-main", attrs: { id: "main", tabindex: "-1" } }, outlet),
    tabs.nav,
  );

  /** Pages this user can open: viewers don't see admin-only ones. */
  function visibleItems() {
    const admin = session.state.get().user?.role === "admin";
    return NAV.filter((item) => admin || !item.adminOnly);
  }

  /** Marks the active page in both navigations and slides their indicators to it.
   * @param {string} path */
  function setActive(path) {
    pathname = path;
    const visible = visibleItems();
    const index = visible.findIndex((item) => item.matches(path));
    for (const { nav, links } of [side, tabs]) {
      nav.style.setProperty("--count", String(visible.length));
      nav.style.setProperty("--active", String(Math.max(index, 0)));
      nav.toggleAttribute("data-none", index < 0);
      links.forEach((link, i) => {
        const item = NAV[i];
        link.hidden = !visible.includes(item);
        if (item === visible[index]) link.setAttribute("aria-current", "page");
        else link.removeAttribute("aria-current");
      });
      // The indicator starts on the first page without sliding there; later changes glide.
      if (!nav.hasAttribute("data-animate")) {
        void nav.offsetWidth;
        nav.setAttribute("data-animate", "");
      }
    }
  }

  // A role change (sign-in as someone else) re-filters the navigation.
  session.state.subscribe(() => setActive(pathname));

  return { element, outlet, setActive };
}
