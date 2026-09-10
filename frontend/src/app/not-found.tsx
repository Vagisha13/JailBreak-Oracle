import Link from "next/link";
import { ShieldQuestion } from "lucide-react";

export default function NotFound() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-950 text-gray-200 p-6">
      <ShieldQuestion className="w-10 h-10 text-violet-400 mb-4" />
      <h2 className="text-2xl font-semibold mb-2">404 — Page not found</h2>
      <p className="text-sm text-gray-400 mb-6 max-w-md text-center">
        The page you are looking for does not exist or has been moved.
      </p>
      <Link
        href="/"
        className="px-4 py-2 rounded-md bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors"
      >
        Return home
      </Link>
    </div>
  );
}