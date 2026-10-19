// Keep these in sync with karaoke/render.py (LONG_GAP, COUNTDOWN).
export const LONG_GAP_SECONDS = 8;
export const COUNTDOWN_SECONDS = 5;

export type TimedWord = {
  text: string;
  start: number;
  end: number;
};

export type TimedLine = {
  text: string;
  start: number;
  end: number;
  uncertain?: boolean;
  words: TimedWord[];
};

export type Cue =
  | { mode: "sing"; line: TimedLine; number: 0 }
  | { mode: "countdown"; line: TimedLine; number: number }
  | { mode: "blank"; line: null; number: 0 };

function displayStart(lines: TimedLine[], index: number) {
  const line = lines[index];
  const previousEnd = index > 0 ? lines[index - 1].end : 0;
  if (index === 0 || line.start - previousEnd >= LONG_GAP_SECONDS) return line.start;
  return previousEnd;
}

function displayEnd(lines: TimedLine[], index: number) {
  const line = lines[index];
  if (index + 1 >= lines.length) return line.end + 0.7;
  if (lines[index + 1].start - line.end >= LONG_GAP_SECONDS) return line.end + 0.2;
  return Math.min(line.end, displayStart(lines, index + 1));
}

export function cueAt(lines: TimedLine[], songTime: number): Cue {
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const previousEnd = index > 0 ? lines[index - 1].end : 0;
    if (line.start - previousEnd < LONG_GAP_SECONDS) continue;
    if (line.start - COUNTDOWN_SECONDS <= songTime && songTime < line.start) {
      const remaining = line.start - songTime;
      const number = Math.max(1, Math.min(COUNTDOWN_SECONDS, Math.ceil(remaining - 1e-9)));
      return { mode: "countdown", line, number };
    }
  }
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (displayStart(lines, index) <= songTime && songTime < displayEnd(lines, index)) {
      return { mode: "sing", line, number: 0 };
    }
  }
  return { mode: "blank", line: null, number: 0 };
}

export function wordFill(word: TimedWord, songTime: number) {
  const end = Math.max(word.end, word.start + 0.05);
  if (songTime >= end) return 1;
  if (songTime <= word.start) return 0;
  return (songTime - word.start) / (end - word.start);
}

export function formatSeconds(value: number) {
  const safe = Math.max(0, value);
  const minutes = Math.floor(safe / 60);
  const seconds = safe - minutes * 60;
  return `${minutes}:${seconds.toFixed(1).padStart(4, "0")}`;
}
