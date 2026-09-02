// What the server said about the signed-in actor. Display only, not a credential.
export const session = {
  actor_id: null,
  actor_kind: null,
  roles: [],
  organisation: "",
  mission_id: null,
};

export function setSession(value) { Object.assign(session, value); }
