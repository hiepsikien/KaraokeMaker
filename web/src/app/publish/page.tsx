import { Suspense } from "react";

import { PublishWorkspace } from "@/components/publish/publish-workspace";

export default function PublishPage() {
  return (
    <Suspense fallback={<p className="py-10 text-sm text-[var(--muted)]">Đang mở trang đăng bài…</p>}>
      <PublishWorkspace />
    </Suspense>
  );
}
