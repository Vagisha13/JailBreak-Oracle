"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  type ReactNode,
} from "react";

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

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  // IMPORTANT:
  // Do not read localStorage during render.
  const [user, setUser] = useState<UserProfile | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchUser = useCallback(async (accessToken: string) => {
    try {
      const res = await api.get<UserProfile>("/auth/me", {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      });

      setUser(res.data);
      localStorage.setItem("oracle_user", JSON.stringify(res.data));

      return true;
    } catch {
      setUser(null);
      setToken(null);

      localStorage.removeItem("oracle_token");
      localStorage.removeItem("oracle_user");

      return false;
    }
  }, []);

  // Initialize authentication ONLY on the client.
  useEffect(() => {
    const init = async () => {
      const storedToken = localStorage.getItem("oracle_token");

      if (!storedToken) {
        setLoading(false);
        return;
      }

      setToken(storedToken);

      try {
        await fetchUser(storedToken);
      } finally {
        setLoading(false);
      }
    };

    void init();
  }, [fetchUser]);

  const login = useCallback(
    async (email: string, password: string) => {
      const formData = new URLSearchParams();

      formData.append("username", email);
      formData.append("password", password);

      const res = await api.post<{ access_token: string }>(
        "/auth/login",
        formData,
        {
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
          },
        }
      );

      const accessToken = res.data.access_token;

      setToken(accessToken);
      localStorage.setItem("oracle_token", accessToken);

      const success = await fetchUser(accessToken);

      if (!success) {
        throw new Error("Failed to load authenticated user");
      }
    },
    [fetchUser]
  );

  const register = useCallback(
    async (email: string, password: string) => {
      const res = await api.post<{ access_token: string }>(
        "/auth/register",
        {
          email,
          password,
        }
      );

      const accessToken = res.data.access_token;

      setToken(accessToken);
      localStorage.setItem("oracle_token", accessToken);

      const success = await fetchUser(accessToken);

      if (!success) {
        throw new Error("Failed to load authenticated user");
      }
    },
    [fetchUser]
  );

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
        loading,
        login,
        register,
        logout,
        isAuthenticated: !!token && !!user,
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