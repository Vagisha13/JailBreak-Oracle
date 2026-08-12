import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";

const inter = Inter({ subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Oracle AI Security | Red Teaming",
  description: "Automated AI Vulnerability Assessment Pipeline",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className={inter.className}>
        <div className="min-h-screen bg-slate-50 flex flex-col">
          {/* Top Navigation Bar */}
          <header className="bg-slate-900 text-white shadow-md">
            <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <div className="w-8 h-8 bg-blue-500 rounded-md flex items-center justify-center font-bold text-lg">
                  O
                </div>
                <h1 className="text-xl font-semibold tracking-tight">Oracle AI Security</h1>
              </div>
              <nav className="flex gap-6 text-sm font-medium text-slate-300">
                <a href="/" className="hover:text-white transition-colors">Dashboard</a>
                <a href="/campaigns" className="hover:text-white transition-colors">Campaigns</a>
                <a href="/reports" className="hover:text-white transition-colors">Reports</a>
              </nav>
            </div>
          </header>

          {/* Main Content Area */}
          <main className="flex-1 max-w-7xl w-full mx-auto p-6">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}