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

export function TimingEditor({
  jobId,
  initialLines,
  locked,
  error,
  log,
  outputs,
  onRender,
  onReveal,
  onBack,
  keepTiming = false,
}: {
  jobId: string;
  initialLines: TimedLine[];
  locked: boolean;
  error: string;
  log: string[];
  outputs: { vocal: string; instrumental: string; directory: string } | null;
  onRender: (lines: TimedLine[]) => void;
  onReveal: (path: string) => void;
  onBack: () => void;
  keepTiming?: boolean;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const dirty = useRef(false);
  const [lines, setLines] = useState(() => cloneLines(initialLines));
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selectedLine, setSelectedLine] = useState<number | null>(null);
  const [selectedWord, setSelectedWord] = useState<number | null>(null);
  const [savedPath, setSavedPath] = useState("");
  const [saveError, setSaveError] = useState("");
  const cue = cueAt(lines, time);

  useEffect(() => {
    if (keepTiming) return;
    let cancelled = false;
    fetch(`/api/jobs/${jobId}/fit`, { method: "POST" })
      .then(async (response) => {
        if (!response.ok) return null;
        return response.json();
      })
      .then((body: { lines?: TimedLine[] } | null) => {
        if (cancelled || dirty.current || !body?.lines?.length) return;
        setLines(cloneLines(body.lines));
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [jobId, keepTiming]);

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
    dirty.current = true;
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
    const line = lines[lineIndex];
    const words = line.words.filter((_, index) => index !== wordIndex);
    if (words.length === 0) {
      dirty.current = true;
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
    dirty.current = true;
    setLines((current) => {
      const next = [...current];
      next.splice(index + 1, 0, line);
      return next;
    });
    setSelectedLine(index + 1);
    setSelectedWord(0);
  }

  function splitLine() {
    if (selectedLine == null || selectedWord == null || selectedWord <= 0) return;
    const line = lines[selectedLine];
    if (!line || selectedWord >= line.words.length) return;
    const head = syncLine({ ...line, words: line.words.slice(0, selectedWord) });
    const tail = syncLine({ ...line, words: line.words.slice(selectedWord) });
    dirty.current = true;
    setLines((current) => {
      const next = [...current];
      next.splice(selectedLine, 1, head, tail);
      return next;
    });
    setSelectedLine(selectedLine + 1);
    setSelectedWord(0);
  }

  async function saveTiming() {
    setSaveError("");
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
    } catch {
      setSaveError("Chưa lưu được. Server Python chưa chạy.");
    }
  }

  const fileName = (path: string) => path.split(/[/\\]/).pop() || path;

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
      canSplit={selectedLine != null && selectedWord != null && selectedWord > 0}
      onSeek={seek}
      onSelectLine={chooseLine}
      onSelectWord={setSelectedWord}
      onChangeLine={replaceLine}
      onTogglePlay={togglePlay}
      onPlayFrom={playFrom}
      onAddLine={addLine}
      onSplitLine={splitLine}
    />
    <div className="lg:grid lg:grid-cols-[minmax(300px,0.85fr)_minmax(0,1.15fr)] lg:items-start lg:gap-6">
      <section className="sticky top-0 z-10 mb-5 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-4 sm:p-5 lg:top-4 lg:mb-0">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Sửa lời</p>
            <h2 className="mt-1 text-xl font-semibold">Chỉnh chữ và lúc từng chữ hiện ra</h2>
          </div>
          <button type="button" onClick={onBack} className="text-sm text-[var(--muted)] hover:text-white">
            Quay lại phần chọn file
          </button>
        </div>
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
            {line.uncertain && (
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
                disabled={locked || selectedLine !== lineIndex || selectedWord == null || selectedWord <= 0}
                onClick={(event) => {
                  event.stopPropagation();
                  splitLine();
                }}
                className="rounded-xl border border-dashed border-[var(--line)] px-3 text-sm text-[var(--muted)] disabled:opacity-40"
              >
                Tách từ chữ này
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

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={locked || lines.length === 0}
          onClick={saveTiming}
          className="rounded-2xl border border-[var(--gold)] px-5 py-3 text-sm font-semibold text-[var(--gold)] disabled:opacity-60"
        >
          Lưu bản sửa
        </button>
        <button
          type="button"
          disabled={locked || lines.length === 0}
          onClick={() => onRender(lines)}
          className="rounded-2xl bg-[var(--gold)] px-5 py-3 text-sm font-semibold text-[#1a1206] disabled:opacity-60"
        >
          {locked ? "Đang xuất video…" : "Xuất hai video"}
        </button>
        {outputs && (
          <>
            <button type="button" className="text-sm hover:text-[var(--gold)]" onClick={() => onReveal(outputs.vocal)}>
              {fileName(outputs.vocal)}
            </button>
            <button type="button" className="text-sm hover:text-[var(--gold)]" onClick={() => onReveal(outputs.instrumental)}>
              {fileName(outputs.instrumental)}
            </button>
            <button type="button" className="text-sm text-[var(--gold)]" onClick={() => onReveal(outputs.directory)}>
              Mở thư mục
            </button>
          </>
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
