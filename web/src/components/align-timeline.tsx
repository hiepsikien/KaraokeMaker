"use client";

import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { formatSeconds, type TimedLine, type TimedWord } from "@/lib/cue";

type Peaks = { min: Float32Array; max: Float32Array; hop: number };
type Edge = "move" | "start" | "end";

const RULER = 18;
const WAVE = 92;
const LANE = 58;
const MAX_PPS = 720;

function shiftWords(words: TimedWord[], delta: number) {
  return words.map((word) => ({ ...word, start: word.start + delta, end: word.end + delta }));
}

function scaleWords(words: TimedWord[], fromStart: number, fromEnd: number, toStart: number, toEnd: number) {
  const fromSpan = Math.max(0.08, fromEnd - fromStart);
  const toSpan = Math.max(0.08, toEnd - toStart);
  return words.map((word) => ({
    ...word,
    start: toStart + ((word.start - fromStart) / fromSpan) * toSpan,
    end: toStart + ((word.end - fromStart) / fromSpan) * toSpan,
  }));
}

function withWords(line: TimedLine, words: TimedWord[]): TimedLine {
  const sorted = [...words].sort((a, b) => a.start - b.start);
  if (sorted.length === 0) return { ...line, words: sorted, text: "", start: 0, end: 0 };
  return {
    ...line,
    words: sorted,
    text: sorted.map((word) => word.text).join(" "),
    start: sorted[0].start,
    end: sorted[sorted.length - 1].end,
  };
}

function usePeaks(jobId: string) {
  const [peaks, setPeaks] = useState<Peaks | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const context = new AudioContext();
    fetch(`/api/jobs/${jobId}/audio`)
      .then((response) => {
        if (!response.ok) throw new Error("audio");
        return response.arrayBuffer();
      })
      .then((buffer) => context.decodeAudioData(buffer))
      .then((audio) => {
        if (cancelled) return;
        const hopSamples = Math.max(1, Math.floor(audio.sampleRate / 100));
        const left = audio.getChannelData(0);
        const right = audio.numberOfChannels > 1 ? audio.getChannelData(1) : null;
        const buckets = Math.ceil(left.length / hopSamples);
        const min = new Float32Array(buckets);
        const max = new Float32Array(buckets);
        for (let bucket = 0; bucket < buckets; bucket += 1) {
          let lo = 0;
          let hi = 0;
          const from = bucket * hopSamples;
          const to = Math.min(left.length, from + hopSamples);
          for (let index = from; index < to; index += 4) {
            const sample = right ? (left[index] + right[index]) * 0.5 : left[index];
            if (sample < lo) lo = sample;
            if (sample > hi) hi = sample;
          }
          min[bucket] = lo;
          max[bucket] = hi;
        }
        setPeaks({ min, max, hop: hopSamples / audio.sampleRate });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      })
      .finally(() => {
        context.close().catch(() => undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  return { peaks, failed };
}

export function AlignTimeline({
  jobId,
  lines,
  time,
  duration,
  playing,
  locked,
  selectedLine,
  onSeek,
  onSelectLine,
  onChangeLine,
}: {
  jobId: string;
  lines: TimedLine[];
  time: number;
  duration: number;
  playing: boolean;
  locked: boolean;
  selectedLine: number | null;
  onSeek: (time: number) => void;
  onSelectLine: (index: number | null) => void;
  onChangeLine: (index: number, line: TimedLine) => void;
}) {
  const { peaks, failed } = usePeaks(jobId);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [viewport, setViewport] = useState(0);
  const [pps, setPps] = useState(40);
  const fitted = useRef(false);
  const pendingScroll = useRef<number | null>(null);
  const songLength = Math.max(duration, lines[lines.length - 1]?.end ?? 0, 1);
  const minPps = viewport > 0 ? viewport / songLength : 8;
  const trackWidth = Math.max(viewport, songLength * pps);
  const activeIndex = selectedLine != null && lines[selectedLine] ? selectedLine : null;

  useEffect(() => {
    const node = stageRef.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setViewport(node.clientWidth));
    observer.observe(node);
    setViewport(node.clientWidth);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (fitted.current || viewport <= 0) return;
    fitted.current = true;
    setPps(viewport / songLength);
  }, [viewport, songLength]);

  const zoomedFor = useRef<number | null>(null);
  useEffect(() => {
    if (activeIndex == null) {
      zoomedFor.current = null;
      return;
    }
    if (viewport <= 0 || zoomedFor.current === activeIndex) return;
    zoomedFor.current = activeIndex;
    fitLine(activeIndex);
  }, [activeIndex, viewport]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (width <= 0 || height <= 0) return;
    const density = window.devicePixelRatio || 1;
    canvas.width = Math.floor(width * density);
    canvas.height = Math.floor(height * density);
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(density, 0, 0, density, 0, 0);
    context.clearRect(0, 0, width, height);
    if (!peaks) return;
    context.fillStyle = "rgba(228, 177, 90, 0.9)";
    const viewStart = scrollLeft / pps;
    const mid = height / 2;
    for (let x = 0; x < width; x += 1) {
      const from = Math.max(0, Math.floor((viewStart + x / pps) / peaks.hop));
      const to = Math.min(peaks.max.length - 1, Math.ceil((viewStart + (x + 1) / pps) / peaks.hop));
      let lo = 0;
      let hi = 0;
      for (let index = from; index <= to; index += 1) {
        if (peaks.min[index] < lo) lo = peaks.min[index];
        if (peaks.max[index] > hi) hi = peaks.max[index];
      }
      context.fillRect(x, mid + lo * (mid - 4), 1, Math.max(1, (hi - lo) * (mid - 4)));
    }
  }, [peaks, pps, scrollLeft, viewport]);

  useEffect(() => {
    if (pendingScroll.current == null) return;
    moveScroll(pendingScroll.current);
    pendingScroll.current = null;
  }, [pps]);

  useEffect(() => {
    if (!playing || viewport <= 0) return;
    const node = scrollRef.current;
    if (!node) return;
    const viewStart = node.scrollLeft / pps;
    const viewEnd = viewStart + viewport / pps;
    if (time < viewStart + 0.2 || time > viewEnd - 0.2) moveScroll(time * pps - viewport * 0.3);
  }, [playing, time, pps, viewport]);

  useEffect(() => {
    const node = stageRef.current;
    if (!node) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      if (event.ctrlKey || event.metaKey) {
        const rect = node.getBoundingClientRect();
        const anchorX = event.clientX - rect.left;
        const anchorTime = (nodeScroll() + anchorX) / pps;
        const next = Math.min(MAX_PPS, Math.max(minPps, pps * (event.deltaY > 0 ? 0.85 : 1.18)));
        pendingScroll.current = anchorTime * next - anchorX;
        setPps(next);
        return;
      }
      moveScroll(nodeScroll() + event.deltaY + event.deltaX);
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [pps, minPps, viewport]);

  function moveScroll(next: number) {
    const node = scrollRef.current;
    if (!node) return;
    const max = Math.max(0, node.scrollWidth - node.clientWidth);
    node.scrollLeft = Math.min(max, Math.max(0, next));
    setScrollLeft(node.scrollLeft);
  }

  function nodeScroll() {
    return scrollRef.current?.scrollLeft ?? scrollLeft;
  }

  function zoomAt(nextPps: number, anchorTime: number, anchorX: number) {
    const clamped = Math.min(MAX_PPS, Math.max(minPps, nextPps));
    const nextScroll = anchorTime * clamped - anchorX;
    pendingScroll.current = nextScroll;
    if (clamped === pps) {
      moveScroll(nextScroll);
      pendingScroll.current = null;
      return;
    }
    setPps(clamped);
  }

  function zoomBy(factor: number) {
    const anchorX = viewport / 2;
    zoomAt(pps * factor, (nodeScroll() + anchorX) / pps, anchorX);
  }

  function fitSong() {
    if (viewport <= 0) return;
    pendingScroll.current = 0;
    setPps(viewport / songLength);
  }

  function fitLine(index: number) {
    const line = lines[index];
    if (!line || viewport <= 0) return;
    const span = Math.max(1.2, line.end - line.start);
    const next = Math.min(MAX_PPS, Math.max(minPps, viewport / (span * 1.35)));
    pendingScroll.current = Math.max(0, (line.start - span * 0.17) * next);
    setPps(next);
  }

  function beginPan(event: ReactPointerEvent<HTMLDivElement>) {
    if ((event.target as HTMLElement).closest("button")) return;
    const origin = event.clientX;
    const startScroll = scrollRef.current?.scrollLeft ?? 0;
    const rect = stageRef.current?.getBoundingClientRect();
    const anchor = rect ? origin - rect.left : 0;
    let dragged = false;
    event.currentTarget.setPointerCapture(event.pointerId);
    const move = (ev: PointerEvent) => {
      if (Math.abs(ev.clientX - origin) > 3) dragged = true;
      moveScroll(startScroll - (ev.clientX - origin));
    };
    const finish = (ev: PointerEvent) => {
      const target = ev.currentTarget as HTMLDivElement;
      if (target.hasPointerCapture(ev.pointerId)) target.releasePointerCapture(ev.pointerId);
      target.onpointermove = null;
      target.onpointerup = null;
      target.onpointercancel = null;
      if (!dragged) onSeek((startScroll + anchor) / pps);
    };
    event.currentTarget.onpointermove = move;
    event.currentTarget.onpointerup = finish;
    event.currentTarget.onpointercancel = finish;
  }

  function limits(index: number) {
    return {
      previous: index > 0 ? lines[index - 1].end + 0.02 : 0,
      next: index + 1 < lines.length ? lines[index + 1].start - 0.02 : songLength,
    };
  }

  function applyLine(index: number, edge: Edge, delta: number) {
    const line = lines[index];
    if (!line) return;
    const { previous, next } = limits(index);
    const span = Math.max(0.2, line.end - line.start);
    if (edge === "move") {
      const start = Math.min(Math.max(line.start + delta, previous), Math.max(previous, next - span));
      onChangeLine(index, withWords(line, shiftWords(line.words, start - line.start)));
      return;
    }
    if (edge === "start") {
      const start = Math.min(Math.max(line.start + delta, previous), line.end - 0.2);
      onChangeLine(index, withWords(line, scaleWords(line.words, line.start, line.end, start, line.end)));
      return;
    }
    const end = Math.max(Math.min(line.end + delta, next), line.start + 0.2);
    onChangeLine(index, withWords(line, scaleWords(line.words, line.start, line.end, line.start, end)));
  }

  function applyWord(lineIndex: number, wordIndex: number, edge: Edge, delta: number) {
    const line = lines[lineIndex];
    const word = line?.words[wordIndex];
    if (!line || !word) return;
    const { previous, next } = limits(lineIndex);
    const before = wordIndex > 0 ? line.words[wordIndex - 1].end : previous;
    const after = wordIndex + 1 < line.words.length ? line.words[wordIndex + 1].start : next;
    let start = word.start;
    let end = word.end;
    if (edge === "move") {
      const span = Math.max(0.08, word.end - word.start);
      start = Math.min(Math.max(word.start + delta, before), Math.max(before, after - span));
      end = start + span;
    } else if (edge === "start") {
      start = Math.min(Math.max(word.start + delta, before), word.end - 0.08);
    } else {
      end = Math.max(Math.min(word.end + delta, after), word.start + 0.08);
    }
    const words = line.words.map((item, index) => (index === wordIndex ? { ...item, start, end } : item));
    onChangeLine(lineIndex, withWords(line, words));
  }

  function beginDrag(event: ReactPointerEvent<HTMLButtonElement>, apply: (delta: number) => void, onClick: () => void) {
    event.stopPropagation();
    if (locked) return;
    const origin = event.clientX;
    let dragged = false;
    event.currentTarget.setPointerCapture(event.pointerId);
    const move = (ev: PointerEvent) => {
      const delta = (ev.clientX - origin) / pps;
      if (Math.abs(ev.clientX - origin) > 4) dragged = true;
      if (dragged) apply(delta);
    };
    const finish = (ev: PointerEvent) => {
      const target = ev.currentTarget as HTMLButtonElement;
      if (target.hasPointerCapture(ev.pointerId)) target.releasePointerCapture(ev.pointerId);
      target.onpointermove = null;
      target.onpointerup = null;
      target.onpointercancel = null;
      if (!dragged) onClick();
    };
    event.currentTarget.onpointermove = move;
    event.currentTarget.onpointerup = finish;
    event.currentTarget.onpointercancel = finish;
  }

  const tick = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60].find((step) => step * pps >= 80) ?? 60;
  const firstTick = Math.floor(scrollLeft / pps / tick) * tick;
  const ticks: number[] = [];
  for (let mark = firstTick; mark < songLength && ticks.length < 24; mark += tick) {
    if (mark >= 0) ticks.push(mark);
  }
  const playhead = time * pps - scrollLeft;
  const selected = activeIndex == null ? null : lines[activeIndex];

  return (
    <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-3 sm:p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">
            {activeIndex == null ? "Biên các câu" : `Câu ${activeIndex + 1} · biên từng chữ`}
          </p>
          <p className="mt-1 text-xs leading-5 text-[var(--muted)]">
            {activeIndex == null
              ? "Kéo cả câu để dời. Kéo hai mép để đổi chỗ câu bắt đầu và kết thúc. Bấm câu để sửa từng chữ."
              : "Kéo chữ tới chỗ hát bắt đầu. Kéo mép chữ để đổi độ dài. Bấm Các câu để quay lại."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {activeIndex != null && (
            <button type="button" className="rounded-full border border-[var(--line)] px-3 py-1 text-xs text-[var(--gold)]" onClick={() => onSelectLine(null)}>
              Các câu
            </button>
          )}
          <button type="button" className="rounded-full border border-[var(--line)] px-3 py-1 text-xs" onClick={() => zoomBy(1 / 1.4)}>
            Thu nhỏ
          </button>
          <button type="button" className="rounded-full border border-[var(--line)] px-3 py-1 text-xs" onClick={() => zoomBy(1.4)}>
            Phóng to
          </button>
          <button type="button" className="rounded-full border border-[var(--line)] px-3 py-1 text-xs" onClick={fitSong}>
            Vừa bài
          </button>
          {activeIndex != null && (
            <button type="button" className="rounded-full border border-[var(--line)] px-3 py-1 text-xs" onClick={() => fitLine(activeIndex)}>
              Vừa câu
            </button>
          )}
        </div>
      </div>
      <div ref={stageRef} className="relative">
        <div
          ref={scrollRef}
          className="overflow-x-auto overflow-y-hidden"
          onScroll={() => setScrollLeft(scrollRef.current?.scrollLeft ?? 0)}
        >
          <div style={{ width: trackWidth, height: RULER + WAVE + LANE + 14 }} />
        </div>
        <div className="absolute inset-x-0 top-0" style={{ height: RULER + WAVE + LANE }}>
          <div className="relative overflow-hidden border-b border-white/10" style={{ height: RULER }} onPointerDown={beginPan}>
            {ticks.map((mark) => (
              <span key={mark} className="absolute top-0 text-[10px] text-[var(--muted)]" style={{ left: mark * pps - scrollLeft + 4 }}>
                {formatSeconds(mark)}
              </span>
            ))}
          </div>
          <div className="relative cursor-grab overflow-hidden bg-black/50" style={{ height: WAVE }} onPointerDown={beginPan}>
            <canvas ref={canvasRef} className="h-full w-full" />
            {selected && (
              <div
                className="pointer-events-none absolute top-0 bottom-0 bg-[var(--gold)]/10"
                style={{ left: selected.start * pps - scrollLeft, width: Math.max(0, (selected.end - selected.start) * pps) }}
              />
            )}
            {!peaks && !failed && <p className="absolute inset-0 flex items-center justify-center text-xs text-[var(--muted)]">Đang vẽ sóng âm…</p>}
            {failed && <p className="absolute inset-0 flex items-center justify-center text-xs text-[var(--muted)]">Không vẽ được sóng âm. Vẫn kéo được chữ.</p>}
          </div>
          <div className="relative overflow-hidden bg-black/35" style={{ height: LANE }} onPointerDown={beginPan}>
            {activeIndex != null &&
              lines.map((line, index) => (
                <button
                  key={`context-${index}`}
                  type="button"
                  title={line.text}
                  className={`absolute top-1 h-1.5 rounded-full ${index === activeIndex ? "bg-[var(--gold)]" : "bg-white/20"}`}
                  style={{ left: line.start * pps - scrollLeft, width: Math.max(4, (line.end - line.start) * pps) }}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectLine(index);
                    fitLine(index);
                  }}
                />
              ))}
            {activeIndex != null &&
              lines[activeIndex].words.map((word, wordIndex) => (
              <Block
                key={`${activeIndex}-${wordIndex}`}
                text={word.text}
                title={`${word.text} · ${formatSeconds(word.start)}`}
                left={word.start * pps - scrollLeft}
                width={(word.end - word.start) * pps}
                top={18}
                disabled={locked}
                onEdge={(event, edge) => beginDrag(event, (delta) => applyWord(activeIndex, wordIndex, edge, delta), () => onSeek(word.start))}
              />
            ))}
            {activeIndex == null &&
              lines.map((line, index) => (
                <Block
                  key={`line-${index}`}
                  text={`${index + 1}. ${line.text}`}
                  title={`Câu ${index + 1} · ${formatSeconds(line.start)} – ${formatSeconds(line.end)}`}
                  left={line.start * pps - scrollLeft}
                  width={(line.end - line.start) * pps}
                  top={10}
                  active={index === selectedLine}
                  disabled={locked}
                  onEdge={(event, edge) =>
                    beginDrag(event, (delta) => applyLine(index, edge, delta), () => {
                      onSelectLine(index);
                      fitLine(index);
                    })
                  }
                />
              ))}
          </div>
          {playhead >= 0 && playhead <= viewport && <div className="pointer-events-none absolute top-0 bottom-0 z-20 w-px bg-white" style={{ left: playhead }} />}
        </div>
        <input
          type="range"
          min={0}
          max={Math.max(0, trackWidth - viewport)}
          step={1}
          value={Math.min(scrollLeft, Math.max(0, trackWidth - viewport))}
          onChange={(event) => moveScroll(Number(event.target.value))}
          aria-label="Kéo ngang timeline"
          className="mt-2 w-full accent-[var(--gold)]"
        />
      </div>
    </section>
  );
}

function Block({
  text,
  title,
  left,
  width,
  top,
  active,
  disabled,
  onEdge,
}: {
  text: string;
  title: string;
  left: number;
  width: number;
  top: number;
  active?: boolean;
  disabled: boolean;
  onEdge: (event: ReactPointerEvent<HTMLButtonElement>, edge: Edge) => void;
}) {
  return (
    <div
      className={`absolute h-9 overflow-hidden rounded-md border ${active ? "border-[var(--gold)] bg-[var(--gold)]/30" : "border-[var(--gold)]/40 bg-[var(--gold)]/15"}`}
      style={{ left, width: Math.max(width, 8), top }}
      title={title}
    >
      <button type="button" disabled={disabled} aria-label="Đổi lúc bắt đầu" className="absolute inset-y-0 left-0 z-10 w-2 cursor-ew-resize" onPointerDown={(event) => onEdge(event, "start")} />
      <button
        type="button"
        disabled={disabled}
        className="h-full w-full cursor-grab truncate px-2 text-left text-[11px] leading-9 disabled:cursor-default"
        onPointerDown={(event) => onEdge(event, "move")}
      >
        {text}
      </button>
      <button type="button" disabled={disabled} aria-label="Đổi lúc kết thúc" className="absolute inset-y-0 right-0 z-10 w-2 cursor-ew-resize" onPointerDown={(event) => onEdge(event, "end")} />
    </div>
  );
}
