// Hub reachability, from the public readiness probe (no login needed).

/** @typedef {"checking" | "online" | "degraded" | "offline"} HubState */
/** @typedef {{ state: HubState, title: string, detail: string, command?: string }} HubReport */
/** @typedef {{ name: string, healthy: boolean, duration_ms: number }} CheckStatus */

const READY_URL = "/api/v1/health/ready";
const TIMEOUT_MS = 4000;

/** @type {HubReport} */
const OFFLINE = {
  state: "offline",
  title: "Can't reach the hub",
  detail: "Start it with",
  command: "vision-hub serve",
};

/** @returns {Promise<HubReport>} */
export async function checkHub() {
  let response;
  try {
    response = await fetch(READY_URL, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    return OFFLINE;
  }
  // 503 is the hub answering "not ready"; anything else (502, 504, HTML) is a proxy or network
  // problem in front of it.
  if (response.status !== 200 && response.status !== 503) return OFFLINE;

  /** @type {CheckStatus[]} */
  let checks;
  try {
    checks = (await response.json()).checks ?? [];
  } catch {
    return OFFLINE;
  }

  if (response.ok) {
    const database = checks.find((check) => check.name === "database");
    return {
      state: "online",
      title: "Hub online",
      detail: database
        ? `Database answered in ${Math.max(1, Math.round(database.duration_ms))} ms.`
        : "Ready for cameras and alerts.",
    };
  }
  const failing = checks.filter((check) => !check.healthy).map((check) => check.name);
  return { state: "degraded", title: "Hub not ready", detail: describeFailing(failing) };
}

/** Readiness check names as people would say them. */
const FAILING = /** @type {Record<string, string>} */ ({
  database: "The database isn't responding.",
  accepting_traffic: "It's restarting or shutting down.",
});

/** @param {string[]} names */
function describeFailing(names) {
  if (names.length === 0) return "It's still starting up.";
  if (names.length === 1 && names[0] in FAILING) return FAILING[names[0]];
  return `Failing checks: ${names.join(", ")}.`;
}
