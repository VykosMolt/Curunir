// Server-asserted display/binding state, never an authority credential.
export const session = {
  actor_id: null,
  actor_kind: null,
  roles: [],
  organisation: "",
  mission_id: null,
};

export function setSession(value) { Object.assign(session, value); }
