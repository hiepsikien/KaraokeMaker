"use client";

import type { ProjectFields } from "@/lib/types";
import { inputClass } from "@/components/ui/field";

const fieldClass = inputClass;

const MODELS: { value: string; label: string }[] = [
  { value: "tiny", label: "Tiny, nhanh nhất" },
  { value: "base", label: "Base" },
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
  { value: "large-v2", label: "Large v2" },
  { value: "large", label: "Large (bản v3)" },
];

const GEMINI_MODELS: { value: string; label: string }[] = [
  { value: "gemini-3.8-flash", label: "Gemini 3.8 Flash" },
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
  const gemini = fields.align_strategy === "gemini_lines";
  return (
    <section className="mx-auto max-w-2xl rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-6">
      <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Ghép chữ</p>
      <h2 className="mt-2 text-xl font-semibold">Chọn chiến lược căn lời</h2>
      <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
        Gemini line-level chia câu và lấy mốc câu; Whisper căn từng từ trong câu. Nếu Gemini lỗi hoặc
        bạn chưa đồng ý gửi audio, app tự quay lại Whisper.
      </p>
      {replacesEdits && (
        <p className="mt-3 rounded-xl bg-amber-950/50 px-3 py-2 text-sm text-amber-100">
          Ghép lại sẽ thay toàn bộ timing đang sửa, kể cả chỗ đã kéo tay.
        </p>
      )}

      <div className="mt-5 grid gap-4 sm:grid-cols-2">
        <label className="block sm:col-span-2">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Chiến lược</span>
          <select
            className={fieldClass}
            value={fields.align_strategy}
            onChange={(event) =>
              setFields((current) => ({
                ...current,
                align_strategy: event.target.value === "gemini_lines" ? "gemini_lines" : "whisper",
              }))
            }
          >
            <option value="gemini_lines">Gemini line-level + Whisper word-level (mặc định)</option>
            <option value="whisper">Whisper (local, dự phòng)</option>
          </select>
        </label>

        {gemini ? (
          <>
            <p className="text-sm leading-6 text-[var(--muted)] sm:col-span-2">
              Gemini chia câu và lấy mốc câu; Whisper căn từng từ. Cần bật đồng ý gửi audio bên dưới.
            </p>
            <label className="block sm:col-span-2">
              <span className="mb-1.5 block text-sm text-[var(--muted)]">Model Gemini</span>
              <select
                className={fieldClass}
                value={fields.gemini_model}
                onChange={(event) =>
                  setFields((current) => ({ ...current, gemini_model: event.target.value }))
                }
              >
                {GEMINI_MODELS.map((model) => (
                  <option key={model.value} value={model.value}>
                    {model.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-start gap-3 sm:col-span-2">
              <input
                type="checkbox"
                className="mt-1"
                checked={fields.google_audio_consent}
                onChange={(event) =>
                  setFields((current) => ({
                    ...current,
                    google_audio_consent: event.target.checked,
                  }))
                }
              />
              <span className="text-sm leading-6">
                Tôi đồng ý gửi file vocal và lời bài hát lên Google Gemini để chia câu. Nếu tắt, app dùng Whisper.
              </span>
            </label>
            {!fields.google_audio_consent && (
              <p className="rounded-xl bg-amber-950/50 px-3 py-2 text-sm text-amber-100 sm:col-span-2">
                Chưa bật đồng ý gửi audio: lần ghép này sẽ dùng Whisper.
              </p>
            )}
          </>
        ) : null}

        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Model Whisper</span>
          <select
            className={fieldClass}
            value={fields.model_name}
            onChange={(event) =>
              setFields((current) => ({ ...current, model_name: event.target.value }))
            }
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
            onChange={(event) =>
              setFields((current) => ({ ...current, device: event.target.value }))
            }
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
            onChange={(event) =>
              setFields((current) => ({ ...current, no_speech_threshold: event.target.value }))
            }
          />
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
