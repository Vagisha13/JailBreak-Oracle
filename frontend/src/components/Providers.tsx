"use client";

import { AuthProvider } from "@/lib/auth";
import AppShell from "@/components/AppShell";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <AppShell>{children}</AppShell>
    </AuthProvider>
  );
}
