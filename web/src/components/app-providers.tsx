"use client";

import type { ReactNode } from "react";

import { DialogProvider } from "@/components/ui/dialog";
import { NoticeProvider } from "@/components/ui/toast";
import { SettingsProvider } from "@/lib/settings";

export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <NoticeProvider>
      <DialogProvider>
        <SettingsProvider>{children}</SettingsProvider>
      </DialogProvider>
    </NoticeProvider>
  );
}
