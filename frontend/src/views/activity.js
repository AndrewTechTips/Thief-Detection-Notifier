// Activity: who changed which camera or account, and when (admins only). The hub records
// field names, never values, so entries say what changed, not what it changed to.

import { api } from "../api/client.js";
import { ApiError, describeError } from "../api/errors.js";
import { session } from "../state/auth.js";
import { deviceName, devices } from "../state/devices.js";
import { h } from "../ui/dom.js";
import { icon } from "../ui/icons.js";
import { pageHeader } from "../ui/page.js";
import { formatClock, formatDay } from "../ui/time.js";

/** @typedef {import("../api/types.js").AuditEntry} AuditEntry */
/** @typedef {import("../api/types.js").AuditPage} AuditPage */
/** @typedef {"all" | "device" | "user"} Scope */

const PAGE_SIZE = 50;

/** @type {{ value: Scope, label: string }[]} */
const SCOPES = [
  { value: "all", label: "Everything" },
  { value: "device", label: "Cameras" },
  { value: "user", label: "Accounts" },
];

/** Device fields as people would name them. */
const FIELD = /** @type {Record<string, string>} */ ({
  name: "name",
  enabled: "starting with the hub",
  target_fps: "analysis rate",
  retention_days: "how long events are kept",
  source: "source",
  detection: "detection settings",
});

const ROLE = /** @type {Record<string, string>} */ ({ admin: "Admin", viewer: "Viewer" });

const listFormat = new Intl.ListFormat("en", { type: "conjunction" });

/**
 * Who did it, in words: "You", "The hub", "andrew (command line)" or a username.
 * @param {string} actor
 */
export function describeActor(actor) {
  if (actor === session.state.get().user?.username) return "You";
  if (actor === "system") return "The hub";
  if (actor.startsWith("cli:")) return `${actor.slice(4) || "Someone"} (command line)`;
  return actor;
}

/**
 * The entry as a sentence plus an optional detail line.
 * @param {AuditEntry} entry
 * @returns {{ who: string, did: string, target: string, link: string | null, detail: string | null, icon: import("../ui/icons.js").IconName }}
 */
export function describeEntry(entry) {
  const who = describeActor(entry.actor);
  const details = /** @type {Record<string, unknown>} */ (entry.details);
  if (entry.target_type === "device") {
    const camera = deviceName(entry.target_id);
    const link = devices.get().has(entry.target_id)
      ? `/devices/${encodeURIComponent(entry.target_id)}`
      : null;
    const base = { who, target: camera, link, icon: /** @type {const} */ ("camera") };
    switch (entry.action) {
      case "device.created":
        return { ...base, did: "added", detail: null };
      case "device.deleted":
        return { ...base, did: "removed", link: null, detail: null };
      case "device.started":
        return { ...base, did: "started", detail: null };
      case "device.stopped":
        return { ...base, did: "stopped", detail: null };
      case "device.updated": {
        const fields = Array.isArray(details.fields) ? details.fields.map(String) : [];
        const named = fields.map((field) => FIELD[field] ?? field.replaceAll("_", " "));
        return {
          ...base,
          did: "changed",
          detail: named.length ? `Changed ${listFormat.format(named)}.` : null,
        };
      }
    }
  }
  if (entry.target_type === "user") {
    const role = typeof details.role === "string" ? (ROLE[details.role] ?? details.role) : null;
    const fromSettings =
      typeof details.source === "string" && details.source.includes("ADMIN_PASSWORD_HASH");
    const detail = [
      role ? `Role: ${role}.` : null,
      fromSettings ? "From the hub's startup settings." : null,
    ]
      .filter(Boolean)
      .join(" ");
    const base = {
      who,
      target: entry.target_id,
      link: null,
      icon: /** @type {const} */ ("activity"),
      detail: detail || null,
    };
    if (entry.action === "user.created") return { ...base, did: "created the account" };
    if (entry.action === "user.updated") return { ...base, did: "reset the account" };
  }
  // An action this dashboard doesn't know yet: still readable.
  return {
    who,
    did: entry.action.replaceAll(/[._]/g, " "),
    target: entry.target_id,
    link: null,
    detail: null,
    icon: "activity",
  };
}

/** @type {import("../router.js").View} */
export default {
  title: "Activity",
  mount(outlet) {
    const query = new URLSearchParams(location.search);
    let scope = /** @type {Scope} */ (
      SCOPES.some((s) => s.value === query.get("scope")) ? query.get("scope") : "all"
    );

    const scopes = h(
      "div",
      { class: "segmented", attrs: { role: "radiogroup", "aria-label": "Show" } },
      SCOPES.map(({ value, label }) =>
        h(
          "label",
          {},
          h("input", { attrs: { type: "radio", name: "scope", value, checked: value === scope } }),
          label,
        ),
      ),
    );
    const list = h("div", { class: "event-days", attrs: { "aria-busy": "true" } });
    const more = h("div", { class: "event-more", attrs: { "aria-live": "polite" } });
    outlet.append(
      pageHeader({
        title: "Activity",
        description: "Who changed which camera or account, and when.",
      }),
      h("div", { class: "event-filters" }, scopes),
      list,
      more,
    );

    /** @type {AuditEntry[]} */
    let entries = [];
    /** @type {string | null} */
    let cursor = null;
    let done = false;
    let loading = false;
    let generation = 0;
    let controller = new AbortController();

    const sentinel = new IntersectionObserver(
      (records) => {
        if (records.some((record) => record.isIntersecting)) loadMore();
      },
      { rootMargin: "600px 0px" },
    );
    sentinel.observe(more);

    function reset() {
      generation += 1;
      controller.abort();
      controller = new AbortController();
      entries = [];
      cursor = null;
      done = false;
      loading = false;
      list.setAttribute("aria-busy", "true");
      list.replaceChildren(skeleton());
      more.replaceChildren();
      loadMore();
    }

    async function loadMore() {
      if (loading || done) return;
      loading = true;
      const mine = generation;
      if (entries.length)
        more.replaceChildren(h("span", { class: "spinner" }), "Loading older activity…");
      try {
        /** @type {AuditPage} */
        const page = await api.get("/audit", {
          query: { limit: PAGE_SIZE, cursor, target_type: scope === "all" ? null : scope },
          signal: controller.signal,
        });
        if (mine !== generation) return;
        entries = [...entries, ...page.items];
        cursor = page.next_cursor;
        done = !cursor;
        render();
      } catch (error) {
        if (mine !== generation || controller.signal.aborted) return;
        showError(error);
        return;
      } finally {
        if (mine === generation) loading = false;
      }
      more.replaceChildren(done && entries.length ? "That's everything." : "");
      if (!done && more.getBoundingClientRect().top < innerHeight + 600) loadMore();
    }

    function render() {
      list.removeAttribute("aria-busy");
      if (!entries.length) {
        list.replaceChildren(
          notice(
            "activity",
            "Nothing here yet",
            scope === "all"
              ? "Changes to cameras and accounts appear here."
              : `No ${scope === "device" ? "camera" : "account"} changes yet.`,
          ),
        );
        return;
      }
      /** @type {Map<string, AuditEntry[]>} */
      const days = new Map();
      for (const entry of entries) {
        const day = formatDay(entry.at);
        days.set(day, [...(days.get(day) ?? []), entry]);
      }
      list.replaceChildren(
        ...[...days].map(([day, dayEntries]) =>
          h(
            "section",
            { class: "event-day", attrs: { "aria-label": day } },
            h("h2", { class: "event-day-title", text: day }),
            h("ol", { class: "event-list card" }, dayEntries.map(row)),
          ),
        ),
      );
    }

    /** @param {AuditEntry} entry */
    function row(entry) {
      const words = describeEntry(entry);
      const target = words.link
        ? h("a", { class: "activity-target", attrs: { href: words.link }, text: words.target })
        : h("span", { class: "activity-target", text: words.target });
      return h(
        "li",
        { class: "activity-row" },
        h("span", { class: "activity-icon", attrs: { "aria-hidden": "true" } }, icon(words.icon)),
        h(
          "div",
          { class: "min-w-0 flex-1" },
          h(
            "p",
            {},
            h("span", { class: "font-medium", text: words.who }),
            ` ${words.did} `,
            target,
          ),
          words.detail ? h("p", { class: "text-sm text-haze", text: words.detail }) : null,
        ),
        h("time", {
          class: "flex-none text-sm text-haze",
          attrs: {
            datetime: entry.at,
            // Matches the hub's logs for this change.
            title: entry.request_id ? `Request ${entry.request_id}` : undefined,
          },
          text: formatClock(entry.at),
        }),
      );
    }

    /** @param {unknown} error */
    function showError(error) {
      list.removeAttribute("aria-busy");
      if (error instanceof ApiError && error.status === 403) {
        list.replaceChildren(
          notice("activity", "Admins only", "Ask an admin if you need to see who changed what."),
        );
        return;
      }
      const { title, detail } = describeError(error);
      if (entries.length) {
        more.replaceChildren(
          `${title}. `,
          h("button", {
            class: "btn btn-ghost btn-sm",
            attrs: { type: "button" },
            text: "Try again",
            on: { click: () => loadMore() },
          }),
        );
        return;
      }
      list.replaceChildren(notice("activity", title, detail, { label: "Try again", run: reset }));
    }

    scopes.addEventListener("change", (event) => {
      scope = /** @type {Scope} */ (/** @type {HTMLInputElement} */ (event.target).value);
      const search = scope === "all" ? "" : `?scope=${scope}`;
      history.replaceState(history.state, "", `${location.pathname}${search}`);
      reset();
    });

    // Camera names arrive with the directory.
    const stopDevices = devices.subscribe(() => {
      if (entries.length) render();
    });

    reset();

    return () => {
      controller.abort();
      sentinel.disconnect();
      stopDevices();
    };
  },
};

function skeleton() {
  return h(
    "div",
    { class: "event-list card", attrs: { "aria-hidden": "true" } },
    ...Array.from({ length: 5 }, () =>
      h(
        "div",
        { class: "activity-row" },
        h("div", { class: "skeleton size-9 rounded-full" }),
        h(
          "div",
          { class: "flex-1 space-y-2" },
          h("div", { class: "skeleton h-4 w-2/3" }),
          h("div", { class: "skeleton h-3 w-1/3" }),
        ),
      ),
    ),
  );
}

/**
 * @param {import("../ui/icons.js").IconName} name
 * @param {string} title
 * @param {string} detail
 * @param {{ label: string, run: () => void }} [action]
 */
function notice(name, title, detail, action) {
  return h(
    "div",
    { class: "camera-empty card" },
    h("span", { class: "camera-empty-icon" }, icon(name)),
    h("p", { class: "font-medium", text: title }),
    h("p", { class: "text-sm text-haze", text: detail }),
    action
      ? h("button", {
          class: "btn btn-secondary btn-sm mt-3",
          attrs: { type: "button" },
          text: action.label,
          on: { click: action.run },
        })
      : null,
  );
}
