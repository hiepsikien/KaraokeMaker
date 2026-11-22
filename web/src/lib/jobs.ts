"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import type { Job } from "@/lib/types";

type Entry = {
  job: Job;
  listeners: Set<() => void>;
  placeholder?: boolean;
};

const entries = new Map<string, Entry>();
const activeListeners = new Set<() => void>();
let timer: number | null = null;
let activeSnapshot: Job[] = [];
let polling = false;

function isActive(job: Job) {
  return (
    job.status === "running" ||
    job.status === "rendering" ||
    job.status === "separating" ||
    job.background?.state === "building"
  );
}

function refreshActive() {
  const next = [...entries.values()]
    .filter((entry) => !entry.placeholder)
    .map((entry) => entry.job)
    .filter(isActive);
  if (
    next.length === activeSnapshot.length &&
    next.every((job, index) => job === activeSnapshot[index])
  ) {
    return;
  }
  activeSnapshot = next;
  activeListeners.forEach((listener) => listener());
}

function emit(id: string) {
  entries.get(id)?.listeners.forEach((listener) => listener());
  refreshActive();
}

async function pollOne(id: string, entry: Entry) {
  if (!isActive(entry.job)) return;
  try {
    const response = await fetch(`/api/jobs/${id}`, { cache: "no-store" });
    if (!response.ok) {
      if (response.status === 404) {
        entry.job = { ...entry.job, status: "error", error: "Không thấy tác vụ (server có thể đã restart)." };
        entry.placeholder = false;
        emit(id);
      }
      return;
    }
    const next = (await response.json()) as Job;
    entry.job = next;
    entry.placeholder = false;
    emit(id);
  } catch {
    /* keep the last known state; the global health banner handles offline */
  }
}

async function pollAll() {
  if (polling) return;
  polling = true;
  try {
    await Promise.all([...entries.entries()].map(([id, entry]) => pollOne(id, entry)));
  } finally {
    polling = false;
    const hasActive = [...entries.values()].some((entry) => isActive(entry.job));
    if (!hasActive && timer != null) {
      window.clearInterval(timer);
      timer = null;
    }
  }
}

function ensureTimer() {
  if (timer != null) return;
  timer = window.setInterval(() => void pollAll(), 1200);
}

export function trackJob(id: string | null | undefined, initial?: Job | null) {
  if (!id) return;
  let entry = entries.get(id);
  if (!entry) {
    entry = {
      job:
        initial ?? {
          id,
          status: "running",
          log: [],
          error: "",
          outputs: null,
          lines: null,
          duration: 0,
        },
      listeners: new Set(),
      placeholder: !initial,
    };
    entries.set(id, entry);
  } else if (initial) {
    entry.job = initial;
    entry.placeholder = false;
  }
  if (isActive(entry.job)) {
    ensureTimer();
    void pollOne(id, entry);
  }
  refreshActive();
}

function subscribe(id: string, listener: () => void) {
  let entry = entries.get(id);
  if (!entry) {
    entry = {
      job: { id, status: "running", log: [], error: "", outputs: null, lines: null, duration: 0 },
      listeners: new Set(),
      placeholder: true,
    };
    entries.set(id, entry);
  }
  entry.listeners.add(listener);
  return () => entry?.listeners.delete(listener);
}

export function useTrackedJob(id: string | null | undefined) {
  const subscribeId = useCallback(
    (listener: () => void) => (id ? subscribe(id, listener) : () => undefined),
    [id],
  );
  const getSnapshot = useCallback(() => {
    if (!id) return null;
    const entry = entries.get(id);
    return entry?.placeholder ? null : entry?.job ?? null;
  }, [id]);

  useEffect(() => {
    if (id) trackJob(id);
  }, [id]);

  return useSyncExternalStore(subscribeId, getSnapshot, () => null);
}

function subscribeActive(listener: () => void) {
  activeListeners.add(listener);
  return () => activeListeners.delete(listener);
}

export function useActiveJobs() {
  return useSyncExternalStore(
    subscribeActive,
    () => activeSnapshot,
    () => [],
  );
}

export function activeJobLabel(job: Job) {
  if (job.status === "rendering") {
    return `Đang xuất video${job.render_percent ? ` ${job.render_percent}%` : "…"}`;
  }
  if (job.status === "separating") return "Đang tách giọng…";
  if (job.background?.state === "building") {
    return `Đang dựng nền${job.background.percent ? ` ${job.background.percent}%` : "…"}`;
  }
  return "Đang căn lời…";
}
