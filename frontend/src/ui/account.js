// Who is signed in, with a way to sign out: a row in the sidebar, a menu in the phone top bar.

import { disablePush } from "../push.js";
import { session } from "../state/auth.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { installButton } from "./install-button.js";
import { enter, exit } from "./motion.js";
import { pushToggle } from "./push-toggle.js";

/** @typedef {import("../api/types.js").Principal} Principal */

const ROLE_LABEL = { admin: "Admin", viewer: "Viewer" };

/** @param {Principal} user */
function avatar(user) {
  return h("span", {
    class: "avatar",
    attrs: { "aria-hidden": "true" },
    text: user.username.charAt(0).toUpperCase(),
  });
}

/** Signing out also stops this device's notifications: it may be shared.
 * @param {HTMLButtonElement} button */
async function signOut(button) {
  button.setAttribute("aria-busy", "true");
  await disablePush().catch(() => {});
  await session.signOut();
}

/** Sidebar row: avatar, name, role and a sign-out button. Hidden while signed out. */
export function accountRow() {
  const element = h("div", { class: "account-row" });
  const unsubscribe = session.state.subscribe(({ user }) => {
    element.hidden = !user;
    if (!user) return element.replaceChildren();
    const button = h(
      "button",
      {
        class: "btn btn-ghost btn-icon btn-sm",
        attrs: { type: "button", "aria-label": "Sign out", title: "Sign out" },
        on: { click: () => signOut(button) },
      },
      icon("signOut"),
    );
    element.replaceChildren(
      avatar(user),
      h(
        "span",
        { class: "min-w-0 flex-1" },
        h("span", { class: "block truncate text-sm font-medium", text: user.username }),
        h("span", { class: "block text-xs text-haze", text: ROLE_LABEL[user.role] }),
      ),
      button,
    );
  });
  return { element, destroy: unsubscribe };
}

let menuId = 0;

/** Phone top bar: an avatar button that opens a small account menu. */
export function accountMenu() {
  const id = `account-menu-${++menuId}`;
  const trigger = h("button", {
    class: "avatar-button",
    attrs: {
      type: "button",
      "aria-haspopup": "true",
      "aria-expanded": "false",
      "aria-controls": id,
    },
  });
  const name = h("p", { class: "truncate font-medium" });
  const role = h("p", { class: "text-sm text-haze" });
  const signOutButton = h(
    "button",
    {
      class: "btn btn-secondary btn-sm w-full",
      attrs: { type: "button" },
      on: { click: () => signOut(signOutButton) },
    },
    icon("signOut"),
    "Sign out",
  );
  const menu = h(
    "div",
    { class: "account-menu panel-solid", attrs: { id, hidden: true } },
    h("div", { class: "px-1 pb-3" }, name, role),
    h("div", { class: "account-menu-section" }, pushToggle().element),
    h(
      "div",
      { class: "grid gap-2" },
      installButton("btn btn-ghost btn-sm w-full").element,
      signOutButton,
    ),
  );
  const element = h("div", { class: "relative" }, trigger, menu);

  /** @param {boolean} open */
  function setOpen(open) {
    if (open === !menu.hidden) return;
    trigger.setAttribute("aria-expanded", String(open));
    if (open) {
      menu.hidden = false;
      enter(menu, [{ opacity: 0, transform: "translateY(-6px) scale(0.97)" }, { opacity: 1 }], {
        duration: 200,
      });
      signOutButton.focus();
    } else {
      exit(menu, [{ opacity: 1 }, { opacity: 0, transform: "scale(0.97)" }], {
        duration: 120,
      }).then(() => {
        if (trigger.getAttribute("aria-expanded") === "false") menu.hidden = true;
      });
    }
  }

  trigger.addEventListener("click", () => setOpen(menu.hidden));
  const onPointer = (/** @type {PointerEvent} */ event) => {
    if (!element.contains(/** @type {Node} */ (event.target))) setOpen(false);
  };
  const onKey = (/** @type {KeyboardEvent} */ event) => {
    if (event.key === "Escape" && !menu.hidden) {
      setOpen(false);
      trigger.focus();
    }
  };
  document.addEventListener("pointerdown", onPointer);
  document.addEventListener("keydown", onKey);

  const unsubscribe = session.state.subscribe(({ user }) => {
    element.hidden = !user;
    if (!user) {
      setOpen(false);
      return;
    }
    trigger.replaceChildren(avatar(user));
    trigger.setAttribute("aria-label", `Account: ${user.username}`);
    name.textContent = user.username;
    role.textContent = ROLE_LABEL[user.role];
  });

  return {
    element,
    destroy() {
      unsubscribe();
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    },
  };
}
