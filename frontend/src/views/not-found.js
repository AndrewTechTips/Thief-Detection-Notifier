import { h } from "../ui/dom.js";

/** @type {import("../router.js").View} */
export default {
  title: "Page not found",
  mount(outlet) {
    outlet.append(
      h(
        "div",
        { class: "mx-auto max-w-md py-20 text-center" },
        h("h1", {
          class: "text-2xl font-semibold tracking-tight outline-none",
          attrs: { tabindex: "-1" },
          text: "Page not found",
        }),
        h(
          "p",
          { class: "mt-2 text-haze text-pretty" },
          "Nothing lives at ",
          h("span", { class: "code-chip", text: location.pathname }),
          ". Check the link, or go back to your cameras.",
        ),
        h("a", { class: "btn btn-primary mt-6", attrs: { href: "/" }, text: "Go to live view" }),
      ),
    );
  },
};
