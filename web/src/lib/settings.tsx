"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { readError } from "@/lib/api";
import { useNotices } from "@/components/ui/toast";

export type AppSettings = {
  gemini_consent: boolean;
  gemini_api_key_set: boolean;
  gemini_api_key?: string;
  clear_gemini_api_key?: boolean;
  google_audio_consent: boolean;
  align_strategy: "whisper" | "gemini_lines";
  gemini_model: string;
  device: string;
  model_name: string;
  output_dir: string;
};

export const DEFAULT_SETTINGS: AppSettings = {
  gemini_consent: false,
  gemini_api_key_set: false,
  google_audio_consent: false,
  align_strategy: "gemini_lines",
  gemini_model: "gemini-3.8-flash",
  device: "cpu",
  model_name: "small",
  output_dir: "",
};

type SettingsContextValue = {
  settings: AppSettings;
  loading: boolean;
  available: boolean;
  open: boolean;
  openSettings: () => void;
  closeSettings: () => void;
  save: (patch: Partial<AppSettings>) => Promise<boolean>;
};

const SettingsContext = createContext<SettingsContextValue | null>(null);

export function SettingsProvider({ children }: { children: ReactNode }) {
  const { push } = useNotices();
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [available, setAvailable] = useState(true);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/settings", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        const data = (await response.json()) as { settings?: AppSettings };
        if (!cancelled && data.settings) {
          setSettings({ ...DEFAULT_SETTINGS, ...data.settings });
          setAvailable(true);
        }
      })
      .catch(() => {
        if (!cancelled) setAvailable(false);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const save = useCallback(
    async (patch: Partial<AppSettings>) => {
      const previous = settings;
      const next = { ...settings, ...patch };
      setSettings(next);
      try {
        const response = await fetch("/api/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(next),
        });
        if (!response.ok) {
          push({ tone: "error", message: await readError(response) });
          setSettings(previous);
          setAvailable(false);
          return false;
        }
        const data = (await response.json()) as { settings?: AppSettings };
        if (data.settings) setSettings({ ...DEFAULT_SETTINGS, ...data.settings });
        setAvailable(true);
        push({ tone: "success", message: "Đã lưu cài đặt." });
        return true;
      } catch {
        push({
          tone: "error",
          message: "Chưa lưu được cài đặt. Server Python chưa chạy hoặc chưa có API settings.",
        });
        setSettings(previous);
        setAvailable(false);
        return false;
      }
    },
    [push, settings],
  );

  const value = useMemo(
    () => ({
      settings,
      loading,
      available,
      open,
      openSettings: () => setOpen(true),
      closeSettings: () => setOpen(false),
      save,
    }),
    [settings, loading, available, open, save],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings() {
  const value = useContext(SettingsContext);
  if (!value) throw new Error("useSettings must be used inside SettingsProvider");
  return value;
}
