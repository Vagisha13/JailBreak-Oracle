"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";

export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gray-950 text-gray-200 p-6">
      <AlertTriangle className="w-10 h-10 text-amber-400 mb-4" />
      <h2 className="text-xl font-semibold mb-2">Something went wrong</h2>
      <p className="text-sm text-gray-400 mb-6 max-w-md text-center">
        The Jailbreak Oracle could not render this page.
      </p>
      <button
        onClick={retry}
        className="px-4 py-2 rounded-md bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-colors"
      >
        Try again
      </button>
    </div>
  );
}