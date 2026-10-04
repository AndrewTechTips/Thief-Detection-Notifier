// Client-side router on the History API. Views load lazily (one chunk each), mount into an
// outlet and return a cleanup function, so streams and timers stop when you leave a page.

/** @typedef {Record<string, string>} Params */
/**
 * @typedef {{
 *   title: string | ((params: Params) => string),
 *   mount: (outlet: HTMLElement, params: Params) => void | (() => void),
 * }} View
 * @typedef {{
 *   path: string,
 *   load: () => Promise<{ default: View }>,
 *   public?: boolean,
 *   layout?: "shell" | "bare",
 * }} Route
 * public: reachable while signed out. layout "bare": full screen, without the app shell.
 * @typedef {{ route: Route | null, params: Params }} Match
 */

import { h } from "./ui/dom.js";

const APP_NAME = "Vision Hub";

/**
 * Turns "/devices/:id" into a regular expression with named groups.
 * @param {string} path
 */
export function compile(path) {
  const pattern = path
    .split("/")
    .map((part) =>
      part.startsWith(":")
        ? `(?<${part.slice(1)}>[^/]+)`
        : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
    )
    .join("/");
  return new RegExp(`^${pattern}/?$`);
}

/**
 * @param {Route[]} routes
 * @param {string} pathname
 * @returns {Match}
 */
export function match(routes, pathname) {
  for (const route of routes) {
    const found = compile(route.path).exec(pathname);
    if (found) {
      const params = /** @type {Params} */ ({});
      for (const [key, value] of Object.entries(found.groups ?? {})) {
        try {
          params[key] = decodeURIComponent(value);
        } catch {
          return { route: null, params: {} }; // malformed escape: treat as not found
        }
      }
      return { route, params };
    }
  }
  return { route: null, params: {} };
}

/**
 * A post-sign-in destination taken from the address bar, made safe: only paths on this origin,
 * never another site ("//evil.example", "/\\evil.example" and the like resolve elsewhere).
 * @param {string | null} value
 * @param {string} origin
 * @param {string[]} [avoid] paths that make no sense as a destination (e.g. the sign-in page)
 */
export function safeRedirect(value, origin, avoid = []) {
  if (!value?.startsWith("/")) return "/";
  let url;
  try {
    url = new URL(value, origin);
  } catch {
    return "/";
  }
  if (url.origin !== origin || avoid.includes(url.pathname)) return "/";
  return url.pathname + url.search + url.hash;
}

/**
 * Whether a click on a link should be handled by the router rather than the browser.
 * @param {MouseEvent} event
 * @param {HTMLAnchorElement} link
 */
export function isInternalClick(event, link) {
  if (event.defaultPrevented || event.button !== 0) return false;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
  if (link.target && link.target !== "_self") return false;
  if (link.hasAttribute("download") || link.origin !== location.origin) return false;
  // API endpoints (snapshots, streams) and other pages are real documents.
  return !link.pathname.startsWith("/api/") && !link.pathname.endsWith(".html");
}

/**
 * @param {{
 *   routes: Route[],
 *   notFound: () => Promise<{ default: View }>,
 *   outlet: (route: Route | null) => HTMLElement,
 *   guard?: (route: Route | null, url: URL) => string | null,
 *   onChange?: (pathname: string) => void,
 *   enter?: (outlet: HTMLElement) => void,
 * }} options
 */
export function createRouter({ routes, notFound, outlet, guard, onChange, enter }) {
  /** @type {void | (() => void)} */
  let cleanup;
  let token = 0;
  let first = true;

  async function render() {
    const current = ++token;
    let { route, params } = match(routes, location.pathname);
    // A guard may send this page elsewhere (e.g. to sign-in); the address bar follows.
    const redirect = guard?.(route, new URL(location.href));
    if (redirect) {
      history.replaceState({ scrollY: 0 }, "", redirect);
      ({ route, params } = match(routes, location.pathname));
    }
    /** @type {View} */
    let view;
    try {
      view = (await (route ? route.load() : notFound())).default;
    } catch {
      view = LOAD_FAILED; // usually a new release replaced the chunk this tab expected
    }
    if (current !== token) return; // a newer navigation won the race

    cleanup?.();
    const target = outlet(route);
    target.replaceChildren();
    cleanup = view.mount(target, params);
    const title = typeof view.title === "function" ? view.title(params) : view.title;
    document.title = title ? `${title} – ${APP_NAME}` : APP_NAME;
    onChange?.(location.pathname);

    const scrollY = /** @type {{ scrollY?: number } | null} */ (history.state)?.scrollY ?? 0;
    window.scrollTo(0, scrollY);
    if (!first) {
      enter?.(target);
      // Move focus to the new page, so screen readers announce it and Tab starts from it.
      target.querySelector("h1")?.focus({ preventScroll: true });
    }
    first = false;
  }

  /**
   * @param {string} path
   * @param {{ replace?: boolean }} [options]
   */
  function navigate(path, { replace = false } = {}) {
    const url = new URL(path, location.origin);
    if (url.pathname === location.pathname && url.search === location.search) return;
    rememberScroll();
    history[replace ? "replaceState" : "pushState"]({ scrollY: 0 }, "", url);
    render();
  }

  function rememberScroll() {
    history.replaceState({ ...history.state, scrollY: window.scrollY }, "");
  }

  function start() {
    history.scrollRestoration = "manual";
    // Keep the current entry's scroll position fresh, so Back and Forward return to it.
    /** @type {number | undefined} */
    let saving;
    window.addEventListener(
      "scroll",
      () => {
        window.clearTimeout(saving);
        saving = window.setTimeout(rememberScroll, 200);
      },
      { passive: true },
    );
    document.addEventListener("click", (event) => {
      const link = /** @type {Element} */ (event.target).closest?.("a[href]");
      if (!(link instanceof HTMLAnchorElement) || !isInternalClick(event, link)) return;
      event.preventDefault();
      navigate(link.pathname + link.search + link.hash);
    });
    window.addEventListener("popstate", render);
    window.addEventListener("pagehide", rememberScroll);
    return render();
  }

  /** Re-checks the current page, e.g. after signing in or out. */
  function refresh() {
    return render();
  }

  return { start, navigate, refresh };
}

/** @type {View} */
const LOAD_FAILED = {
  title: "Page didn't load",
  mount(outlet) {
    outlet.append(
      h(
        "div",
        { class: "mx-auto max-w-md py-24 text-center" },
        h("h1", {
          class: "text-2xl font-semibold tracking-tight",
          attrs: { tabindex: "-1" },
          text: "This page didn't load",
        }),
        h("p", {
          class: "mt-2 text-haze",
          text: "The dashboard may have been updated. Reloading fetches the latest version.",
        }),
        h("button", {
          class: "btn btn-primary mt-6",
          attrs: { type: "button" },
          text: "Reload",
          on: { click: () => location.reload() },
        }),
      ),
    );
  },
};
