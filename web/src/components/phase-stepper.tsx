import type { Phase } from "@/lib/types";

const STEPS: { id: string; label: string; phases: Phase[] }[] = [
  { id: "prepare", label: "Chuẩn bị", phases: ["setup", "restore", "align"] },
  { id: "edit", label: "Biên tập", phases: ["edit", "export"] },
];

function stepIndex(phase: Phase) {
  if (phase === "home") return -1;
  return STEPS.findIndex((step) => step.phases.includes(phase));
}

export function PhaseStepper({
  phase,
  rendering = false,
  exported = false,
}: {
  phase: Phase;
  rendering?: boolean;
  exported?: boolean;
}) {
  if (phase === "home") return null;
  const current = stepIndex(phase);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <ol className="flex flex-wrap gap-2">
        {STEPS.map((step, index) => {
          const active = index === current;
          const done = index < current;
          return (
            <li
              key={step.id}
              className={`rounded-full px-3 py-1 text-xs font-semibold tracking-[0.12em] uppercase ${
                active
                  ? "bg-[var(--gold)] text-[#1a1206]"
                  : done
                    ? "border border-[var(--gold)]/50 text-[var(--gold)]"
                    : "border border-[var(--line)] text-[var(--muted)]"
              }`}
            >
              {index + 1}. {step.label}
            </li>
          );
        })}
      </ol>
      <span
        className={`rounded-full border px-2.5 py-1 text-[11px] ${
          rendering
            ? "border-[var(--gold)]/60 text-[var(--gold)]"
            : exported
              ? "border-emerald-800 text-emerald-200"
              : "border-[var(--line)] text-[var(--muted)]"
        }`}
      >
        {rendering ? "Đang xuất video…" : exported ? "Đã xuất video" : "Chưa xuất"}
      </span>
    </div>
  );
}
