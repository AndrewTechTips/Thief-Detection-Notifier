import "./styles/main.css";

import { mountBoot } from "./views/boot.js";

mountBoot(/** @type {HTMLElement} */ (document.querySelector("[data-boot]")));
