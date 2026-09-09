"use client";

import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import { api } from "@/lib/api";
import type { UserProfile } from "@/lib/types";

interface AuthContextValue {
  user: UserProfile | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
}

function readStoredCredentials(): { token: string | null; user: UserProfile | null } {
  if (typeof window === "undefined") return { token: null, user: null };
  const storedToken = localStorage.getItem("oracle_token");
  const storedUser = localStorage.getItem("oracle_user");
  let parsedUser: UserProfile | null = null;
  if (storedUser) {
    try {
      parsedUser = JSON.parse(storedUser);
    } catch {
      localStorage.removeItem("oracle_user");
    }
  }
  return { token: storedToken, user: parsedUser };
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const initial = readStoredCredentials();
  const [user, setUser] = useState<UserProfile | null>(initial.user);
  const [token, setToken] = useState<string | null>(initial.token);

  const fetchUser = useCallback(async (accessToken: string) => {
    try {
      const res = await api.get<UserProfile>("/auth/me", {
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      setUser(res.data);
      localStorage.setItem("oracle_user", JSON.stringify(res.data));
    } catch {
      setUser(null);
      localStorage.removeItem("oracle_user");
    }
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const formData = new URLSearchParams();
    formData.append("username", email);
    formData.append("password", password);
    const res = await api.post<{ access_token: string }>("/auth/login", formData, {
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
    });
    const accessToken = res.data.access_token;
    setToken(accessToken);
    localStorage.setItem("oracle_token", accessToken);
    await fetchUser(accessToken);
  }, [fetchUser]);

  const register = useCallback(async (email: string, password: string) => {
    const res = await api.post<{ access_token: string }>("/auth/register", { email, password });
    const accessToken = res.data.access_token;
    setToken(accessToken);
    localStorage.setItem("oracle_token", accessToken);
    await fetchUser(accessToken);
  }, [fetchUser]);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    localStorage.removeItem("oracle_token");
    localStorage.removeItem("oracle_user");
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        token,
        loading: false,
        login,
        register,
        logout,
        isAuthenticated: !!token,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
