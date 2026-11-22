"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/primitives";
import { SettingsDrawer } from "@/components/settings-drawer";
import { IconFilm, IconFolder, IconSend, IconSettings } from "@/components/ui/icons";
import { cn } from "@/lib/cn";
import { useHasDirty } from "@/lib/dirty";
import { activeJobLabel, useActiveJobs } from "@/lib/jobs";
import { useSettings } from "@/lib/settings";
import { useGuardedNavigate } from "@/lib/navigation";

const NAV = [
  { href: "/", label: "Dự án", icon: IconFolder },
  { href: "/studio", label: "AI Studio", icon: IconFilm },
  { href: "/publish", label: "Đăng bài", icon: IconSend },
];

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const navigate = useGuardedNavigate();
  const { openSettings } = useSettings();
  const activeJobs = useActiveJobs();
  const dirty = useHasDirty();
  const [offline, setOffline] = useState(false);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (!dirty) return;
    function onBeforeUnload(event: BeforeUnloadEvent) {
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [dirty]);

  useEffect(() => {
    let cancelled = false;
    async function check() {
      setChecking(true);
      try {
        const response = await fetch("/api/health", { cache: "no-store" });
        if (!cancelled) setOffline(!response.ok);
      } catch {
        if (!cancelled) setOffline(true);
      } finally {
        if (!cancelled) setChecking(false);
      }
    }
    void check();
    const timer = window.setInterval(check, 20_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-50 border-b border-[var(--line)] bg-[var(--ink)]/85 backdrop-blur">
        <div className="mx-auto flex max-w-[96rem] flex-wrap items-center gap-3 px-4 py-3 sm:px-6 lg:px-8">
          <button
            type="button"
            onClick={() => void navigate("/")}
            className="flex items-center gap-2 text-sm font-semibold tracking-wide"
          >
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-[var(--gold)] text-[#1a1206]">
              ◆
            </span>
            <span className="hidden sm:inline">KaraokeMaker</span>
          </button>

          <nav aria-label="Điều hướng chính" className="flex items-center gap-1 text-sm">
            {NAV.map((item) => {
              const active =
                item.href === "/" ? pathname === "/" || pathname.startsWith("/project") : pathname.startsWith(item.href);
              const Icon = item.icon;
              return (
                <a
                  key={item.href}
                  href={item.href}
                  aria-current={active ? "page" : undefined}
                  onClick={(event) => {
                    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
                    event.preventDefault();
                    void navigate(item.href);
                  }}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 transition",
                    active
                      ? "bg-[var(--gold)] font-semibold text-[#1a1206]"
                      : "text-[var(--muted)] hover:bg-white/5 hover:text-[var(--paper)]",
                  )}
                >
                  <Icon className="h-4 w-4" />
                  {item.label}
                </a>
              );
            })}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            {dirty ? <Badge tone="gold">Chưa lưu</Badge> : null}
            {activeJobs.length > 0 ? (
              <Badge tone="gold" className="max-w-[15rem]">
                <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--gold)]" />
                <span className="truncate">{activeJobLabel(activeJobs[0])}</span>
                {activeJobs.length > 1 ? <span>· +{activeJobs.length - 1}</span> : null}
              </Badge>
            ) : null}
            <button
              type="button"
              onClick={openSettings}
              aria-label="Cài đặt"
              title="Cài đặt"
              className="rounded-full border border-[var(--line)] p-2 text-[var(--muted)] transition hover:border-[var(--gold)] hover:text-[var(--gold)]"
            >
              <IconSettings className="h-4 w-4" />
            </button>
          </div>
        </div>
      </header>

      {offline ? (
        <div
          role="alert"
          className="border-b border-red-900/70 bg-red-950/70 px-4 py-2 text-sm text-red-50"
        >
          <div className="mx-auto flex max-w-[96rem] flex-wrap items-center gap-3 px-0 sm:px-2 lg:px-4">
            <span>Chưa kết nối phần xử lý (server Python).</span>
            <button
              type="button"
              disabled={checking}
              onClick={async () => {
                setChecking(true);
                try {
                  const response = await fetch("/api/health", { cache: "no-store" });
                  setOffline(!response.ok);
                } catch {
                  setOffline(true);
                } finally {
                  setChecking(false);
                }
              }}
              className="rounded-lg border border-red-800 px-2.5 py-1 text-xs font-semibold hover:bg-red-900/60 disabled:opacity-60"
            >
              {checking ? "Đang thử…" : "Thử lại"}
            </button>
            <span className="text-xs text-red-200/80">
              Chạy <code>.venv-karaoke/bin/python -m karaoke</code> ở thư mục gốc.
            </span>
          </div>
        </div>
      ) : null}

      <main className="mx-auto w-full max-w-[96rem] px-4 py-5 sm:px-6 lg:px-8">{children}</main>
      <SettingsDrawer />
    </div>
  );
}
