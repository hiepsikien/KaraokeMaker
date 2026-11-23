import { useEffect, useRef, useState } from "react";

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

/** State that survives route changes and refreshes within the same tab.
 *
 * The first render always uses the server-safe fallback; sessionStorage is
 * loaded after mount. Reading it in the state initializer caused hydration
 * mismatches because the server has no sessionStorage.
 */
export function useSessionState<T>(
  key: string,
  initial: T | (() => T),
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const fallbackRef = useRef<T | undefined>(undefined);
  if (fallbackRef.current === undefined) {
    fallbackRef.current = typeof initial === "function" ? (initial as () => T)() : initial;
  }
  const [value, setValue] = useState<T>(fallbackRef.current as T);
  const hydrated = useRef(false);

  useEffect(() => {
    setValue(readSession(key, fallbackRef.current as T));
    hydrated.current = true;
  }, [key]);

  useEffect(() => {
    if (!hydrated.current) return;
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
