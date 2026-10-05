// Readable names for the API types generated from the hub's OpenAPI schema (schema.d.ts,
// regenerated with `npm run api:sync`). Types only, for JSDoc:
//   /** @typedef {import("../api/types.js").Device} Device */

/** @typedef {import("./schema").components["schemas"]} Schemas */

// Health and auth
/** @typedef {Schemas["Readiness"]} Readiness */
/** @typedef {Schemas["CheckStatus"]} CheckStatus */
/** @typedef {Schemas["TokenResponse"]} TokenResponse */
/** @typedef {Schemas["PrincipalOut"]} Principal */
/** @typedef {Schemas["Role"]} Role */
/** @typedef {Schemas["TicketOut"]} Ticket */

// Errors (RFC 9457 problem details)
/** @typedef {Schemas["ProblemDetail"]} Problem */
/** @typedef {Schemas["FieldError"]} FieldError */

// Devices
/** @typedef {Schemas["DeviceOut"]} Device */
/** @typedef {Schemas["DeviceCreate"]} DeviceCreate */
/** @typedef {Schemas["DeviceUpdate"]} DeviceUpdate */
/** @typedef {Schemas["DeviceStatus"]} DeviceStatus */
/** @typedef {Schemas["DetectionConfig"]} DetectionConfig */
/** @typedef {Schemas["SourceTestRequest"]} SourceTestRequest */
/** @typedef {Schemas["SourceTestResult"]} SourceTestResult */
/** @typedef {Schemas["Page_DeviceOut_"]} DevicePage */

// Events and audit
/** @typedef {Schemas["EventOut"]} MotionEvent */
/** @typedef {Schemas["Page_EventOut_"]} EventPage */
/** @typedef {Schemas["AuditEntryOut"]} AuditEntry */
/** @typedef {Schemas["Page_AuditEntryOut_"]} AuditPage */

// Web push
/** @typedef {Schemas["PushConfigOut"]} PushConfig */
/** @typedef {Schemas["PushTestOut"]} PushTest */

// WebSocket protocol (/api/v1/ws/events), discriminated on `type`
/** @typedef {Schemas["WsServerMessage"]} ServerMessage */
/** @typedef {Schemas["WsClientMessage"]} ClientMessage */
/**
 * The server message with a given `type`, e.g. `MessageOf<"motion.ended">`.
 * @template {ServerMessage["type"]} T
 * @typedef {Extract<ServerMessage, { type: T }>} MessageOf
 */

export {};
