import { StudioWorkspace } from "@/components/studio-workspace";

export default function StudioPage() {
  return (
    <main className="mx-auto max-w-[96rem] px-5 py-8 sm:px-8">
      <header className="mb-8 flex items-end justify-between gap-6 border-b border-[var(--line)] pb-6">
        <div>
          <p className="text-xs font-semibold tracking-[0.22em] text-[var(--gold)] uppercase">Workspace</p>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight">AI Studio</h1>
        </div>
        <p className="hidden max-w-sm text-right text-sm leading-6 text-[var(--muted)] sm:block">
          Tạo nền sống từ ảnh tĩnh; preview 16:9 hoặc 9:16; chưa lưu asset vào dự án.
        </p>
      </header>
      <StudioWorkspace />
    </main>
  );
}
