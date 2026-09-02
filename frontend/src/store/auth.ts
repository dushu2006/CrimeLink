import { create } from "zustand";
import { login as apiLogin, logout as apiLogout, tokenStore, type Role, type Session } from "../api/client";

interface AuthState {
  session: Session | null;
  error: string | null;
  busy: boolean;
  signIn: (badge: string, password: string) => Promise<boolean>;
  signOut: () => Promise<void>;
  hydrate: () => void;
}

export const useAuth = create<AuthState>((set) => ({
  session: tokenStore.user,
  error: null,
  busy: false,
  async signIn(badge, password) {
    set({ busy: true, error: null });
    try {
      const session = await apiLogin(badge, password);
      set({ session, busy: false });
      return true;
    } catch (error) {
      set({ busy: false, error: error instanceof Error ? error.message : "Sign-in failed." });
      return false;
    }
  },
  async signOut() {
    await apiLogout();
    set({ session: null });
  },
  hydrate() {
    set({ session: tokenStore.user });
  },
}));

export function role(): Role | null {
  return tokenStore.user?.role ?? null;
}

export function can(required: Role[]): boolean {
  const current = role();
  return current ? required.includes(current) : false;
}
