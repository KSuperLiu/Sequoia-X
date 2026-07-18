import { createContext, useContext } from "react";

export type User = { username: string; role: "ADMIN" | "MEMBER"; csrf_token: string };

export const AuthContext = createContext<User | null>(null);

export function useAuth() {
  return useContext(AuthContext);
}
