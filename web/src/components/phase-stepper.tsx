import type { Phase } from "@/lib/types";

// Presentation only: several internal phases share one user-facing step.
// The state machine (Phase) stays unchanged; "home" hides the stepper.
const STEPS: { id: string; label: string; phases: Phase[] }[] = [
  { id: "prepare", label: "Chuẩn bị", phases: ["setup", "restore", "align"] },
  { id: "edit", label: "Biên tập", phases: ["edit"] },
  { id: "export", label: "Xuất", phases: ["export"] },
];

function stepIndex(phase: Phase) {
  if (phase === "home") return -1;
  return STEPS.findIndex((step) => step.phases.includes(phase));
}

export function PhaseStepper({ phase }: { phase: Phase }) {
  if (phase === "home") return null;
  const current = stepIndex(phase);

  return (
    <ol className="mb-6 flex flex-wrap gap-2 border-b border-[var(--line)] pb-4">
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
  );
}
