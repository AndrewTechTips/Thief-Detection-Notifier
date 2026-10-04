// "Install app": shown only while the browser offers installation (Chrome, Edge, Android).
// Safari installs from its Share menu and never makes the offer, so the button stays hidden.

import { install, installOffer } from "../app-install.js";
import { h } from "./dom.js";
import { icon } from "./icons.js";

/** @param {string} className */
export function installButton(className) {
  const element = h(
    "button",
    {
      class: className,
      attrs: { type: "button" },
      on: { click: () => install() },
    },
    icon("download"),
    "Install app",
  );
  const unsubscribe = installOffer.subscribe((offer) => {
    element.hidden = !offer;
  });
  return { element, destroy: unsubscribe };
}
