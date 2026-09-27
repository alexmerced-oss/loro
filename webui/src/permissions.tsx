import { createContext, useContext } from "react";

/** Permissions of the signed-in user. Launch-token mode is one user with every permission. */
export const ALL_PERMISSIONS = ["read", "operate", "approve", "admin"];
export const PermissionsContext = createContext<string[]>(ALL_PERMISSIONS);

export function useCan(permission: string): boolean {
  return useContext(PermissionsContext).includes(permission);
}
