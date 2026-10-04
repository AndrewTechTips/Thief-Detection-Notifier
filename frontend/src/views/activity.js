import { notBuiltYet, pageHeader } from "../ui/page.js";

/** @type {import("../router.js").View} */
export default {
  title: "Activity",
  mount(outlet) {
    outlet.append(
      pageHeader({
        title: "Activity",
        description: "Who changed which camera or account, and when.",
      }),
      notBuiltYet("The activity log arrives in roadmap 4.3."),
    );
  },
};
