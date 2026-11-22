"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export const btnPrimary =
  "inline-flex items-center justify-center gap-2 rounded-xl bg-[var(--gold)] px-4 py-2.5 text-sm font-semibold text-[#1a1206] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50";
export const btnSecondary =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-[var(--line)] px-4 py-2.5 text-sm font-semibold text-[var(--paper)] transition hover:border-[var(--gold)] hover:text-[var(--gold)] disabled:cursor-not-allowed disabled:opacity-50";
export const btnGhost =
  "inline-flex items-center justify-center gap-2 rounded-xl px-3 py-2 text-sm text-[var(--muted)] transition hover:bg-white/5 hover:text-[var(--paper)] disabled:opacity-50";
export const btnDanger =
  "inline-flex items-center justify-center gap-2 rounded-xl border border-red-900/70 px-4 py-2.5 text-sm font-semibold text-red-200 transition hover:bg-red-950/50 disabled:opacity-50";

export function Panel({
  children,
  className,
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "aside" | "article";
}) {
  return (
    <Tag
      className={cn(
        "rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 sm:p-5",
        className,
      )}
    >
      {children}
    </Tag>
  );
}

export function SectionTitle({
  children,
  action,
  className,
}: {
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-3", className)}>
      <h2 className="text-xs font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">
        {children}
      </h2>
      {action}
    </div>
  );
}

export function Badge({
  children,
  tone = "muted",
  className,
}: {
  children: ReactNode;
  tone?: "muted" | "gold" | "success" | "danger" | "info";
  className?: string;
}) {
  const tones = {
    muted: "border-[var(--line)] text-[var(--muted)]",
    gold: "border-[var(--gold)]/60 text-[var(--gold)]",
    success: "border-emerald-800 text-emerald-200",
    danger: "border-red-900 text-red-200",
    info: "border-sky-900 text-sky-200",
  } as const;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] leading-4",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

export function Disclosure({
  title,
  summary,
  children,
  defaultOpen = false,
  className,
}: {
  title: ReactNode;
  summary?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  return (
    <details
      open={defaultOpen}
      className={cn("group rounded-2xl border border-[var(--line)] bg-black/15", className)}
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm">
        <span className="flex min-w-0 items-center gap-2">
          <span className="text-[var(--muted)] transition group-open:rotate-90">▸</span>
          <span className="truncate font-medium">{title}</span>
        </span>
        {summary ? (
          <span className="shrink-0 text-xs text-[var(--muted)]">{summary}</span>
        ) : null}
      </summary>
      <div className="border-t border-[var(--line)] px-4 py-4">{children}</div>
    </details>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
  className,
}: {
  value: T;
  options: { value: T; label: ReactNode; badge?: ReactNode }[];
  onChange: (value: T) => void;
  ariaLabel: string;
  className?: string;
}) {
  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const index = options.findIndex((option) => option.value === value);
    const delta = event.key === "ArrowRight" ? 1 : -1;
    const next = options[(index + delta + options.length) % options.length];
    if (next) onChange(next.value);
  }

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      className={cn("inline-flex rounded-xl border border-[var(--line)] bg-black/20 p-1", className)}
    >
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm transition",
              active
                ? "bg-[var(--gold)] font-semibold text-[#1a1206]"
                : "text-[var(--muted)] hover:text-[var(--paper)]",
            )}
          >
            {option.label}
            {option.badge != null ? (
              <span
                className={cn(
                  "rounded-full px-1.5 text-[10px] leading-4",
                  active ? "bg-black/15" : "border border-[var(--line)]",
                )}
              >
                {option.badge}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-[var(--line)] bg-black/10 px-6 py-10 text-center",
        className,
      )}
    >
      <p className="text-sm font-medium text-[var(--paper)]">{title}</p>
      {description ? (
        <p className="max-w-md text-xs leading-5 text-[var(--muted)]">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent",
        className,
      )}
    />
  );
}

export function ProgressBar({ percent }: { percent: number }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-black/40">
      <div
        className="h-full rounded-full bg-[var(--gold)] transition-[width]"
        style={{ width: `${Math.max(0, Math.min(100, percent))}%` }}
      />
    </div>
  );
}
