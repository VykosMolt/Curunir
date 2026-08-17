// Shared current-session state, set once on boot from /api/session. Views read
// actor_id / actor_kind / mission_id from here to build a signed action's
// canonical binding. This is server-asserted identity (the browser cannot set
// its own authority) — it is display/binding state, not a credential.
export const session = {
  actor_id: null, actor_kind: null, roles: [], organisation: "", mission_id: null,
};
export function setSession(s) { Object.assign(session, s); }
