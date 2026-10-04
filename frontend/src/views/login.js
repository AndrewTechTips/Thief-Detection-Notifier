// Sign-in page: full screen, the same lens as the boot screen. The lens breathes while signing
// in and turns rose on an error. After sign-in, main.js takes you back to where you were going.

import { ApiError, describeError, formatWait } from "../api/errors.js";
import { session } from "../state/auth.js";
import { connectionPill } from "../ui/connection.js";
import { h } from "../ui/dom.js";
import { passwordToggle } from "../ui/forms.js";
import { enter } from "../ui/motion.js";

const KEEP_KEY = "vision-hub.keep-signed-in";

/** What the page says when a session has just ended. */
const NOTICE = {
  expired: "Your session expired. Sign in again to continue.",
  "signed-out": "You're signed out.",
};

/** @type {import("../router.js").View} */
export default {
  title: "Sign in",
  mount(outlet) {
    const pill = connectionPill({ subject: "Hub" });
    const reason = session.state.get().reason;

    const username = field("username", "Username", {
      autocomplete: "username",
      autocapitalize: "none",
      spellcheck: "false",
      enterkeyhint: "next",
    });
    const password = field("password", "Password", {
      type: "password",
      autocomplete: "current-password",
      enterkeyhint: "go",
    });
    passwordToggle(password.input);

    const keep = h("input", {
      class: "switch",
      attrs: {
        type: "checkbox",
        role: "switch",
        id: "login-keep",
        name: "keep",
        "aria-describedby": "login-keep-hint",
      },
    });
    keep.checked = readKeep();

    const error = h("div", {
      class: "login-error",
      attrs: { role: "alert", id: "login-error", hidden: true },
    });
    const submit = h("button", {
      class: "btn btn-primary btn-lg w-full",
      attrs: { type: "submit" },
      text: "Sign in",
    });

    const form = h(
      "form",
      {
        class: "mt-6 grid gap-4",
        attrs: {
          method: "post",
          action: "/login",
          novalidate: true,
          "aria-describedby": "login-error",
        },
      },
      username.element,
      password.element,
      h(
        "div",
        { class: "flex items-center justify-between gap-4 py-1" },
        h(
          "div",
          {},
          h("label", {
            class: "block cursor-pointer text-sm font-medium",
            attrs: { for: "login-keep" },
            text: "Keep me signed in",
          }),
          h("p", {
            class: "hint",
            attrs: { id: "login-keep-hint" },
            text: "Stay signed in after closing the browser.",
          }),
        ),
        keep,
      ),
      error,
      submit,
    );

    const panel = h(
      "section",
      {
        class: "panel login-panel lens-host w-full max-w-sm px-6 pt-9 pb-7 sm:px-8",
        attrs: { "aria-labelledby": "login-title" },
      },
      h(
        "div",
        { class: "lens mx-auto", attrs: { "aria-hidden": "true" } },
        h("span", { class: "lens-glow" }),
        h("span", { class: "lens-iris" }),
      ),
      h("p", { class: "mt-6 text-center text-sm text-haze", text: "Vision Hub" }),
      h("h1", {
        class: "mt-1 text-center text-2xl font-semibold tracking-tight outline-none",
        attrs: { id: "login-title", tabindex: "-1" },
        text: "Sign in",
      }),
      reason
        ? h("p", { class: "login-notice", attrs: { role: "status" }, text: NOTICE[reason] })
        : null,
      form,
    );

    outlet.append(
      h(
        "div",
        { class: "login-screen" },
        panel,
        h("div", { class: "mt-5 flex justify-center" }, pill.element),
      ),
    );

    /** @type {number | undefined} */
    let countdown;
    let busy = false;

    /** @param {string} message */
    function showError(message) {
      error.textContent = message;
      error.hidden = false;
      panel.dataset.state = "error";
      enter(
        panel,
        [
          { transform: "translateX(0)" },
          { transform: "translateX(-7px)" },
          { transform: "translateX(6px)" },
          { transform: "translateX(-3px)" },
          { transform: "translateX(0)" },
        ],
        { duration: 360, easing: "ease-out" },
      );
    }

    function clearError() {
      error.hidden = true;
      if (panel.dataset.state === "error") delete panel.dataset.state;
    }

    /** Too many attempts: the button stays disabled and counts down until the hub allows more.
     * @param {number} seconds */
    function lockFor(seconds) {
      const until = Date.now() + seconds * 1000;
      submit.disabled = true;
      const tick = () => {
        const left = Math.ceil((until - Date.now()) / 1000);
        if (left <= 0) {
          window.clearInterval(countdown);
          submit.disabled = false;
          submit.textContent = "Sign in";
          clearError();
          return;
        }
        submit.textContent = `Try again in ${formatWait(left)}`;
        error.textContent = "Too many sign-in attempts. Wait a moment, then try again.";
      };
      tick();
      countdown = window.setInterval(tick, 1000);
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (busy || submit.disabled) return;
      clearError();
      const name = username.input.value.trim();
      const secret = password.input.value;
      const missing = [
        !name && username.invalid("Enter your username."),
        !secret && password.invalid("Enter your password."),
      ].filter(Boolean);
      if (missing.length) {
        (name ? password : username).input.focus();
        return;
      }

      busy = true;
      submit.setAttribute("aria-busy", "true");
      panel.dataset.state = "checking";
      writeKeep(keep.checked);
      try {
        await session.signIn(name, secret, keep.checked);
        // main.js moves on to the page you were going to.
      } catch (failure) {
        delete panel.dataset.state;
        if (failure instanceof ApiError && failure.status === 401) {
          showError("Wrong username or password.");
          password.input.select();
          password.input.focus();
        } else if (failure instanceof ApiError && failure.status === 429) {
          showError("Too many sign-in attempts.");
          lockFor(failure.retryAfter ?? 60);
        } else {
          const { title, detail } = describeError(failure);
          showError(`${title}. ${detail}`);
        }
      } finally {
        busy = false;
        submit.removeAttribute("aria-busy");
      }
    });

    // Fine pointers only: on phones, focusing would throw the keyboard over the page at once.
    if (matchMedia("(pointer: fine)").matches) username.input.focus({ preventScroll: true });

    return () => {
      pill.destroy();
      window.clearInterval(countdown);
    };
  },
};

/**
 * A labelled input with an inline error message.
 * @param {string} name
 * @param {string} label
 * @param {Record<string, string>} attrs
 */
function field(name, label, attrs) {
  const id = `login-${name}`;
  const message = h("p", { class: "field-error", attrs: { id: `${id}-error`, hidden: true } });
  const input = h("input", {
    class: "input",
    attrs: { id, name, type: "text", required: true, ...attrs },
  });
  const control = attrs.type === "password" ? h("div", { class: "input-group" }, input) : input;
  const element = h(
    "div",
    { class: "field" },
    h("label", { class: "label", attrs: { for: id }, text: label }),
    control,
    message,
  );
  input.addEventListener("input", () => {
    input.removeAttribute("aria-invalid");
    input.removeAttribute("aria-describedby");
    message.hidden = true;
  });
  return {
    element,
    input,
    /** @param {string} text */
    invalid(text) {
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", message.id);
      message.textContent = text;
      message.hidden = false;
      return true;
    },
  };
}

function readKeep() {
  try {
    return localStorage.getItem(KEEP_KEY) === "true";
  } catch {
    return false;
  }
}

/** @param {boolean} value */
function writeKeep(value) {
  try {
    localStorage.setItem(KEEP_KEY, String(value));
  } catch {
    // preference not remembered
  }
}
