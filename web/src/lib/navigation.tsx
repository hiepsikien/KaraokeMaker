"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";

import { useDialog } from "@/components/ui/dialog";
import { useNotices } from "@/components/ui/toast";
import { discardAllDirty, hasDirty, saveAllDirty } from "@/lib/dirty";

/**
 * Navigation that respects unsaved editor state. Use this for every internal
 * route change that can unmount a dirty editor, not just the top nav.
 */
export function useGuardedNavigate() {
  const router = useRouter();
  const { choose } = useDialog();
  const { push } = useNotices();

  return useCallback(
    async (href: string) => {
      if (typeof window !== "undefined") {
        const current = `${window.location.pathname}${window.location.search}`;
        if (href === current) return;
      }
      if (!hasDirty()) {
        router.push(href);
        return;
      }
      const result = await choose({
        title: "Còn thay đổi chưa lưu",
        description:
          "Dự án đang có thay đổi chưa lưu. Chọn Lưu rồi rời để giữ thay đổi, hoặc rời và bỏ bản nháp trong phiên này.",
        choices: [
          { label: "Lưu rồi rời", value: "save", tone: "primary" },
          { label: "Rời không lưu", value: "discard", tone: "danger" },
          { label: "Ở lại", value: "stay" },
        ],
      });
      if (!result || result.value === "stay") return;
      if (result.value === "save") {
        const ok = await saveAllDirty();
        if (!ok) {
          push({
            tone: "error",
            message: "Chưa lưu được. Hãy thử lại trước khi rời trang.",
          });
          return;
        }
      } else if (result.value === "discard") {
        discardAllDirty();
      }
      router.push(href);
    },
    [choose, push, router],
  );
}
