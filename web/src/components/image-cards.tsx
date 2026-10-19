"use client";

import { useEffect, useState } from "react";

export type ImageFact = {
  path: string;
  width: number;
  height: number;
  ratio: string;
  fits_frame: boolean;
  note: string;
  thumbnail: string;
};

function fileName(path: string) {
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

export function ImageCards({
  paths,
  onRemove,
}: {
  paths: string[];
  onRemove: (path: string) => void;
}) {
  const [facts, setFacts] = useState<ImageFact[]>([]);
  const signature = paths.join("\n");

  useEffect(() => {
    const current = signature.split("\n").filter(Boolean);
    if (current.length === 0) {
      setFacts([]);
      return;
    }
    const controller = new AbortController();
    fetch("/api/images/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: current }),
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : { images: [] }))
      .then((data) => setFacts(data.images || []))
      .catch(() => {
        if (!controller.signal.aborted) setFacts([]);
      });
    return () => controller.abort();
  }, [signature]);

  if (paths.length === 0) return null;

  const known = new Map(facts.map((fact) => [fact.path, fact]));

  return (
    <ul className="flex gap-3 overflow-x-auto pb-1">
      {paths.map((path) => {
        const fact = known.get(path);
        return (
          <li key={path} className="w-44 shrink-0 rounded-2xl border border-[var(--line)] bg-black/20 p-2">
            <div className="flex h-28 items-center justify-center overflow-hidden rounded-xl bg-black">
              {fact?.thumbnail ? (
                <img src={fact.thumbnail} alt="" className="max-h-full max-w-full object-contain" />
              ) : (
                <span className="text-xs text-[var(--muted)]">Đang đọc…</span>
              )}
            </div>
            <p className="mt-2 truncate text-xs">{fileName(path)}</p>
            <p className="text-xs text-[var(--muted)]">
              {fact && fact.width > 0 ? `${fact.width}×${fact.height} · ${fact.ratio}` : "Chưa rõ kích thước"}
            </p>
            <p className={`mt-1 text-xs leading-4 ${fact?.fits_frame ? "text-[var(--gold)]" : "text-amber-100/90"}`}>
              {fact?.note || ""}
            </p>
            <button type="button" className="mt-2 text-xs text-[var(--muted)] hover:text-white" onClick={() => onRemove(path)}>
              Xóa
            </button>
          </li>
        );
      })}
    </ul>
  );
}
