"use client";

import type { ProjectFields } from "@/lib/types";

const fieldClass =
  "w-full rounded-xl border border-[var(--line)] bg-black/30 px-3 py-2 text-sm outline-none focus:border-[var(--gold)]";

const MODELS: { value: string; label: string }[] = [
  { value: "tiny", label: "Tiny, nhanh nhất" },
  { value: "base", label: "Base" },
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
  { value: "large-v2", label: "Large v2" },
  { value: "large", label: "Large (bản v3)" },
];

export function AlignOptions({
  fields,
  setFields,
  replacesEdits,
  notice,
  onStart,
  onBack,
}: {
  fields: ProjectFields;
  setFields: (updater: (current: ProjectFields) => ProjectFields) => void;
  replacesEdits: boolean;
  notice: string;
  onStart: () => void;
  onBack: () => void;
}) {
  return (
    <section className="mx-auto max-w-2xl rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-6">
      <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Ghép chữ</p>
      <h2 className="mt-2 text-xl font-semibold">Chọn model Whisper</h2>
      <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
        Whisper nghe vocal và ghi chữ cùng thời gian. Máy chưa có model thì sẽ tải về khi bấm bắt đầu. Large khoảng 3 GB.
      </p>
      {replacesEdits && (
        <p className="mt-3 rounded-xl bg-amber-950/50 px-3 py-2 text-sm text-amber-100">
          Ghép lại sẽ thay toàn bộ timing đang sửa, kể cả chỗ đã kéo tay.
        </p>
      )}

      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Model</span>
          <select
            className={fieldClass}
            value={fields.model_name}
            onChange={(event) => setFields((current) => ({ ...current, model_name: event.target.value }))}
          >
            {MODELS.map((model) => (
              <option key={model.value} value={model.value}>
                {model.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Thiết bị</span>
          <select
            className={fieldClass}
            value={fields.device}
            onChange={(event) => setFields((current) => ({ ...current, device: event.target.value }))}
          >
            <option value="cpu">CPU</option>
            <option value="mps">GPU Apple</option>
          </select>
        </label>
        <label className="block sm:col-span-2">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Ngưỡng không-lời (0–1, để trống là tắt)</span>
          <input
            className={fieldClass}
            type="number"
            min="0"
            max="1"
            step="0.05"
            placeholder="Tắt, giữ cả đoạn hát bị nghi là không lời"
            value={fields.no_speech_threshold}
            onChange={(event) => setFields((current) => ({ ...current, no_speech_threshold: event.target.value }))}
          />
        </label>
        <label className="flex items-start gap-3 sm:col-span-2">
          <input
            type="checkbox"
            className="mt-1"
            checked={fields.whisper_retry}
            onChange={(event) => setFields((current) => ({ ...current, whisper_retry: event.target.checked }))}
          />
          <span className="text-sm leading-6">
            Thử lại khi một câu bị lặp. Bỏ chọn thì khóa nhiệt độ 0, đoạn lặp dễ bị bỏ trống.
          </span>
        </label>
      </div>

      {notice && <p className="mt-4 rounded-xl bg-red-950/60 px-3 py-2 text-sm text-red-100">{notice}</p>}

      <div className="mt-5 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onStart}
          className="rounded-2xl bg-[var(--gold)] px-4 py-2.5 text-sm font-semibold text-[#1a1206]"
        >
          Bắt đầu ghép chữ
        </button>
        <button
          type="button"
          onClick={onBack}
          className="rounded-2xl border border-[var(--line)] px-4 py-2.5 text-sm text-[var(--muted)]"
        >
          Quay lại
        </button>
      </div>
    </section>
  );
}
