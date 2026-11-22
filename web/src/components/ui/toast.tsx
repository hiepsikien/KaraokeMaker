"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cn } from "@/lib/cn";
import { IconClose } from "@/components/ui/icons";

export type NoticeTone = "success" | "info" | "error";

export type NoticeAction = {
  label: string;
  onClick: () => void | Promise<void>;
};

export type Notice = {
  id: string;
  message: ReactNode;
  tone: NoticeTone;
  action?: NoticeAction;
};

type NoticeContextValue = {
  push: (notice: { message: ReactNode; tone?: NoticeTone; action?: NoticeAction; duration?: number }) => string;
  dismiss: (id: string) => void;
};

const NoticeContext = createContext<NoticeContextValue | null>(null);

function newId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function NoticeProvider({ children }: { children: ReactNode }) {
  const [notices, setNotices] = useState<Notice[]>([]);
  const timers = useRef(new Map<string, number>());

  const dismiss = useCallback((id: string) => {
    const timer = timers.current.get(id);
    if (timer) window.clearTimeout(timer);
    timers.current.delete(id);
    setNotices((current) => current.filter((notice) => notice.id !== id));
  }, []);

  const push = useCallback<NoticeContextValue["push"]>(
    ({ message, tone = "info", action, duration }) => {
      const id = newId();
      setNotices((current) => [...current.slice(-3), { id, message, tone, action }]);
      const timeout = duration ?? (tone === "error" ? 9000 : 4500);
      if (timeout > 0) {
        timers.current.set(
          id,
          window.setTimeout(() => dismiss(id), timeout),
        );
      }
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const current = timers.current;
    return () => {
      current.forEach((timer) => window.clearTimeout(timer));
      current.clear();
    };
  }, []);

  const value = useMemo(() => ({ push, dismiss }), [push, dismiss]);

  return (
    <NoticeContext.Provider value={value}>
      {children}
      <div
        className="pointer-events-none fixed right-3 bottom-3 z-[80] flex w-[min(24rem,calc(100vw-1.5rem))] flex-col gap-2 sm:right-5 sm:bottom-5"
        aria-live="polite"
        aria-atomic="false"
      >
        {notices.map((notice) => (
          <div
            key={notice.id}
            role={notice.tone === "error" ? "alert" : "status"}
            className={cn(
              "pointer-events-auto flex items-start gap-2 rounded-2xl border px-3.5 py-3 text-sm shadow-2xl backdrop-blur",
              notice.tone === "success" && "border-emerald-800/80 bg-emerald-950/90 text-emerald-50",
              notice.tone === "error" && "border-red-900/80 bg-red-950/95 text-red-50",
              notice.tone === "info" && "border-[var(--line)] bg-[var(--panel)]/95 text-[var(--paper)]",
            )}
          >
            <div className="min-w-0 flex-1 leading-5">{notice.message}</div>
            {notice.action ? (
              <button
                type="button"
                className={cn(
                  "shrink-0 rounded-lg px-2 py-1 text-xs font-semibold",
                  notice.tone === "error"
                    ? "text-red-100 hover:bg-red-900/60"
                    : "text-[var(--gold)] hover:bg-black/20",
                )}
                onClick={async () => {
                  dismiss(notice.id);
                  await notice.action?.onClick();
                }}
              >
                {notice.action.label}
              </button>
            ) : null}
            <button
              type="button"
              aria-label="Đóng thông báo"
              className="shrink-0 rounded-lg p-1 opacity-60 transition hover:bg-black/20 hover:opacity-100"
              onClick={() => dismiss(notice.id)}
            >
              <IconClose className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </NoticeContext.Provider>
  );
}

export function useNotices() {
  const value = useContext(NoticeContext);
  if (!value) throw new Error("useNotices must be used inside NoticeProvider");
  return value;
}
