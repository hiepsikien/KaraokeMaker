"use client";

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { cueAt, formatSeconds, wordFill, type TimedLine, type TimedWord } from "@/lib/cue";

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
    <p className="flex flex-wrap justify-center gap-x-3 gap-y-1 text-3xl font-semibold tracking-wide sm:text-4xl">
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

function WordTrack({
  line,
  locked,
  onChange,
  onSeek,
}: {
  line: TimedLine;
  locked: boolean;
  onChange: (words: TimedWord[]) => void;
  onSeek: (time: number) => void;
}) {
  const trackRef = useRef<HTMLDivElement>(null);
  const first = line.words[0]?.start ?? line.start;
  const last = line.words[line.words.length - 1]?.end ?? line.end;
  const windowStart = Math.max(0, first - 1.2);
  const windowEnd = Math.max(windowStart + 6, last + 1.2);
  const span = windowEnd - windowStart;

  function beginDrag(event: ReactPointerEvent<HTMLButtonElement>, index: number) {
    if (locked) return;
    const track = trackRef.current;
    if (!track) return;
    const width = track.getBoundingClientRect().width || 1;
    const origin = event.clientX;
    const duration = Math.max(0.08, line.words[index].end - line.words[index].start);
    const start0 = line.words[index].start;
    let words = line.words.map((word) => ({ ...word }));
    let dragged = false;
    event.currentTarget.setPointerCapture(event.pointerId);
    const move = (ev: PointerEvent) => {
      const delta = ev.clientX - origin;
      if (Math.abs(delta) > 4) dragged = true;
      const nextStart = Math.max(0, start0 + (delta / width) * span);
      words = words.map((word, wordIndex) =>
        wordIndex === index ? { ...word, start: nextStart, end: nextStart + duration } : word,
      );
      onChange(words);
    };
    const finish = (ev: PointerEvent) => {
      const target = ev.currentTarget as HTMLButtonElement;
      if (target.hasPointerCapture(ev.pointerId)) target.releasePointerCapture(ev.pointerId);
      target.onpointermove = null;
      target.onpointerup = null;
      target.onpointercancel = null;
      if (dragged) onChange([...words].sort((a, b) => a.start - b.start));
      (target as HTMLButtonElement & { dragged?: boolean }).dragged = dragged;
    };
    event.currentTarget.onpointermove = move;
    event.currentTarget.onpointerup = finish;
    event.currentTarget.onpointercancel = finish;
  }

  return (
    <div ref={trackRef} className="relative h-14 rounded-xl bg-black/35">
      {line.words.map((word, index) => {
        const left = ((word.start - windowStart) / span) * 100;
        const width = Math.max(6, ((word.end - word.start) / span) * 100);
        return (
          <button
            key={index}
            type="button"
            disabled={locked}
            title="Kéo để đổi lúc chữ xuất hiện"
            onClick={(event) => {
              const dragged = (event.currentTarget as HTMLButtonElement & { dragged?: boolean }).dragged;
              if (dragged) {
                (event.currentTarget as HTMLButtonElement & { dragged?: boolean }).dragged = false;
                return;
              }
              onSeek(Math.max(0, word.start - 0.3));
            }}
            onPointerDown={(event) => beginDrag(event, index)}
            className="absolute top-2 h-10 cursor-ew-resize overflow-hidden rounded-lg border border-[var(--gold)]/50 bg-[var(--gold)]/20 px-2 text-left text-xs text-[var(--paper)] disabled:cursor-default"
            style={{ left: `${left}%`, width: `${width}%`, minWidth: "2.5rem" }}
          >
            <span className="block truncate font-medium">{word.text}</span>
            <span className="block text-[10px] text-[var(--muted)]">{formatSeconds(word.start)}</span>
          </button>
        );
      })}
    </div>
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
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [lines, setLines] = useState(() => cloneLines(initialLines));
  const [time, setTime] = useState(0);
  const cue = cueAt(lines, time);

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
      setLines((current) => current.filter((_, index) => index !== lineIndex));
      return;
    }
    replaceLine(lineIndex, syncLine({ ...line, words }));
  }

  function seek(next: number) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = next;
    setTime(next);
  }

  const fileName = (path: string) => path.split(/[/\\]/).pop() || path;

  return (
    <div className="space-y-5">
      <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5 sm:p-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Sửa lời</p>
            <h2 className="mt-1 text-2xl font-semibold">Chỉnh chữ và lúc từng chữ hiện ra</h2>
          </div>
          <button type="button" onClick={onBack} className="text-sm text-[var(--muted)] hover:text-white">
            Quay lại phần chọn file
          </button>
        </div>
        <div className="flex min-h-40 flex-col items-center justify-center rounded-2xl bg-black/45 px-6 py-8">
          {cue.mode === "countdown" && (
            <p className="mb-3 text-5xl font-semibold text-[var(--gold)]">{cue.number}</p>
          )}
          {cue.line ? (
            <LyricLine line={cue.line} time={time} preview={cue.mode === "countdown"} />
          ) : (
            <p className="text-sm text-[var(--muted)]">Đoạn nhạc, chưa tới câu hát</p>
          )}
        </div>
        <audio ref={audioRef} className="mt-4 w-full" controls src={`http://127.0.0.1:8765/api/jobs/${jobId}/audio`} />
        <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
          Nghe vocal để đối chiếu. Kéo ô chữ trên thước thời gian để đổi lúc chữ được tô vàng. Câu sau một đoạn nhạc từ 8 giây sẽ hiện trước kèm số đếm 5, 4, 3, 2, 1.
        </p>
      </section>

      <section className="space-y-4">
        {lines.map((line, lineIndex) => (
          <article key={lineIndex} className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-4 sm:p-5">
            <div className="mb-3 flex items-center justify-between gap-3 text-xs text-[var(--muted)]">
              <span>
                Câu {lineIndex + 1} · {formatSeconds(line.start)} – {formatSeconds(line.end)}
              </span>
              <button type="button" disabled={locked} onClick={() => seek(Math.max(0, line.start - 0.4))} className="text-[var(--gold)] disabled:opacity-40">
                Nghe câu này
              </button>
            </div>
            <WordTrack
              line={line}
              locked={locked}
              onSeek={seek}
              onChange={(words) =>
                replaceLine(lineIndex, {
                  ...line,
                  words,
                  text: words.map((word) => word.text).join(" "),
                  start: Math.min(...words.map((word) => word.start)),
                  end: Math.max(...words.map((word) => word.end)),
                })
              }
            />
            <div className="mt-3 flex flex-wrap gap-2">
              {line.words.map((word, wordIndex) => (
                <label key={`${lineIndex}-${wordIndex}`} className="flex items-center gap-1 rounded-xl border border-[var(--line)] bg-black/20 px-2 py-1">
                  <input
                    value={word.text}
                    disabled={locked}
                    onChange={(event) => changeWord(lineIndex, wordIndex, event.target.value)}
                    className="w-24 bg-transparent text-sm outline-none"
                  />
                  <button type="button" disabled={locked} className="text-xs text-[var(--muted)] hover:text-white disabled:opacity-40" onClick={() => removeWord(lineIndex, wordIndex)}>
                    ×
                  </button>
                </label>
              ))}
              <button type="button" disabled={locked} onClick={() => addWord(lineIndex)} className="rounded-xl border border-dashed border-[var(--line)] px-3 text-sm text-[var(--muted)] disabled:opacity-40">
                Thêm chữ
              </button>
            </div>
          </article>
        ))}
      </section>

      {error && <p className="rounded-xl bg-red-950/60 px-3 py-2 text-sm text-red-100">{error}</p>}

      <div className="flex flex-wrap items-center gap-3">
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
  );
}
