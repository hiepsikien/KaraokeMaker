"use client";

import { useEffect, useState } from "react";

import { PageHeader } from "@/components/ui/page-header";
import { Badge, btnPrimary, btnSecondary, EmptyState, Panel, Spinner } from "@/components/ui/primitives";
import { IconFolder, IconPlay, IconPlus, IconSparkles } from "@/components/ui/icons";
import { useNotices } from "@/components/ui/toast";
import { readError } from "@/lib/api";
import { useGuardedNavigate } from "@/lib/navigation";
import { formatRelative, profileMeta } from "@/lib/format";

type RecentProject = {
  path: string;
  title: string;
  artist: string;
  status: string;
  updated: string;
  output_profile?: string;
};

const STATUS: Record<string, { label: string; tone: "muted" | "gold" | "success" | "info" }> = {
  draft: { label: "Nháp", tone: "muted" },
  aligned: { label: "Đã ghép lời", tone: "info" },
  exported: { label: "Đã xuất", tone: "success" },
};

export function Dashboard() {
  const navigate = useGuardedNavigate();
  const { push } = useNotices();
  const [projects, setProjects] = useState<RecentProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/projects/recent?limit=8", { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readError(response));
        const data = (await response.json()) as { projects?: RecentProject[] };
        if (!cancelled) setProjects(Array.isArray(data.projects) ? data.projects : []);
      })
      .catch(() => {
        if (!cancelled) {
          setError("Không tải được dự án gần đây. Kiểm tra server Python rồi thử lại.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function openFile() {
    setBusy(true);
    try {
      const response = await fetch("/api/browse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "project", initial_dir: "" }),
      });
      if (!response.ok) {
        push({ tone: "error", message: await readError(response) });
        return;
      }
      const data = (await response.json()) as { paths?: string[] };
      const path = data.paths?.[0];
      if (!path) return;
      await navigate(`/project?project=${encodeURIComponent(path)}`);
    } catch {
      push({ tone: "error", message: "Không mở được hộp thoại chọn dự án. Kiểm tra server Python." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Dự án Karaoke"
        description="Tạo hoặc mở dự án, căn lời vào vocal, biên tập timing, rồi xuất hai video: có giọng và không lời."
        actions={
          <>
            <button type="button" className={btnSecondary} onClick={openFile} disabled={busy}>
              <IconFolder />
              {busy ? "Đang mở…" : "Mở file dự án"}
            </button>
            <button type="button" className={btnPrimary} onClick={() => void navigate("/project?new=1")}>
              <IconPlus />
              Tạo dự án mới
            </button>
          </>
        }
      />

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section aria-labelledby="recent-title">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 id="recent-title" className="text-sm font-semibold tracking-[0.14em] text-[var(--gold)] uppercase">
              Dự án gần đây
            </h2>
            {loading ? (
              <span className="inline-flex items-center gap-2 text-xs text-[var(--muted)]">
                <Spinner /> Đang tải…
              </span>
            ) : null}
          </div>

          {error ? (
            <p className="mb-3 rounded-xl border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs leading-5 text-amber-100">
              {error} Bạn vẫn có thể tạo mới hoặc mở file.
            </p>
          ) : null}

          {!loading && projects.length === 0 ? (
            <EmptyState
              title="Chưa có dự án nào"
              description="Tạo dự án mới để khai báo audio, lời và ảnh. Dự án đã lưu sẽ xuất hiện ở đây để mở lại bằng một click."
              action={
                <button type="button" className={btnPrimary} onClick={() => void navigate("/project?new=1")}>
                  <IconPlus /> Tạo dự án mới
                </button>
              }
            />
          ) : (
            <ul className="grid gap-3 sm:grid-cols-2">
              {projects.map((project) => {
                const status = STATUS[project.status] || STATUS.draft;
                const profile = profileMeta(project.output_profile);
                return (
                  <li key={project.path}>
                    <button
                      type="button"
                      onClick={() => void navigate(`/project?project=${encodeURIComponent(project.path)}`)}
                      className="group flex h-full w-full flex-col gap-2 rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-4 text-left transition hover:border-[var(--gold)] hover:bg-black/20"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-base font-semibold">
                            {project.title || "Chưa đặt tên"}
                          </p>
                          <p className="truncate text-xs text-[var(--muted)]">
                            {project.artist || "Chưa có tác giả"}
                          </p>
                        </div>
                        <Badge tone={status.tone}>{status.label}</Badge>
                      </div>
                      <div className="mt-auto flex items-center justify-between gap-2 text-[11px] text-[var(--muted)]">
                        <span className="truncate">
                          {profile.label} · {formatRelative(project.updated)}
                        </span>
                        <span className="inline-flex items-center gap-1 text-[var(--gold)] opacity-0 transition group-hover:opacity-100">
                          <IconPlay className="h-3 w-3" /> Mở
                        </span>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <aside className="space-y-4 lg:sticky lg:top-20">
          <Panel className="space-y-3">
            <div className="flex items-center gap-2">
              <IconSparkles className="h-4 w-4 text-[var(--gold)]" />
              <h2 className="text-sm font-semibold">Luồng làm việc</h2>
            </div>
            <ol className="space-y-2 text-sm leading-6 text-[var(--muted)]">
              <li>
                <span className="text-[var(--paper)]">1. Chuẩn bị</span> — audio, lời, ảnh, hiệu ứng.
              </li>
              <li>
                <span className="text-[var(--paper)]">2. Biên tập</span> — nghe và chỉnh timing từng chữ.
              </li>
              <li>
                <span className="text-[var(--paper)]">3. Xuất</span> — hai video trong một lần.
              </li>
            </ol>
            <p className="text-xs leading-5 text-[var(--muted)]">
              Đã có vocal và instrumental thì bỏ qua bước tách/trộn; vào thẳng ghép chữ.
            </p>
          </Panel>

          <Panel className="space-y-2 text-xs leading-5 text-[var(--muted)]">
            <h2 className="text-sm font-semibold text-[var(--paper)]">Mẹo</h2>
            <p>
              Dùng <span className="text-[var(--paper)]">AI Studio</span> để làm clip nền động, lưu vào Thư viện clip,
              rồi chọn lại trong mục Hiệu ứng nền của dự án.
            </p>
            <button
              type="button"
              onClick={() => void navigate("/studio")}
              className="text-left text-[var(--gold)] hover:text-white"
            >
              Mở AI Studio →
            </button>
          </Panel>
        </aside>
      </div>
    </>
  );
}
