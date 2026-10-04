import { h } from "../ui/dom.js";
import { icon } from "../ui/icons.js";
import { notBuiltYet, pageHeader } from "../ui/page.js";

/** @type {import("../router.js").View} */
export default {
  title: (params) => params.id,
  mount(outlet, params) {
    outlet.append(
      h(
        "a",
        { class: "btn btn-ghost btn-sm -ml-2 mb-3", attrs: { href: "/" } },
        icon("back"),
        "All cameras",
      ),
      pageHeader({ title: params.id, description: "Camera details and detection settings." }),
      notBuiltYet("Camera controls arrive in roadmap 4.3."),
    );
  },
};
