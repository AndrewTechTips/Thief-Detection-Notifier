// Form behaviours shared by views.

import { h } from "./dom.js";
import { icon } from "./icons.js";

/**
 * Adds a show/hide button inside a password input's `.input-group`.
 * @param {HTMLInputElement} input
 */
export function passwordToggle(input) {
  const button = h("button", {
    class: "btn btn-ghost btn-icon btn-sm",
    attrs: { type: "button", "aria-label": "Show password", "aria-pressed": "false" },
  });
  const render = () => {
    const visible = input.type === "text";
    button.replaceChildren(icon(visible ? "eyeOff" : "eye"));
    button.setAttribute("aria-label", visible ? "Hide password" : "Show password");
    button.setAttribute("aria-pressed", String(visible));
  };
  button.addEventListener("click", () => {
    input.type = input.type === "password" ? "text" : "password";
    render();
    input.focus({ preventScroll: true });
  });
  render();
  input.after(button);
  return button;
}
