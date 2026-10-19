import type { Phase } from "@/lib/types";

const STEPS: { id: Phase; label: string }[] = [
  { id: "setup", label: "Khai báo" },
  { id: "restore", label: "Tách và trộn" },
  { id: "align", label: "Ghép chữ" },
  { id: "edit", label: "Biên tập" },
  { id: "export", label: "Xuất" },
];

function stepIndex(phase: Phase) {
  if (phase === "home") return -1;
  if (phase === "export") return STEPS.length - 1;
  return STEPS.findIndex((step) => step.id === phase);
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
