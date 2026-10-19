"use client";

import { useEffect, useState } from "react";

import type { Job, RestoreSettings } from "@/lib/types";

const fieldClass =
  "w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm outline-none transition focus:border-[var(--gold)]";

function settingsFrom(restore: RestoreSettings | null | undefined): RestoreSettings {
  return {
    instrumental_stem: restore?.instrumental_stem || "",
    high_hz: restore?.high_hz ?? 8000,
    low_hz: restore?.low_hz ?? 120,
    high_volume: restore?.high_volume ?? 50,
    low_volume: restore?.low_volume ?? 50,
  };
}

export function AudioRestore({
  job,
  notice,
  onJob,
  onSeparate,
  onPreview,
  onExport,
  onAlign,
  onBack,
}: {
  job: Job;
  notice: string;
  onJob: (job: Job) => void;
  onSeparate: () => void;
  onPreview: (settings: RestoreSettings) => Promise<boolean>;
  onExport: (settings: RestoreSettings, format: string) => Promise<boolean>;
  onAlign: () => void;
  onBack: () => void;
}) {
  const [settings, setSettings] = useState<RestoreSettings>(() => settingsFrom(job.restore));
  const [format, setFormat] = useState("wav");
  const [previewToken, setPreviewToken] = useState(0);
  const [busy, setBusy] = useState("");
  const separating = job.status === "separating";
  const stemReady = Boolean(job.restore?.instrumental_stem || settings.instrumental_stem);
  const canAlign = Boolean(job.project?.vocal_audio && job.project?.instrumental_audio);

  useEffect(() => {
    const stem = job.restore?.instrumental_stem || "";
    setSettings((current) =>
      current.instrumental_stem === stem ? current : { ...current, instrumental_stem: stem },
    );
  }, [job.restore?.instrumental_stem]);

  useEffect(() => {
    if (job.status !== "separating") return;
    const timer = setInterval(async () => {
      const response = await fetch(`/api/jobs/${job.id}`);
      if (!response.ok) return;
      onJob((await response.json()) as Job);
    }, 1000);
    return () => clearInterval(timer);
  }, [job.id, job.status, onJob]);

  function update(key: keyof RestoreSettings, value: string) {
    const numeric = Number(value);
    setSettings((current) => ({ ...current, [key]: Number.isFinite(numeric) ? numeric : current[key] }));
  }

  async function preview() {
    setBusy("preview");
    const ok = await onPreview(settings);
    if (ok) setPreviewToken(Date.now());
    setBusy("");
  }

  async function exportMix() {
    setBusy("export");
    await onExport(settings, format);
    setBusy("");
  }

  return (
    <section className="mx-auto max-w-3xl space-y-5 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Tách và trộn</p>
          <h2 className="mt-1 text-xl font-semibold">Khôi phục dải tần cho nhạc nền</h2>
        </div>
        <button type="button" onClick={onBack} className="text-sm text-[var(--muted)] hover:text-white">
          Quay lại khai báo
        </button>
      </div>

      <p className="text-sm leading-6 text-[var(--muted)]">
        Kim Vocal 2 tách vocal và instrumental (overlap 25%, segment 512). Bản nhạc nền là instrumental cộng thêm
        dải cao và dải thấp của file gốc. Vocal không được trộn vào.
      </p>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={separating || busy !== ""}
          onClick={onSeparate}
          className="rounded-2xl bg-[var(--gold)] px-4 py-2 text-sm font-semibold text-[#1a1206] disabled:opacity-60"
        >
          {separating ? "Đang tách…" : stemReady ? "Tách lại" : "Tách"}
        </button>
        {canAlign && (
          <button
            type="button"
            disabled={separating}
            onClick={onAlign}
            className="rounded-2xl border border-[var(--gold)] px-4 py-2 text-sm text-[var(--gold)] disabled:opacity-60"
          >
            Bỏ qua, căn lời
          </button>
        )}
      </div>

      <ol className="max-h-40 space-y-1 overflow-auto text-sm leading-6 text-[var(--muted)]">
        {job.log.map((line, index) => (
          <li key={`${index}-${line}`}>{line}</li>
        ))}
      </ol>
      {job.status === "error" && <p className="text-sm text-red-200">{job.error}</p>}

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Cutoff cao (Hz)</span>
          <input
            className={fieldClass}
            type="number"
            min={2000}
            max={16000}
            value={settings.high_hz}
            onChange={(event) => update("high_hz", event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Volume dải cao (%)</span>
          <input
            className={fieldClass}
            type="number"
            min={0}
            max={100}
            value={settings.high_volume}
            onChange={(event) => update("high_volume", event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Cutoff thấp (Hz)</span>
          <input
            className={fieldClass}
            type="number"
            min={20}
            max={500}
            value={settings.low_hz}
            onChange={(event) => update("low_hz", event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Volume dải thấp (%)</span>
          <input
            className={fieldClass}
            type="number"
            min={0}
            max={100}
            value={settings.low_volume}
            onChange={(event) => update("low_volume", event.target.value)}
          />
        </label>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <button
          type="button"
          disabled={!stemReady || separating || busy !== ""}
          onClick={preview}
          className="rounded-2xl border border-[var(--line)] px-4 py-2 text-sm hover:border-[var(--gold)] disabled:opacity-60"
        >
          {busy === "preview" ? "Đang dựng…" : "Nghe thử"}
        </button>
        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Định dạng xuất</span>
          <select className={fieldClass} value={format} onChange={(event) => setFormat(event.target.value)}>
            <option value="wav">WAV</option>
            <option value="flac">FLAC</option>
            <option value="mp3">MP3</option>
          </select>
        </label>
        <button
          type="button"
          disabled={!stemReady || separating || busy !== ""}
          onClick={exportMix}
          className="rounded-2xl bg-[var(--gold)] px-4 py-2 text-sm font-semibold text-[#1a1206] disabled:opacity-60"
        >
          {busy === "export" ? "Đang xuất…" : "Xuất nhạc nền"}
        </button>
      </div>

      {previewToken > 0 && (
        <audio key={previewToken} className="w-full" controls autoPlay src={`/api/restore/${job.id}/preview?v=${previewToken}`} />
      )}

      {canAlign && (
        <button
          type="button"
          disabled={separating}
          onClick={onAlign}
          className="w-full rounded-2xl border border-[var(--gold)] px-4 py-3 text-sm font-semibold text-[var(--gold)] disabled:opacity-60"
        >
          Căn lời và vào biên tập
        </button>
      )}

      {notice && <p className="rounded-xl bg-red-950/60 px-3 py-2 text-sm text-red-100">{notice}</p>}
    </section>
  );
}
