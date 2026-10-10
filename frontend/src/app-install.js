// The installed-app side of the dashboard: the service worker (production builds only) with an
// "Update ready" prompt, and the browser's install offer.

import { Store } from "./state/store.js";
import { toast } from "./ui/toast.js";

/**
 * Chrome's install offer (not in TypeScript's DOM types).
 * @typedef {Event & { prompt: () => Promise<void>, userChoice: Promise<{ outcome: string }> }} InstallPrompt
 */

/** The pending install offer, if the browser made one and the app isn't installed. */
export const installOffer = new Store(/** @type {InstallPrompt | null} */ (null));

window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault(); // our own "Install app" button instead of the browser's banner
  installOffer.set(/** @type {InstallPrompt} */ (event));
});
window.addEventListener("appinstalled", () => installOffer.set(null));

export async function install() {
  const offer = installOffer.get();
  if (!offer) return;
  await offer.prompt();
  await offer.userChoice;
  installOffer.set(null); // an offer can be used once, accepted or not
}

/** Registers the service worker and offers a reload when a new version has been downloaded. */
export function registerServiceWorker() {
  // The dev server never gets one: a stale cached app is the last thing to debug.
  // Nor does the public demo: its hub lives in the page, there is nothing to work offline for.
  if (!import.meta.env.PROD || import.meta.env.MODE === "demo") return;
  if (!("serviceWorker" in navigator)) return;
  const container = navigator.serviceWorker;
  // A first install takes control of the page too; only an update should reload it.
  const updating = Boolean(container.controller);

  window.addEventListener("load", async () => {
    const registration = await container.register("/sw.js").catch(() => null);
    if (!registration) return;

    /** @param {ServiceWorker} worker */
    const offer = (worker) =>
      toast({
        key: "update",
        tone: "iris",
        title: "Update ready",
        message: "A new version of the dashboard has been downloaded.",
        duration: 0,
        action: { label: "Reload", run: () => worker.postMessage({ type: "SKIP_WAITING" }) },
      });

    if (registration.waiting && container.controller) offer(registration.waiting);
    registration.addEventListener("updatefound", () => {
      const worker = registration.installing;
      worker?.addEventListener("statechange", () => {
        if (worker.state === "installed" && container.controller) offer(worker);
      });
    });
    // Look for a new version whenever the dashboard comes back into view.
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) registration.update().catch(() => {});
    });
  });

  let reloading = false;
  container.addEventListener("controllerchange", () => {
    if (reloading || !updating) return;
    reloading = true;
    location.reload(); // the new version took over (after "Reload"): load its files
  });
}
