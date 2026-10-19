export function HomeGate({
  onCreate,
  onOpen,
  busy,
  notice,
}: {
  onCreate: () => void;
  onOpen: () => void;
  busy: boolean;
  notice: string;
}) {
  return (
    <div className="mx-auto max-w-xl space-y-5 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-6 sm:p-8">
      <div>
        <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Dự án</p>
        <h2 className="mt-2 text-2xl font-semibold">Bắt đầu thế nào?</h2>
        <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
          Tạo dự án mới để khai báo file và căn lời, hoặc mở file dự án đã lưu để sửa tiếp và xuất video.
        </p>
      </div>
      {notice && <p className="rounded-xl bg-red-950/60 px-3 py-2 text-sm text-red-100">{notice}</p>}
      <button
        type="button"
        disabled={busy}
        onClick={onCreate}
        className="w-full rounded-2xl bg-[var(--gold)] px-4 py-3 text-sm font-semibold text-[#1a1206] disabled:opacity-60"
      >
        Tạo dự án mới
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={onOpen}
        className="w-full rounded-2xl border border-[var(--gold)] px-4 py-3 text-sm font-semibold text-[var(--gold)] disabled:opacity-60"
      >
        {busy ? "Đang mở…" : "Mở dự án đã có"}
      </button>
      <p className="text-xs leading-5 text-[var(--muted)]">
        Chọn file <span className="text-[var(--paper)]">.karaoke.json</span>. Bản{" "}
        <span className="text-[var(--paper)]">… timing.json</span> cũ vẫn mở được và sẽ được chuyển sang file dự án.
      </p>
    </div>
  );
}
