// The public demo's entry point, loaded before the dashboard starts (main.js, demo builds only):
// the in-browser hub, a signed-in visitor on the first visit, and a note saying what this is.

import "./demo.css";

import { REFRESH_KEY } from "../state/session.js";
import { h } from "../ui/dom.js";
import { icon } from "../ui/icons.js";
import { startDemoHub } from "./hub.js";

const REPOSITORY = "https://github.com/AndrewTechTips/iot-vision-hub";
const VISITED = "vision-hub.demo-visited";
const NOTE_CLOSED = "vision-hub.demo-note-closed";

export async function startDemo() {
  await startDemoHub();
  signInFirstVisit();
  if (!read(NOTE_CLOSED)) document.body.append(note());
}

/** The first visit in a tab starts signed in; signing out (then in, with anything) still works. */
function signInFirstVisit() {
  try {
    if (sessionStorage.getItem(VISITED)) return;
    sessionStorage.setItem(VISITED, "1");
    if (!localStorage.getItem(REFRESH_KEY)) sessionStorage.setItem(REFRESH_KEY, "demo-visitor");
  } catch {
    // storage blocked: the sign-in page takes any name and password
  }
}

function note() {
  const close = h(
    "button",
    {
      class: "btn btn-ghost btn-icon btn-sm demo-note-close",
      attrs: { type: "button", "aria-label": "Hide this note" },
      on: {
        click: () => {
          element.remove();
          try {
            sessionStorage.setItem(NOTE_CLOSED, "1");
          } catch {
            // hidden for this page only
          }
        },
      },
    },
    icon("close"),
  );
  const element = h(
    "aside",
    { class: "demo-note panel-solid", attrs: { "aria-label": "About this demo" } },
    h("span", { class: "dot", attrs: { "aria-hidden": "true", "data-pulse": "" } }),
    h(
      "p",
      {},
      h("strong", { text: "Demo." }),
      " ",
      h("span", { text: "The hub runs in your browser, replaying real footage. " }),
      h("span", { class: "demo-note-more", text: "Any sign-in works. " }),
      h("a", {
        attrs: { href: REPOSITORY, target: "_blank", rel: "noopener" },
        text: "Run it yourself",
      }),
    ),
    close,
  );
  return element;
}

/** @param {string} key */
function read(key) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
