"use client";

import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export const inputClass =
  "w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm text-[var(--paper)] outline-none transition placeholder:text-[var(--muted)]/60 focus:border-[var(--gold)] disabled:cursor-not-allowed disabled:opacity-60";

export function Field({
  label,
  hint,
  htmlFor,
  children,
  className,
  action,
}: {
  label: ReactNode;
  hint?: ReactNode;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
  action?: ReactNode;
}) {
  return (
    <div className={cn("block", className)}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <label htmlFor={htmlFor} className="block text-xs font-medium text-[var(--muted)]">
          {label}
        </label>
        {action}
      </div>
      {children}
      {hint ? <p className="mt-1.5 text-[11px] leading-4 text-[var(--muted)]">{hint}</p> : null}
    </div>
  );
}

export function TextInput({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(inputClass, className)} {...props} />;
}

export function TextArea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(inputClass, "resize-y leading-6", className)} {...props} />;
}

export function Select({
  className,
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cn(inputClass, className)} {...props}>
      {children}
    </select>
  );
}

export function PathField({
  label,
  value,
  placeholder,
  onBrowse,
  busy = false,
  hint,
  action,
}: {
  label: ReactNode;
  value: string;
  placeholder?: string;
  onBrowse: () => void;
  busy?: boolean;
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <Field label={label} hint={hint} action={action}>
      <div className="flex gap-2">
        <input className={inputClass} value={value} placeholder={placeholder} readOnly />
        <button
          type="button"
          onClick={onBrowse}
          disabled={busy}
          className="shrink-0 rounded-xl border border-[var(--line)] px-3 text-sm text-[var(--paper)] transition hover:border-[var(--gold)] disabled:opacity-60"
        >
          {busy ? "…" : "Chọn"}
        </button>
      </div>
    </Field>
  );
}
