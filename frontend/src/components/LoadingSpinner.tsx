interface LoadingSpinnerProps {
  message?: string;
  size?: "sm" | "md" | "lg";
}

export function LoadingSpinner({ message = "Loading...", size = "md" }: LoadingSpinnerProps) {
  const sizeMap = { sm: "w-5 h-5", md: "w-8 h-8", lg: "w-12 h-12" };
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <div className={`${sizeMap[size]} border-2 border-cyan-500/30 border-t-cyan-400 rounded-full animate-spin`} />
      <p className="text-sm text-slate-400">{message}</p>
    </div>
  );
}
