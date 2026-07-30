import React from "react";
import { cn } from "@/lib/utils";

interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: "default" | "secondary" | "destructive" | "outline" | "success" | "warning" | "info" | "accent";
}

const Badge = React.forwardRef<HTMLDivElement, BadgeProps>(
  ({ className, variant = "default", ...props }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold backdrop-blur-md ring-1 ring-inset transition-colors focus:outline-none focus:ring-2 focus:ring-accent/40",
          {
            "bg-slate-100/70 dark:bg-white/[0.06] ring-slate-900/[0.08] dark:ring-white/[0.08] text-gray-700 dark:text-gray-300":
              variant === "default" || variant === "secondary",
            "bg-red-400/15 dark:bg-red-500/20 ring-red-500/40 dark:ring-red-500/30 text-red-700 dark:text-red-300":
              variant === "destructive",
            "ring-slate-900/[0.15] dark:ring-white/15 text-gray-700 dark:text-gray-300":
              variant === "outline",
            "bg-green-400/15 dark:bg-green-500/20 ring-green-500/40 dark:ring-green-500/30 text-green-700 dark:text-green-300":
              variant === "success",
            "bg-yellow-400/15 dark:bg-yellow-500/20 ring-yellow-500/40 dark:ring-yellow-500/30 text-yellow-700 dark:text-yellow-300":
              variant === "warning",
            "bg-blue-400/15 dark:bg-blue-500/20 ring-blue-500/40 dark:ring-blue-500/30 text-blue-700 dark:text-blue-300":
              variant === "info",
            "bg-accent/10 dark:bg-accent/15 ring-accent/30 dark:ring-accent/40 text-accent dark:text-accent":
              variant === "accent",
          },
          className
        )}
        {...props}
      />
    );
  }
);
Badge.displayName = "Badge";

export { Badge };