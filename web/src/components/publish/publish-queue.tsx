"use client";

import { Badge, EmptyState, Panel, ProgressBar, SectionTitle, btnGhost, btnSecondary } from "@/components/ui/primitives";
import { formatRelative } from "@/lib/format";
import { jobStatus, type PublishJob } from "@/lib/publish";

function isActive(job: PublishJob) {
  return ["queued", "initializing", "uploading", "finalizing", "cancel_requested"].includes(job.status);
}

const STEP_LABELS: Record<string, string> = {
  upload: "Tải video",
  playlist: "Thêm playlist",
  done: "Hoàn tất",
};

export function PublishQueue({
  jobs,
  busy,
  onRetry,
  onCancel,
  onRefresh,
}: {
  jobs: PublishJob[];
  busy: string;
  onRetry: (id: string) => void;
  onCancel: (id: string) => void;
  onRefresh: () => void;
}) {
  const active = jobs.filter(isActive);
  const history = jobs.filter((job) => !isActive(job)).sort((a, b) => {
    return (Date.parse(b.updated || b.created || "") || 0) - (Date.parse(a.updated || a.created || "") || 0);
  });

  return (
    <div className="space-y-4">
      <Panel className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SectionTitle>Hàng đợi</SectionTitle>
          <div className="flex items-center gap-2">
            <Badge tone={active.length ? "gold" : "muted"}>{active.length} đang chạy</Badge>
            <button type="button" className={btnGhost} onClick={onRefresh}>
              Làm mới
            </button>
          </div>
        </div>
        {active.length === 0 ? (
          <EmptyState
            title="Không có video đang đăng"
            description="Bấm Đăng ngay trong phần soạn nội dung. Một worker chạy tuần tự để tránh vượt hạn mức."
          />
        ) : (
          <ul className="space-y-2">
            {active.map((job) => {
              const status = jobStatus(job.status);
              const percent = Math.max(0, Math.min(100, Number(job.progress) || 0));
              const title = job.project_title || job.draft_id || job.id;
              return (
                <li key={job.id} className="space-y-2 rounded-xl border border-[var(--line)] bg-black/20 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{title}</p>
                      <p className="text-[11px] text-[var(--muted)]">
                        {job.platform || "youtube"}
                        {job.video_role ? ` · ${job.video_role === "vocal" ? "có giọng" : "không lời"}` : ""}
                        {job.step ? ` · ${STEP_LABELS[job.step] || job.step}` : ""}
                        {job.attempts ? ` · lần ${job.attempts}` : ""}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge tone={status.tone}>{status.label}</Badge>
                      <span className="text-xs tabular-nums text-[var(--muted)]">{percent}%</span>
                    </div>
                  </div>
                  <ProgressBar percent={percent} />
                  {job.last_error ? (
                    <p className="rounded-lg bg-red-950/40 px-2 py-1 text-[11px] leading-5 text-red-100">
                      {job.last_error}
                    </p>
                  ) : null}
                  <div className="flex flex-wrap gap-2">
                    {job.status === "failed" ? (
                      <button
                        type="button"
                        className={btnSecondary}
                        disabled={busy !== ""}
                        onClick={() => onRetry(job.id)}
                      >
                        {busy === `retry:${job.id}` ? "Đang thử…" : "Thử lại"}
                      </button>
                    ) : null}
                    {job.status !== "cancel_requested" ? (
                      <button
                        type="button"
                        className={btnGhost}
                        disabled={busy !== ""}
                        onClick={() => onCancel(job.id)}
                      >
                        {busy === `cancel:${job.id}` ? "Đang huỷ…" : "Huỷ"}
                      </button>
                    ) : null}
                  </div>
                  {job.status === "cancel_requested" ? (
                    <p className="text-[11px] leading-4 text-[var(--muted)]">
                      Đang yêu cầu huỷ. Video đã gửi xong có thể không thu hồi được.
                    </p>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </Panel>

      <Panel className="space-y-3">
        <SectionTitle action={<span className="text-xs text-[var(--muted)]">{history.length} lần</span>}>
          Lịch sử
        </SectionTitle>
        {history.length === 0 ? (
          <p className="text-xs leading-5 text-[var(--muted)]">Chưa có lần đăng nào.</p>
        ) : (
          <ul className="max-h-80 space-y-2 overflow-auto pr-1">
            {history.map((job) => {
              const status = jobStatus(job.status);
              const url = job.url;
              return (
                <li
                  key={job.id}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--line)] bg-black/15 px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm">{job.project_title || job.draft_id || job.id}</p>
                    <p className="text-[11px] text-[var(--muted)]">
                      {formatRelative(job.updated || job.created) || "—"}
                      {job.video_role ? ` · ${job.video_role === "vocal" ? "có giọng" : "không lời"}` : ""}
                    </p>
                    {job.last_error ? (
                      <p className="mt-1 text-[11px] leading-4 text-red-200">{job.last_error}</p>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge tone={status.tone}>{status.label}</Badge>
                    {url ? (
                      <a href={url} target="_blank" rel="noreferrer" className="text-xs text-[var(--gold)] hover:text-white">
                        Mở
                      </a>
                    ) : null}
                    {job.status === "failed" ? (
                      <button
                        type="button"
                        className="text-xs text-[var(--gold)] hover:text-white"
                        disabled={busy !== ""}
                        onClick={() => onRetry(job.id)}
                      >
                        Thử lại
                      </button>
                    ) : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}
