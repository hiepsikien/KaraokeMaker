import { useEffect, useState } from "react";

const PREFIX = "karaoke.ux.v1.";

export function readSession<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.sessionStorage.getItem(PREFIX + key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

export function writeSession<T>(key: string, value: T) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* quota or private mode: keep the in-memory state */
  }
}

export function clearSession(key: string) {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(PREFIX + key);
  } catch {
    /* ignore */
  }
}

/** State that survives route changes and refreshes within the same tab. */
export function useSessionState<T>(
  key: string,
  initial: T | (() => T),
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    const fallback = typeof initial === "function" ? (initial as () => T)() : initial;
    return readSession(key, fallback);
  });
  useEffect(() => {
    writeSession(key, value);
  }, [key, value]);

  return [value, setValue];
}

export const sessionKeys = {
  makerPhase: "maker.phase",
  makerFields: "maker.fields",
  makerHasLines: "maker.hasLines",
  makerAlignReturn: "maker.alignReturn",
  makerJob: "maker.jobId",
  studioImages: "studio.images",
  studioEffects: "studio.effects",
  studioProfile: "studio.profile",
  studioDuration: "studio.duration",
  studioTab: "studio.tab",
  timingDraft: (jobId: string) => `timing.${jobId}`,
};
