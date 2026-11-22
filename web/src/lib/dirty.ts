"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";

type DirtyEntry = {
  dirty: boolean;
  save?: () => boolean | Promise<boolean>;
  discard?: () => void;
};

const entries = new Map<string, DirtyEntry>();
const listeners = new Set<() => void>();

function notify() {
  listeners.forEach((listener) => listener());
}

export function registerDirty(
  key: string,
  dirty: boolean,
  save?: DirtyEntry["save"],
  discard?: DirtyEntry["discard"],
) {
  if (!dirty) {
    if (entries.delete(key)) notify();
    return;
  }
  const existing = entries.get(key);
  entries.set(key, { dirty: true, save, discard });
  if (!existing) notify();
}

export function hasDirty() {
  return entries.size > 0;
}

export function dirtyKeys() {
  return [...entries.keys()];
}

export async function saveAllDirty() {
  for (const entry of entries.values()) {
    if (!entry.save) continue;
    try {
      const ok = await entry.save();
      if (!ok) return false;
    } catch {
      return false;
    }
  }
  return true;
}

export function clearDirty(key: string) {
  if (entries.delete(key)) notify();
}

export function discardAllDirty() {
  if (entries.size === 0) return;
  for (const entry of entries.values()) {
    try {
      entry.discard?.();
    } catch {
      /* keep discarding the rest */
    }
  }
  entries.clear();
  notify();
}

export function clearAllDirty() {
  if (entries.size === 0) return;
  entries.clear();
  notify();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useHasDirty() {
  return useSyncExternalStore(subscribe, () => entries.size > 0, () => false);
}

/** Register a component's unsaved edits so navigation can guard them. */
export function useDirty(
  key: string,
  dirty: boolean,
  save?: DirtyEntry["save"],
  discard?: DirtyEntry["discard"],
) {
  const saveRef = useRef(save);
  const discardRef = useRef(discard);
  saveRef.current = save;
  discardRef.current = discard;

  useEffect(() => {
    registerDirty(
      key,
      dirty,
      save ? () => saveRef.current?.() ?? true : undefined,
      discard ? () => discardRef.current?.() : undefined,
    );
    return () => registerDirty(key, false);
  }, [key, dirty, Boolean(save), Boolean(discard)]);
}
