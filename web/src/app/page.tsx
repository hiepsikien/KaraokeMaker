import { MakerForm } from "@/components/maker-form";

export default function HomePage() {
  return (
    <main className="mx-auto max-w-6xl px-5 py-8 sm:px-8">
      <header className="mb-8 flex items-end justify-between gap-6 border-b border-[var(--line)] pb-6">
        <div>
          <p className="text-xs font-semibold tracking-[0.22em] text-[var(--gold)] uppercase">Studio</p>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight">KaraokeMaker</h1>
        </div>
        <p className="hidden max-w-sm text-right text-sm leading-6 text-[var(--muted)] sm:block">
          Căn lời vào vocal, lặp ảnh nền, rồi xuất hai video: có giọng và không lời.
        </p>
      </header>
      <MakerForm />
    </main>
  );
}
