import { notBuiltYet, pageHeader } from "../ui/page.js";

/** @type {import("../router.js").View} */
export default {
  title: "Events",
  mount(outlet) {
    outlet.append(
      pageHeader({ title: "Events", description: "Motion your cameras recorded, newest first." }),
      notBuiltYet("The event history arrives in roadmap 4.3."),
    );
  },
};
