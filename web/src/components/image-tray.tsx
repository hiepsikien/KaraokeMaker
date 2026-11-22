"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/primitives";
import { IconPlus, IconClose } from "@/components/ui/icons";
import { fileName } from "@/lib/format";
import type { ImageFact } from "@/components/image-cards";

export function ImageTray({
  paths,
  selected,
  outputProfile,
  onSelect,
  onRemove,
  onAdd,
  busyAdd = false,
}: {
  paths: string[];
  selected: string;
  outputProfile: string;
  onSelect: (path: string) => void;
  onRemove: (path: string) => void;
  onAdd: () => void;
  busyAdd?: boolean;
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
      body: JSON.stringify({ paths: current, output_profile: outputProfile, with_preview: false }),
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : { images: [] }))
      .then((data: { images?: ImageFact[] }) => setFacts(data.images || []))
      .catch(() => {
        if (!controller.signal.aborted) setFacts([]);
      });
    return () => controller.abort();
  }, [signature, outputProfile]);

  const known = new Map(facts.map((fact) => [fact.path, fact]));

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">
          Ảnh nguồn
        </h2>
        <button
          type="button"
          onClick={onAdd}
          disabled={busyAdd}
          className="inline-flex items-center gap-1 rounded-lg border border-[var(--line)] px-2 py-1 text-xs text-[var(--muted)] transition hover:border-[var(--gold)] hover:text-[var(--gold)] disabled:opacity-60"
        >
          <IconPlus className="h-3.5 w-3.5" />
          {busyAdd ? "Đang mở…" : "Thêm"}
        </button>
      </div>

      {paths.length === 0 ? (
        <button
          type="button"
          onClick={onAdd}
          className="w-full rounded-xl border border-dashed border-[var(--line)] px-3 py-6 text-xs leading-5 text-[var(--muted)] transition hover:border-[var(--gold)]"
        >
          Chưa có ảnh. Bấm để chọn ảnh tĩnh.
        </button>
      ) : (
        <ul className="grid grid-cols-2 gap-2">
          {paths.map((path) => {
            const fact = known.get(path);
            const active = path === selected;
            return (
              <li key={path} className="group relative">
                <button
                  type="button"
                  onClick={() => onSelect(path)}
                  className={`block w-full overflow-hidden rounded-xl border bg-black/30 text-left transition ${
                    active ? "border-[var(--gold)]" : "border-[var(--line)] hover:border-[var(--gold)]/60"
                  }`}
                >
                  <span className="flex aspect-[4/3] items-center justify-center overflow-hidden">
                    {fact?.thumbnail ? (
                      <img src={fact.thumbnail} alt="" className="max-h-full max-w-full object-contain" />
                    ) : (
                      <span className="text-[10px] text-[var(--muted)]">Đang đọc…</span>
                    )}
                  </span>
                  <span className="block px-1.5 py-1">
                    <span className="block truncate text-[11px]">{fileName(path)}</span>
                    <span className="block truncate text-[10px] text-[var(--muted)]">
                      {fact && fact.width > 0 ? `${fact.width}×${fact.height}` : "\u00a0"}
                    </span>
                  </span>
                </button>
                {active ? (
                  <span className="pointer-events-none absolute top-1 left-1">
                    <Badge tone="gold" className="bg-black/70">
                      Đang chọn
                    </Badge>
                  </span>
                ) : null}
                <button
                  type="button"
                  aria-label={`Xoá ${fileName(path)}`}
                  onClick={() => onRemove(path)}
                  className="absolute top-1 right-1 rounded-md bg-black/70 p-1 text-[var(--muted)] opacity-0 transition hover:text-red-200 group-hover:opacity-100 focus:opacity-100"
                >
                  <IconClose className="h-3 w-3" />
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
