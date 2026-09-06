import { Inbox } from "lucide-react";

interface EmptyStateProps {
  message: string;
  action?: React.ReactNode;
}

export function EmptyState({ message, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 gap-4">
      <div className="p-4 rounded-full bg-slate-800 border border-slate-700">
        <Inbox className="w-8 h-8 text-slate-500" />
      </div>
      <p className="text-sm text-slate-400 text-center max-w-md">{message}</p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
