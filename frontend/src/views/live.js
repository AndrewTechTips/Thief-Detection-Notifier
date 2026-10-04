import { notBuiltYet, pageHeader } from "../ui/page.js";

/** @type {import("../router.js").View} */
export default {
  title: "Live",
  mount(outlet) {
    outlet.append(
      pageHeader({ title: "Live", description: "Live video and status for every camera." }),
      notBuiltYet("Camera tiles arrive with the device grid (roadmap 4.3)."),
    );
  },
};
