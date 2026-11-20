import { createContext, useContext, useMemo, useState } from 'react';

const SessionContext = createContext(null);

/** Session (JWT, role, name) held in React state only: a reload signs the user out by design. */
export function SessionProvider({ children }) {
  const [session, setSession] = useState(null);
  const value = useMemo(() => ({ session, setSession, signOut: () => setSession(null) }), [session]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession() {
  return useContext(SessionContext);
}

export const HOME_FOR_ROLE = { agent: '/agent', finance: '/finance', manager: '/manager', customer: '/return' };
export const ROLE_LABEL = { agent: 'Warehouse agent', finance: 'Finance reviewer', manager: 'Ops manager', customer: 'Customer' };
