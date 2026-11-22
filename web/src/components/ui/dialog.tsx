"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cn } from "@/lib/cn";
import { btnDanger, btnPrimary, btnSecondary } from "@/components/ui/primitives";
import { IconClose } from "@/components/ui/icons";

export type DialogChoice = {
  label: string;
  value: string;
  tone?: "primary" | "default" | "danger";
};

export type DialogConfig = {
  title: string;
  description?: ReactNode;
  choices?: DialogChoice[];
  input?: {
    label?: string;
    defaultValue?: string;
    placeholder?: string;
  };
  cancelLabel?: string;
};

export type DialogResult = {
  value: string;
  input: string;
};

type DialogContextValue = {
  choose: (config: DialogConfig) => Promise<DialogResult | null>;
  confirm: (config: Omit<DialogConfig, "choices"> & { confirmLabel?: string; danger?: boolean }) => Promise<boolean>;
  prompt: (config: Omit<DialogConfig, "choices"> & { confirmLabel?: string }) => Promise<string | null>;
};

const DialogContext = createContext<DialogContextValue | null>(null);

type Pending = {
  config: DialogConfig;
  resolve: (result: DialogResult | null) => void;
};

export function DialogProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [inputValue, setInputValue] = useState("");
  const panelRef = useRef<HTMLDivElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const pendingRef = useRef<Pending | null>(null);

  const close = useCallback((result: DialogResult | null) => {
    const current = pendingRef.current;
    if (!current) return;
    pendingRef.current = null;
    current.resolve(result);
    setPending(null);
    window.setTimeout(() => previousFocus.current?.focus?.(), 0);
  }, []);

  const choose = useCallback<(config: DialogConfig) => Promise<DialogResult | null>>((config) => {
    // A second dialog must not leave the first promise pending forever.
    if (pendingRef.current) {
      pendingRef.current.resolve(null);
      pendingRef.current = null;
    }
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setInputValue(config.input?.defaultValue ?? "");
    return new Promise((resolve) => {
      pendingRef.current = { config, resolve };
      setPending(pendingRef.current);
    });
  }, []);

  const confirm = useCallback<DialogContextValue["confirm"]>(
    async ({ confirmLabel = "Đồng ý", danger = false, cancelLabel = "Huỷ", ...config }) => {
      const result = await choose({
        ...config,
        cancelLabel,
        choices: [
          { label: confirmLabel, value: "ok", tone: danger ? "danger" : "primary" },
          { label: cancelLabel, value: "cancel" },
        ],
      });
      return result?.value === "ok";
    },
    [choose],
  );

  const prompt = useCallback<DialogContextValue["prompt"]>(
    async ({ confirmLabel = "Lưu", cancelLabel = "Huỷ", ...config }) => {
      const result = await choose({
        ...config,
        cancelLabel,
        choices: [
          { label: confirmLabel, value: "ok", tone: "primary" },
          { label: cancelLabel, value: "cancel" },
        ],
      });
      return result?.value === "ok" ? result.input.trim() : null;
    },
    [choose],
  );

  useEffect(() => {
    if (!pending) return;
    const panel = panelRef.current;
    const focusable =
      panel?.querySelector<HTMLElement>("input, textarea") ||
      panel?.querySelector<HTMLElement>("[data-dialog-primary='true']") ||
      panel?.querySelector<HTMLElement>(
        "button:not([disabled]), [tabindex]:not([tabindex='-1'])",
      );
    focusable?.focus();
  }, [pending]);

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (!pending) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close(null);
      return;
    }
    if (event.key !== "Tab") return;
    const nodes = panelRef.current?.querySelectorAll<HTMLElement>(
      "input, textarea, button:not([disabled]), [tabindex]:not([tabindex='-1'])",
    );
    if (!nodes || nodes.length === 0) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    }
  }

  const value: DialogContextValue = { choose, confirm, prompt };

  return (
    <DialogContext.Provider value={value}>
      {children}
      {pending ? (
        <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm">
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="dialog-title"
            className="w-full max-w-md rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5 shadow-2xl"
            onKeyDown={onKeyDown}
          >
            <div className="flex items-start justify-between gap-3">
              <h2 id="dialog-title" className="text-lg font-semibold">
                {pending.config.title}
              </h2>
              <button
                type="button"
                aria-label="Đóng"
                onClick={() => close(null)}
                className="rounded-lg p-1 text-[var(--muted)] hover:bg-white/5 hover:text-white"
              >
                <IconClose />
              </button>
            </div>
            {pending.config.description ? (
              <div className="mt-2 text-sm leading-6 text-[var(--muted)]">
                {pending.config.description}
              </div>
            ) : null}
            {pending.config.input ? (
              <label className="mt-4 block">
                <span className="mb-1.5 block text-xs text-[var(--muted)]">
                  {pending.config.input.label || "Tên"}
                </span>
                <input
                  value={inputValue}
                  onChange={(event) => setInputValue(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      event.preventDefault();
                      close({ value: "ok", input: inputValue });
                    }
                  }}
                  placeholder={pending.config.input.placeholder}
                  maxLength={120}
                  className="w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm outline-none focus:border-[var(--gold)]"
                />
              </label>
            ) : null}
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              {(pending.config.choices || []).map((choice) => (
                <button
                  key={choice.value}
                  type="button"
                  data-dialog-primary={choice.tone === "primary" ? "true" : undefined}
                  className={cn(
                    choice.tone === "primary" && btnPrimary,
                    choice.tone === "danger" && btnDanger,
                    (!choice.tone || choice.tone === "default") && btnSecondary,
                  )}
                  onClick={() => close({ value: choice.value, input: inputValue })}
                >
                  {choice.label}
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </DialogContext.Provider>
  );
}

export function useDialog() {
  const value = useContext(DialogContext);
  if (!value) throw new Error("useDialog must be used inside DialogProvider");
  return value;
}

export function Drawer({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  width = "max-w-2xl",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const previous = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    previous.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    window.setTimeout(() => closeRef.current?.focus(), 0);
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      previous.current?.focus?.();
    };
  }, [open, onClose]);

  return (
    <>
      <div
        aria-hidden
        onClick={onClose}
        className={cn(
          "fixed inset-0 z-[70] bg-black/60 backdrop-blur-sm transition-opacity",
          open ? "opacity-100" : "pointer-events-none opacity-0",
        )}
      />
      <aside
        ref={panelRef}
        aria-hidden={!open}
        inert={!open}
        role="dialog"
        aria-modal={open}
        className={cn(
          "fixed top-0 right-0 z-[71] flex h-full w-full flex-col border-l border-[var(--line)] bg-[var(--ink)] shadow-2xl transition-transform duration-200",
          width,
          open ? "translate-x-0" : "pointer-events-none translate-x-full",
        )}
      >
        <header className="flex items-start justify-between gap-3 border-b border-[var(--line)] px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold">{title}</h2>
            {description ? (
              <p className="mt-1 text-xs leading-5 text-[var(--muted)]">{description}</p>
            ) : null}
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Đóng"
            className="rounded-lg p-1.5 text-[var(--muted)] hover:bg-white/5 hover:text-white"
          >
            <IconClose />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer ? (
          <footer className="border-t border-[var(--line)] bg-[var(--panel)]/60 px-5 py-3">
            {footer}
          </footer>
        ) : null}
      </aside>
    </>
  );
}
