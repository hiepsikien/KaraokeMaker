import { Suspense } from "react";

import { StudioWorkspace } from "@/components/studio-workspace";

export default function StudioPage() {
  return (
    <Suspense fallback={<p className="py-10 text-sm text-[var(--muted)]">Đang mở AI Studio…</p>}>
      <StudioWorkspace />
    </Suspense>
  );
}
