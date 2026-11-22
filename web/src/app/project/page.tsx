import { Suspense } from "react";

import { MakerForm } from "@/components/maker-form";

export default function ProjectPage() {
  return (
    <Suspense fallback={<p className="py-10 text-sm text-[var(--muted)]">Đang mở dự án…</p>}>
      <MakerForm />
    </Suspense>
  );
}
