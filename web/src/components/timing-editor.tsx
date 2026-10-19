"use client";

import { useEffect, useRef, useState } from "react";

import { AlignTimeline } from "@/components/align-timeline";
import { cueAt, formatSeconds, wordFill, type TimedLine } from "@/lib/cue";

function cloneLines(lines: TimedLine[]) {
  return lines.map((line) => ({
    ...line,
    words: line.words.map((word) => ({ ...word })),
  }));
}

function syncLine(line: TimedLine): TimedLine {
  const words = [...line.words].sort((a, b) => a.start - b.start);
  if (words.length === 0) {
    return { ...line, words, text: "", start: 0, end: 0 };
  }
  return {
    ...line,
    words,
    text: words.map((word) => word.text).join(" "),
    start: words[0].start,
    end: words[words.length - 1].end,
  };
}

function LyricLine({ line, time, preview }: { line: TimedLine; time: number; preview: boolean }) {
  return (
    <p className="flex flex-wrap justify-center gap-x-2 gap-y-1 text-lg font-semibold tracking-wide sm:text-xl">
      {line.words.map((word, index) => {
        const fill = preview ? 0 : wordFill(word, time);
        return (
          <span key={`${word.start}-${index}`} className="relative inline-block">
            <span className="text-white/90">{word.text}</span>
            <span className="absolute inset-0 overflow-hidden text-[var(--gold)]" style={{ width: `${fill * 100}%` }}>
              {word.text}
            </span>
          </span>
        );
      })}
    </p>
  );
}

export type ExportOptions = {
  output_dir: string;
  format: "mp4" | "mov" | "mkv";
};

export function TimingEditor({
  jobId,
  initialLines,
  locked,
  exported = false,
  error,
  log,
  outputs,
  defaultOutputDir = "",
  onRender,
  onReveal,
  onResumeEditing,
  onBrowseOutput,
  onBack,
  onRealign,
  onFillGaps,
  exportGaps = [],
}: {
  jobId: string;
  initialLines: TimedLine[];
  locked: boolean;
  exported?: boolean;
  error: string;
  log: string[];
  outputs: { vocal: string; instrumental: string; directory: string } | null;
  defaultOutputDir?: string;
  onRender: (lines: TimedLine[], options: ExportOptions) => void;
  onReveal: (path: string) => void;
  onResumeEditing?: () => void;
  onBrowseOutput?: () => Promise<string | null>;
  onBack: () => void;
  onRealign?: () => void;
  onFillGaps?: () => void;
  exportGaps?: string[];
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [lines, setLines] = useState(() => cloneLines(initialLines));
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selectedLine, setSelectedLine] = useState<number | null>(null);
  const [selectedWord, setSelectedWord] = useState<number | null>(null);
  const [savedPath, setSavedPath] = useState("");
  const [saveError, setSaveError] = useState("");
  const [dirty, setDirty] = useState(false);
  const [exportDir, setExportDir] = useState(defaultOutputDir);
  const [exportFormat, setExportFormat] = useState<ExportOptions["format"]>("mp4");
  const [browsingOutput, setBrowsingOutput] = useState(false);
  const cue = cueAt(lines, time);

  useEffect(() => {
    if (defaultOutputDir) setExportDir((current) => current || defaultOutputDir);
  }, [defaultOutputDir]);

  useEffect(() => {
    if (outputs?.directory) setExportDir(outputs.directory);
  }, [outputs?.directory]);

  function markDirty() {
    if (!dirty) setDirty(true);
    if (exported) onResumeEditing?.();
  }

  useEffect(() => {
    let frame = 0;
    const tick = () => {
      const audio = audioRef.current;
      if (audio) setTime(audio.currentTime || 0);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, []);

  function replaceLine(index: number, line: TimedLine) {
    markDirty();
    setLines((current) => current.map((item, itemIndex) => (itemIndex === index ? line : item)));
  }

  function changeWord(lineIndex: number, wordIndex: number, text: string) {
    const line = lines[lineIndex];
    const words = line.words.map((word, index) => (index === wordIndex ? { ...word, text } : word));
    replaceLine(lineIndex, syncLine({ ...line, words }));
  }

  function addWord(lineIndex: number) {
    const line = lines[lineIndex];
    const last = line.words[line.words.length - 1];
    const start = last ? last.end + 0.06 : line.start;
    const words = [...line.words, { text: "chữ", start, end: start + 0.4 }];
    replaceLine(lineIndex, syncLine({ ...line, words }));
  }

  function removeWord(lineIndex: number, wordIndex: number) {
    markDirty();
    const line = lines[lineIndex];
    const words = line.words.filter((_, index) => index !== wordIndex);
    if (words.length === 0) {
      setLines((current) => current.filter((_, index) => index !== lineIndex));
      return;
    }
    replaceLine(lineIndex, syncLine({ ...line, words }));
  }

  useEffect(() => {
    if (selectedLine == null) return;
    document.getElementById(`karaoke-line-${selectedLine}`)?.scrollIntoView({ block: "nearest" });
  }, [selectedLine]);

  function seek(next: number) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = next;
    setTime(next);
  }

  function playFrom(next: number) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = Math.max(0, next);
    setTime(audio.currentTime);
    audio.play().catch(() => undefined);
  }

  function togglePlay() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) audio.play().catch(() => undefined);
    else audio.pause();
  }

  function chooseLine(index: number | null) {
    setSelectedLine(index);
    setSelectedWord(null);
  }

  function addLine() {
    markDirty();
    const index = selectedLine ?? Math.max(0, lines.length - 1);
    const previousEnd = lines[index]?.end ?? 0;
    const nextStart = index + 1 < lines.length ? lines[index + 1].start : Number.POSITIVE_INFINITY;
    let start = time;
    if (!(start >= previousEnd + 0.04 && start + 0.45 < nextStart)) start = previousEnd + 0.08;
    if (start + 0.2 >= nextStart) start = Math.max(previousEnd + 0.02, nextStart - 0.5);
    const end = Math.max(start + 0.12, Math.min(start + 0.4, nextStart - 0.02));
    const line = syncLine({
      text: "chữ",
      start,
      end,
      words: [{ text: "chữ", start, end }],
    });
    setLines((current) => {
      const next = [...current];
      next.splice(index + 1, 0, line);
      return next;
    });
    setSelectedLine(index + 1);
    setSelectedWord(0);
  }

  function splitWordIndex(lineIndex: number) {
    const line = lines[lineIndex];
    if (!line || line.words.length < 2) return null;
    if (selectedLine === lineIndex && selectedWord != null) {
      return selectedWord > 0 && selectedWord < line.words.length ? selectedWord : null;
    }
    if (time <= line.words[0].start || time >= line.words[line.words.length - 1].end) return null;
    const index = line.words.findIndex((word) => (word.start < time && word.end > time) || word.start >= time);
    return index > 0 ? index : null;
  }

  function splitLine(lineIndex = selectedLine ?? lines.findIndex((line) => time >= line.start && time < line.end)) {
    if (lineIndex == null || lineIndex < 0) return;
    const wordIndex = splitWordIndex(lineIndex);
    const line = lines[lineIndex];
    if (wordIndex == null || !line) return;
    markDirty();
    const head = syncLine({ ...line, words: line.words.slice(0, wordIndex) });
    const tail = syncLine({ ...line, words: line.words.slice(wordIndex) });
    setLines((current) => {
      const next = [...current];
      next.splice(lineIndex, 1, head, tail);
      return next;
    });
    setSelectedLine(lineIndex + 1);
    setSelectedWord(0);
  }

  async function saveTiming() {
    setSaveError("");
    onResumeEditing?.();
    try {
      const response = await fetch(`/api/jobs/${jobId}/timing`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lines }),
      });
      if (!response.ok) {
        setSaveError("Chưa lưu được. Hãy khởi động lại server Python rồi thử lại.");
        return;
      }
      const body = await response.json();
      setSavedPath(body.path || "");
      setDirty(false);
    } catch {
      setSaveError("Chưa lưu được. Server Python chưa chạy.");
    }
  }

  const fileName = (path: string) => path.split(/[/\\]/).pop() || path;

  async function pickExportDir() {
    if (!onBrowseOutput) return;
    setBrowsingOutput(true);
    try {
      const picked = await onBrowseOutput();
      if (picked) setExportDir(picked);
    } finally {
      setBrowsingOutput(false);
    }
  }

  function startExport() {
    if (!exportDir.trim()) {
      setSaveError("Hãy chọn thư mục xuất trước khi xuất video.");
      return;
    }
    setSaveError("");
    onRender(lines, { output_dir: exportDir.trim(), format: exportFormat });
  }

  return (
    <div className="space-y-5">
    <AlignTimeline
      jobId={jobId}
      lines={lines}
      time={time}
      duration={duration}
      playing={playing}
      locked={locked}
      selectedLine={selectedLine}
      selectedWord={selectedWord}
      canSplit={splitWordIndex(selectedLine ?? lines.findIndex((line) => time >= line.start && time < line.end)) != null}
      onSeek={seek}
      onSelectLine={chooseLine}
      onSelectWord={setSelectedWord}
      onChangeLine={replaceLine}
      onTogglePlay={togglePlay}
      onPlayFrom={playFrom}
      onAddLine={addLine}
      onSplitLine={() => splitLine()}
    />
    <div className="lg:grid lg:grid-cols-[minmax(300px,0.85fr)_minmax(0,1.15fr)] lg:items-start lg:gap-6">
      <section className="sticky top-0 z-10 mb-5 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-4 sm:p-5 lg:top-4 lg:mb-0">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Biên tập</p>
            <h2 className="mt-1 text-xl font-semibold">Chỉnh chữ và lúc từng chữ hiện ra</h2>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {onRealign && (
              <button
                type="button"
                disabled={locked}
                onClick={onRealign}
                className="text-sm text-[var(--gold)] disabled:opacity-40"
              >
                Ghép chữ lại
              </button>
            )}
            <button type="button" onClick={onBack} className="text-sm text-[var(--muted)] hover:text-white">
              Khai báo dự án
            </button>
          </div>
        </div>
        {(exported || outputs) && (
          <p className="mb-4 rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2 text-sm leading-6 text-[var(--muted)]">
            {exported && !dirty
              ? "Đã xuất video. Kéo sửa timing bên dưới — nút Lưu dự án luôn dùng được; xong thì Xuất lại."
              : dirty
                ? "Đang sửa sau khi xuất. Hãy Lưu dự án, rồi Xuất lại nếu cần video mới."
                : "Có bản video đã xuất. Bạn vẫn có thể sửa timing và Lưu dự án bất cứ lúc nào."}
          </p>
        )}
        <div className="flex min-h-28 flex-col items-center justify-center rounded-2xl bg-black/45 px-4 py-5">
          {cue.mode === "countdown" && (
            <p className="mb-2 text-3xl font-semibold text-[var(--gold)]">{cue.number}</p>
          )}
          {cue.line ? (
            <LyricLine line={cue.line} time={time} preview={cue.mode === "countdown"} />
          ) : (
            <p className="text-sm text-[var(--muted)]">Đoạn nhạc, chưa tới câu hát</p>
          )}
        </div>
        <audio
          ref={audioRef}
          className="mt-4 w-full"
          controls
          src={`/api/jobs/${jobId}/audio`}
          onLoadedMetadata={(event) => setDuration(event.currentTarget.duration || 0)}
          onPlay={() => setPlaying(true)}
          onPause={() => setPlaying(false)}
        />
        <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
          Sóng âm ở trên, chữ ở dưới. Cuộn để kéo ngang. Giữ ⌘ rồi cuộn để phóng to, giống Audacity. Câu sau hiện ngay khi câu trước ngân xong.
        </p>
      </section>

      <div className="min-w-0 space-y-3 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto lg:pr-1">
      <section className="space-y-3">
        {lines.map((line, lineIndex) => (
          <article
            id={`karaoke-line-${lineIndex}`}
            key={lineIndex}
            onClick={() => chooseLine(lineIndex)}
            className={`cursor-pointer rounded-3xl border bg-[var(--panel)] p-4 sm:p-5 ${selectedLine === lineIndex ? "border-[var(--gold)]" : "border-[var(--line)]"}`}
          >
            {line.supplemented && (
              <p className="mb-3 rounded-xl bg-sky-950/50 px-3 py-2 text-xs leading-5 text-sky-100">
                Whisper nghe được câu này, nhưng lời bạn nhập chưa có. Sửa chữ nếu đó là hát bè hoặc đoạn kết.
              </p>
            )}
            {line.uncertain && !line.supplemented && (
              <p className="mb-3 rounded-xl bg-amber-950/50 px-3 py-2 text-xs leading-5 text-amber-100">
                Câu này được ghép ước lượng. Hãy nghe và kéo lại từng chữ.
              </p>
            )}
            <div className="mb-3 flex items-center justify-between gap-3 text-xs text-[var(--muted)]">
              <span>
                Câu {lineIndex + 1} · {formatSeconds(line.start)} – {formatSeconds(line.end)}
              </span>
              <button
                type="button"
                disabled={locked}
                onClick={(event) => {
                  event.stopPropagation();
                  chooseLine(lineIndex);
                  playFrom(line.start);
                }}
                className="text-[var(--gold)] disabled:opacity-40"
              >
                Nghe câu này
              </button>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              {line.words.map((word, wordIndex) => (
                <label
                  key={`${lineIndex}-${wordIndex}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    setSelectedLine(lineIndex);
                    setSelectedWord(wordIndex);
                  }}
                  className={`flex items-center gap-1 rounded-xl border bg-black/20 px-2 py-1 ${selectedLine === lineIndex && selectedWord === wordIndex ? "border-[var(--gold)]" : "border-[var(--line)]"}`}
                >
                  <input
                    value={word.text}
                    disabled={locked}
                    onChange={(event) => changeWord(lineIndex, wordIndex, event.target.value)}
                    className="w-20 bg-transparent text-xs outline-none"
                  />
                  <button type="button" disabled={locked} className="text-xs text-[var(--muted)] hover:text-white disabled:opacity-40" onClick={() => removeWord(lineIndex, wordIndex)}>
                    ×
                  </button>
                </label>
              ))}
              <button type="button" disabled={locked} onClick={(event) => { event.stopPropagation(); setSelectedLine(lineIndex); addWord(lineIndex); }} className="rounded-xl border border-dashed border-[var(--line)] px-3 text-sm text-[var(--muted)] disabled:opacity-40">
                Thêm chữ
              </button>
              <button
                type="button"
                disabled={locked || splitWordIndex(lineIndex) == null}
                onClick={(event) => {
                  event.stopPropagation();
                  setSelectedLine(lineIndex);
                  splitLine(lineIndex);
                }}
                className="rounded-xl border border-dashed border-[var(--line)] px-3 text-sm text-[var(--muted)] disabled:opacity-40"
              >
                Chia câu
              </button>
            </div>
          </article>
        ))}
      </section>

      {error && <p className="rounded-xl bg-red-950/60 px-3 py-2 text-sm text-red-100">{error}</p>}
      {saveError && <p className="rounded-xl bg-red-950/60 px-3 py-2 text-sm text-red-100">{saveError}</p>}
      {savedPath && (
        <p className="text-sm text-[var(--muted)]">
          Đã lưu{" "}
          <button type="button" className="text-[var(--gold)]" onClick={() => onReveal(savedPath)}>
            {fileName(savedPath)}
          </button>
        </p>
      )}

      <div className="sticky bottom-3 z-20 space-y-3 rounded-3xl border border-[var(--line)] bg-[var(--panel)]/95 p-4 shadow-lg backdrop-blur">
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_140px] sm:items-end">
          <label className="block min-w-0">
            <span className="mb-1.5 block text-xs font-semibold tracking-[0.14em] text-[var(--gold)] uppercase">
              Thư mục xuất
            </span>
            <div className="flex gap-2">
              <input
                readOnly
                value={exportDir}
                placeholder="Chọn nơi lưu hai video"
                className="w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm outline-none"
              />
              <button
                type="button"
                disabled={locked || browsingOutput}
                onClick={pickExportDir}
                className="shrink-0 rounded-xl border border-[var(--line)] px-3 text-sm hover:border-[var(--gold)] disabled:opacity-60"
              >
                {browsingOutput ? "…" : "Chọn"}
              </button>
            </div>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-xs font-semibold tracking-[0.14em] text-[var(--gold)] uppercase">
              Định dạng
            </span>
            <select
              disabled={locked}
              value={exportFormat}
              onChange={(event) => setExportFormat(event.target.value as ExportOptions["format"])}
              className="w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm outline-none disabled:opacity-60"
            >
              <option value="mp4">MP4</option>
              <option value="mov">MOV</option>
              <option value="mkv">MKV</option>
            </select>
          </label>
        </div>
        <p className="text-xs leading-5 text-[var(--muted)]">
          Tên file có hậu tố thời gian (vd. 20261019-161005) nên xuất lại không ghi đè bản trước.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={locked || lines.length === 0}
            onClick={saveTiming}
            className="rounded-2xl border border-[var(--gold)] px-5 py-3 text-sm font-semibold text-[var(--gold)] disabled:opacity-60"
          >
            {locked ? "Đang xuất… (lưu sau)" : "Lưu dự án"}
          </button>
          <button
            type="button"
            disabled={locked || lines.length === 0 || exportGaps.length > 0 || !exportDir.trim()}
            onClick={startExport}
            className="rounded-2xl bg-[var(--gold)] px-5 py-3 text-sm font-semibold text-[#1a1206] disabled:opacity-60"
          >
            {locked ? "Đang xuất video…" : outputs ? "Xuất lại hai video" : "Xuất hai video"}
          </button>
        </div>
        {exportGaps.length > 0 && (
          <p className="text-sm text-[var(--muted)]">
            Chưa xuất được vì còn thiếu {exportGaps.join(", ")}.{" "}
            {onFillGaps ? (
              <button type="button" className="text-[var(--gold)]" onClick={onFillGaps}>
                Bổ sung file trong khai báo
              </button>
            ) : (
              "Hãy bổ sung file trong phần khai báo."
            )}
          </p>
        )}
        {outputs && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-[var(--muted)]">Bản đã xuất:</span>
            <button type="button" className="hover:text-[var(--gold)]" onClick={() => onReveal(outputs.vocal)}>
              {fileName(outputs.vocal)}
            </button>
            <button type="button" className="hover:text-[var(--gold)]" onClick={() => onReveal(outputs.instrumental)}>
              {fileName(outputs.instrumental)}
            </button>
            <button type="button" className="text-[var(--gold)]" onClick={() => onReveal(outputs.directory)}>
              Mở thư mục
            </button>
          </div>
        )}
      </div>

      <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
        <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Tiến trình</h2>
        <ol className="mt-3 max-h-48 space-y-1 overflow-auto text-sm leading-6 text-[var(--muted)]">
          {log.map((line, index) => (
            <li key={`${index}-${line}`}>{line}</li>
          ))}
        </ol>
      </section>
      </div>
    </div>
    </div>
  );
}
