"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { AlignTimeline } from "@/components/align-timeline";
import { Drawer, useDialog } from "@/components/ui/dialog";
import { Field, PathField, Select } from "@/components/ui/field";
import {
  Badge,
  Disclosure,
  Panel,
  ProgressBar,
  SectionTitle,
  Segmented,
  btnGhost,
  btnPrimary,
  btnSecondary,
} from "@/components/ui/primitives";
import { useNotices } from "@/components/ui/toast";
import { cueAt, formatSeconds, wordFill, type TimedLine } from "@/lib/cue";
import { readError } from "@/lib/api";
import { useDirty } from "@/lib/dirty";
import { fileName, profileMeta } from "@/lib/format";
import { clearSession, readSession, sessionKeys, writeSession } from "@/lib/session";
import {
  mergePresentation,
  mergeWaveform,
  type Presentation,
  type Project,
  type WaveformPlan,
} from "@/lib/types";

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
  waveform?: WaveformPlan;
};

type TimingDraft = {
  version?: number;
  savedAt?: number;
  lines: TimedLine[];
  removed: TimedLine[];
  dirty: boolean;
  selectedLine: number | null;
  exportDir: string;
  exportFormat: ExportOptions["format"];
};

const DRAFT_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

function isUsableDraft(value: TimingDraft | null): value is TimingDraft {
  if (!value || !Array.isArray(value.lines)) return false;
  if (value.version != null && value.version !== 1) return false;
  if (value.savedAt != null && Date.now() - value.savedAt > DRAFT_MAX_AGE_MS) return false;
  return true;
}

export function TimingEditor({
  jobId,
  projectPath = "",
  initialLines,
  locked,
  exported = false,
  error,
  log,
  outputs,
  defaultOutputDir = "",
  background = null,
  renderPercent = 0,
  openExportRequest = null,
  onExportRequestHandled,
  presentation,
  waveform,
  outputProfile = "landscape_1080p",
  onPresentationSaved,
  onRender,
  onReveal,
  onBuildBackground,
  onCleanupBackground,
  onResumeEditing,
  onSavedPath,
  onBrowseOutput,
  onBack,
  onRealign,
  onFillGaps,
  onNewProject,
  exportGaps = [],
}: {
  jobId: string;
  projectPath?: string;
  initialLines: TimedLine[];
  locked: boolean;
  exported?: boolean;
  error: string;
  log: string[];
  outputs: { vocal: string; instrumental: string; directory: string } | null;
  defaultOutputDir?: string;
  renderPercent?: number;
  openExportRequest?: number | null;
  onExportRequestHandled?: () => void;
  background?: {
    state: "missing" | "stale" | "ready" | "building" | "error";
    percent?: number;
    reason?: string;
    hash?: string;
    path?: string;
    total_frames?: number;
    cover_frames?: number;
    song_frames?: number;
    image_frames?: number;
    profile_id?: string;
    fps?: number;
  } | null;
  presentation?: Presentation;
  waveform?: WaveformPlan;
  outputProfile?: string;
  onPresentationSaved?: (project: Project) => void;
  onRender: (lines: TimedLine[], options: ExportOptions) => void;
  onReveal: (path: string) => void;
  onBuildBackground?: (rebuild: boolean) => void;
  onCleanupBackground?: () => void;
  onResumeEditing?: () => void;
  onSavedPath?: (path: string) => void;
  onBrowseOutput?: () => Promise<string | null>;
  onBack: () => void;
  onRealign?: () => void;
  onFillGaps?: () => void;
  onNewProject?: () => void;
  exportGaps?: string[];
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const { push } = useNotices();
  const { confirm } = useDialog();
  const draftKey = sessionKeys.timingDraft(projectPath || jobId);
  const legacyDraftKey = projectPath ? sessionKeys.timingDraft(jobId) : "";

  const [restored] = useState<TimingDraft | null>(() => {
    const draft = readSession<TimingDraft | null>(draftKey, null);
    if (isUsableDraft(draft)) return draft;
    if (legacyDraftKey) {
      const legacy = readSession<TimingDraft | null>(legacyDraftKey, null);
      if (isUsableDraft(legacy)) {
        writeSession(draftKey, legacy);
        clearSession(legacyDraftKey);
        return legacy;
      }
    }
    return null;
  });
  const [lines, setLines] = useState<TimedLine[]>(() => cloneLines(restored?.lines ?? initialLines));
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selectedLine, setSelectedLine] = useState<number | null>(() => {
    const index = restored?.selectedLine;
    return index != null && index >= 0 && index < (restored?.lines.length ?? 0) ? index : null;
  });
  const [selectedWord, setSelectedWord] = useState<number | null>(null);
  const [savedPath, setSavedPath] = useState("");
  const [saveError, setSaveError] = useState("");
  const [dirty, setDirty] = useState(() => Boolean(restored?.dirty));
  const [exportDir, setExportDir] = useState(() => restored?.exportDir || defaultOutputDir);
  const [exportFormat, setExportFormat] = useState<ExportOptions["format"]>(() => {
    const format = restored?.exportFormat;
    return format === "mov" || format === "mkv" ? format : "mp4";
  });
  const [browsingOutput, setBrowsingOutput] = useState(false);
  const [removed, setRemoved] = useState<TimedLine[]>(() =>
    Array.isArray(restored?.removed) ? restored.removed : []
  );
  const [exportOpen, setExportOpen] = useState(false);
  const closeExport = useCallback(() => setExportOpen(false), []);
  useEffect(() => {
    if (openExportRequest == null) return;
    if (locked || lines.length === 0) return;
    setExportOpen(true);
    onExportRequestHandled?.();
  }, [openExportRequest, locked, lines.length, onExportRequestHandled]);
  const [exportTab, setExportTab] = useState<"export" | "waveform" | "pages">("export");
  const [wave, setWave] = useState<WaveformPlan>(() => mergeWaveform(waveform));
  const [waveSaving, setWaveSaving] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [waveError, setWaveError] = useState("");
  const waveRef = useRef(wave);
  const persistTimer = useRef<number | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    offsetX: number;
    offsetY: number;
    frameRect: DOMRect;
  } | null>(null);
  waveRef.current = wave;
  const waveSignature = waveform ? JSON.stringify(waveform) : "";
  useEffect(() => {
    if (!waveSignature) return;
    const next = mergeWaveform(waveform);
    setWave(next);
    waveRef.current = next;
  }, [waveSignature]);
  useEffect(() => {
    return () => {
      if (persistTimer.current) window.clearTimeout(persistTimer.current);
    };
  }, []);

  const cue = cueAt(lines, time);
  const backgroundBuilding = background?.state === "building";
  const backgroundReady = background?.state === "ready";
  const backgroundLabel =
    background?.state === "building"
      ? `Đang dựng nền${typeof background.percent === "number" ? ` ${background.percent}%` : "…"}`
      : backgroundReady
        ? "Nền đã sẵn sàng"
        : background?.state === "stale"
          ? "Nền cần dựng lại"
          : background?.state === "error"
            ? "Dựng nền lỗi"
            : "Chưa dựng nền";
  const exportStatusTone: "gold" | "success" | "danger" | "muted" = locked
    ? "gold"
    : outputs
      ? "success"
      : exportGaps.length
        ? "danger"
        : "muted";
  const exportStatusLabel = locked
    ? `Đang xuất${renderPercent ? ` ${renderPercent}%` : "…"}`
    : outputs
      ? "Đã xuất"
      : exportGaps.length
        ? "Thiếu file"
        : "Chưa xuất";

  const restoredNotice = useRef(false);
  const suppressFlush = useRef(false);
  useEffect(() => {
    if (!restored?.dirty || restoredNotice.current) return;
    restoredNotice.current = true;
    push({
      tone: "info",
      message: "Đã khôi phục bản nháp chưa lưu",
      action: {
        label: "Bỏ bản nháp",
        onClick: discardRestoredDraft,
      },
    });
  }, [push, restored, initialLines, draftKey, legacyDraftKey]);

  const latestDraft = useRef<TimingDraft | null>(null);
  const dirtyRef = useRef(dirty);
  const writeTimer = useRef<number | null>(null);
  dirtyRef.current = dirty;
  latestDraft.current = {
    version: 1,
    savedAt: Date.now(),
    lines,
    removed,
    dirty,
    selectedLine,
    exportDir,
    exportFormat,
  };

  useEffect(() => {
    if (!dirty) {
      clearSession(draftKey);
      if (legacyDraftKey) clearSession(legacyDraftKey);
      return;
    }
    if (writeTimer.current) window.clearTimeout(writeTimer.current);
    writeTimer.current = window.setTimeout(() => {
      if (latestDraft.current) writeSession(draftKey, latestDraft.current);
    }, 300);
    return () => {
      if (writeTimer.current) window.clearTimeout(writeTimer.current);
    };
  }, [draftKey, legacyDraftKey, lines, removed, dirty, selectedLine, exportDir, exportFormat]);

  useEffect(() => {
    function onPageHide() {
      if (!suppressFlush.current && dirtyRef.current && latestDraft.current) {
        writeSession(draftKey, latestDraft.current);
      }
    }
    window.addEventListener("pagehide", onPageHide);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      if (!suppressFlush.current && dirtyRef.current && latestDraft.current) {
        writeSession(draftKey, latestDraft.current);
      }
    };
  }, [draftKey]);

  useEffect(() => {
    if (defaultOutputDir) setExportDir((current) => current || defaultOutputDir);
  }, [defaultOutputDir]);

  useEffect(() => {
    if (outputs?.directory) setExportDir(outputs.directory);
  }, [outputs?.directory]);

  function discardRestoredDraft() {
    suppressFlush.current = true;
    if (writeTimer.current) window.clearTimeout(writeTimer.current);
    setLines(cloneLines(initialLines));
    setRemoved([]);
    setSelectedLine(null);
    setSelectedWord(null);
    setDirty(false);
    clearSession(draftKey);
    if (legacyDraftKey) clearSession(legacyDraftKey);
    push({ tone: "info", message: "Đã bỏ bản nháp, quay về bản đã lưu." });
  }

  function markDirty() {
    suppressFlush.current = false;
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

  const reorder = useRef<number[] | null>(null);

  function sortLines() {
    setLines((current) => {
      const order = current.map((_, index) => index).sort((a, b) => current[a].start - current[b].start || a - b);
      if (order.every((from, to) => from === to)) return current;
      reorder.current = order;
      return order.map((from) => current[from]);
    });
  }

  useEffect(() => {
    const order = reorder.current;
    if (!order) return;
    reorder.current = null;
    setSelectedLine((selected) => (selected == null ? selected : order.indexOf(selected)));
  }, [lines]);

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

  async function leaveIfSaved(leave: () => void) {
    if (!dirty) {
      leave();
      return;
    }
    const ok = await confirm({
      title: "Timing chưa lưu",
      description: "Các thay đổi timing chưa được lưu và sẽ mất nếu rời màn Biên tập.",
      confirmLabel: "Rời và bỏ thay đổi",
      cancelLabel: "Ở lại",
      danger: true,
    });
    if (ok) {
      suppressFlush.current = true;
      if (writeTimer.current) window.clearTimeout(writeTimer.current);
      clearSession(draftKey);
      if (legacyDraftKey) clearSession(legacyDraftKey);
      leave();
    }
  }

  function removeLine(lineIndex: number) {
    const line = lines[lineIndex];
    if (!line) return;
    markDirty();
    setRemoved((current) => [...current, line]);
    setLines((current) => current.filter((_, index) => index !== lineIndex));
    setSelectedWord(null);
    setSelectedLine((selected) => {
      if (selected == null || selected === lineIndex) return null;
      return selected > lineIndex ? selected - 1 : selected;
    });
  }

  function undoRemove() {
    const line = removed[removed.length - 1];
    if (!line) return;
    markDirty();
    const later = lines.findIndex((item) => item.start > line.start);
    const index = later < 0 ? lines.length : later;
    setRemoved(removed.slice(0, -1));
    setLines([...lines.slice(0, index), line, ...lines.slice(index)]);
    setSelectedWord(null);
    setSelectedLine((selected) => (selected != null && selected >= index ? selected + 1 : selected));
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
        return false;
      }
      const body = await response.json();
      setSavedPath(body.path || "");
      if (body.path) onSavedPath?.(body.path);
      suppressFlush.current = true;
      if (writeTimer.current) window.clearTimeout(writeTimer.current);
      setDirty(false);
      clearSession(draftKey);
      if (legacyDraftKey) clearSession(legacyDraftKey);
      push({ tone: "success", message: "Đã lưu dự án" });
      return true;
    } catch {
      setSaveError("Chưa lưu được. Server Python chưa chạy.");
      return false;
    }
  }

  useDirty(
    "karaoke.timing",
    dirty,
    saveTiming,
    () => {
      suppressFlush.current = true;
      if (writeTimer.current) window.clearTimeout(writeTimer.current);
      clearSession(draftKey);
      if (legacyDraftKey) clearSession(legacyDraftKey);
    },
  );

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
    onRender(lines, { output_dir: exportDir.trim(), format: exportFormat, waveform: waveRef.current });
  }

  async function persistPresentation(partial: {
    waveform?: WaveformPlan;
    presentation?: Presentation;
  }) {
    if (!projectPath) {
      setWaveSaving("error");
      setWaveError("Chưa có file dự án. Hãy Lưu dự án trước khi đổi waveform.");
      return;
    }
    setWaveSaving("saving");
    setWaveError("");
    try {
      const response = await fetch("/api/projects/presentation", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ project_path: projectPath, ...partial }),
      });
      if (!response.ok) {
        setWaveSaving("error");
        setWaveError(await readError(response));
        return;
      }
      const data = (await response.json()) as { project?: Project };
      const project = data.project;
      if (project) onPresentationSaved?.(project);
      setWaveSaving("saved");
      window.setTimeout(() => setWaveSaving((current) => (current === "saved" ? "idle" : current)), 1800);
    } catch {
      setWaveSaving("error");
      setWaveError("Chưa lưu được vị trí waveform. Kiểm tra server Python.");
    }
  }

  function commitWave(patch: Partial<WaveformPlan>, debounce = false) {
    const next = { ...waveRef.current, ...patch };
    waveRef.current = next;
    setWave(next);
    if (persistTimer.current) window.clearTimeout(persistTimer.current);
    if (debounce) {
      persistTimer.current = window.setTimeout(() => {
        void persistPresentation({ waveform: waveRef.current });
      }, 500);
    } else {
      void persistPresentation({ waveform: next });
    }
  }

  function waveCenterFromPointer(event: React.PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) return null;
    const rect = event.currentTarget.parentElement?.getBoundingClientRect() || drag.frameRect;
    if (rect.width <= 0 || rect.height <= 0) return null;
    const halfW = waveRef.current.width / 2;
    const halfH = waveRef.current.height / 2;
    const x = (event.clientX - drag.offsetX - rect.left) / rect.width;
    const y = (event.clientY - drag.offsetY - rect.top) / rect.height;
    return {
      x: Math.min(1 - halfW, Math.max(halfW, x)),
      y: Math.min(1 - halfH, Math.max(halfH, y)),
    };
  }

  function startWaveDrag(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault();
    event.stopPropagation();
    const rect = event.currentTarget.parentElement?.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return;
    const centerX = rect.left + waveRef.current.x * rect.width;
    const centerY = rect.top + waveRef.current.y * rect.height;
    dragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - centerX,
      offsetY: event.clientY - centerY,
      frameRect: rect,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function moveWaveDrag(event: React.PointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return;
    const next = waveCenterFromPointer(event);
    if (!next) return;
    const updated = { ...waveRef.current, ...next };
    waveRef.current = updated;
    setWave(updated);
  }

  function endWaveDrag(event: React.PointerEvent<HTMLDivElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    if (!dragRef.current) return;
    dragRef.current = null;
    void persistPresentation({ waveform: waveRef.current });
  }

  function snapWave(x: number, y: number) {
    commitWave({ x, y });
  }

  const pagePresentation = mergePresentation(presentation);
  const profile = profileMeta(outputProfile);
  const snapMargin = 0.04;
  const snapPoints = [
    { key: "tl", label: "↖", x: wave.width / 2 + snapMargin, y: wave.height / 2 + snapMargin },
    { key: "tc", label: "↑", x: 0.5, y: wave.height / 2 + snapMargin },
    { key: "tr", label: "↗", x: 1 - wave.width / 2 - snapMargin, y: wave.height / 2 + snapMargin },
    { key: "ml", label: "←", x: wave.width / 2 + snapMargin, y: 0.5 },
    { key: "mc", label: "•", x: 0.5, y: 0.5 },
    { key: "mr", label: "→", x: 1 - wave.width / 2 - snapMargin, y: 0.5 },
    { key: "bl", label: "↙", x: wave.width / 2 + snapMargin, y: 1 - wave.height / 2 - snapMargin },
    { key: "bc", label: "↓", x: 0.5, y: 1 - wave.height / 2 - snapMargin },
    { key: "br", label: "↘", x: 1 - wave.width / 2 - snapMargin, y: 1 - wave.height / 2 - snapMargin },
  ];
  const waveBars = Array.from({ length: 34 }, (_, index) => {
    const phase = Math.sin(index * 1.7) * 0.34 + Math.sin(index * 0.51 + 1.2) * 0.36;
    return Math.min(1, Math.max(0.14, Math.abs(phase) + 0.3));
  });
  const playhead = 0.42;

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
        onDragEnd={sortLines}
        onRemoveLine={removeLine}
        actions={
          <>
            {onRealign && (
              <button
                type="button"
                disabled={locked}
                onClick={() => void leaveIfSaved(onRealign)}
                className="text-sm text-[var(--gold)] hover:text-white disabled:opacity-40"
              >
                Ghép chữ lại
              </button>
            )}
            <button
              type="button"
              onClick={() => void leaveIfSaved(onBack)}
              className="text-sm text-[var(--muted)] hover:text-white"
            >
              Sửa tên, ảnh, lời…
            </button>
            {onNewProject && (
              <button
                type="button"
                onClick={() => void leaveIfSaved(onNewProject)}
                className="text-sm text-[var(--muted)] hover:text-white"
              >
                Dự án mới
              </button>
            )}
            <button
              type="button"
              disabled={locked || lines.length === 0}
              onClick={() => void saveTiming()}
              className="rounded-full border border-[var(--gold)] px-4 py-1.5 text-sm font-semibold text-[var(--gold)] disabled:opacity-60"
            >
              {locked ? "Đang xuất… (lưu sau)" : dirty ? "Lưu dự án •" : "Lưu dự án"}
            </button>
            {saveError ? (
              <span className="text-xs text-red-200">Chưa lưu được</span>
            ) : savedPath && !dirty ? (
              <button
                type="button"
                className="text-xs text-[var(--muted)] hover:text-[var(--gold)]"
                onClick={() => onReveal(savedPath)}
              >
                Đã lưu
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => setExportOpen(true)}
              className="rounded-full border border-[var(--line)] px-4 py-1.5 text-sm font-semibold text-[var(--paper)] transition hover:border-[var(--gold)] hover:text-[var(--gold)]"
            >
              {exported ? "Xuất lại" : "Xuất"}
            </button>
            <Badge tone={exportStatusTone}>{exportStatusLabel}</Badge>
          </>
        }
      />

      <div className="lg:grid lg:grid-cols-[minmax(300px,0.85fr)_minmax(0,1.15fr)] lg:items-start lg:gap-6">
        <section className="sticky top-0 z-10 mb-5 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-4 sm:p-5 lg:top-4 lg:mb-0">
          <div className="mb-4">
            <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Biên tập</p>
            <h2 className="mt-1 text-xl font-semibold">Chỉnh chữ và lúc từng chữ hiện ra</h2>
          </div>
          {restored?.dirty && dirty ? (
            <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--gold)]/60 bg-black/25 px-3 py-2 text-xs leading-5">
              <span className="text-[var(--muted)]">Đang hiển thị bản nháp chưa lưu.</span>
              <button type="button" onClick={discardRestoredDraft} className="font-semibold text-[var(--gold)] hover:text-white">
                Bỏ bản nháp, về bản đã lưu
              </button>
            </div>
          ) : null}
          {(exported || outputs) && (
            <p className="mb-4 rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2 text-sm leading-6 text-[var(--muted)]">
              {exported && !dirty
                ? "Đã xuất video. Sửa tên/ảnh/lời ở “Sửa tên, ảnh, lời…” rồi Xuất lại."
                : dirty
                  ? "Đang sửa sau khi xuất. Lưu dự án rồi Xuất lại nếu cần video mới."
                  : "Có bản đã xuất. Sửa timing tại đây rồi Xuất lại."}
            </p>
          )}
          <div className="flex min-h-28 flex-col items-center justify-center rounded-2xl bg-black/45 px-4 py-5">
            {cue.mode === "countdown" && <p className="mb-2 text-3xl font-semibold text-[var(--gold)]">{cue.number}</p>}
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
            Sóng âm ở trên, chữ ở dưới. Giữ ⌘ rồi cuộn để phóng to.
          </p>
          <Disclosure title="Trợ giúp" className="mt-2" summary="Cách căn">
            <p className="text-xs leading-5 text-[var(--muted)]">
              Cuộn để kéo ngang. Câu sau hiện ngay khi câu trước ngân xong. Bấm một câu bên phải để sửa từng chữ.
            </p>
          </Disclosure>
        </section>

        <div className="min-w-0 space-y-3 lg:max-h-[calc(100vh-2rem)] lg:overflow-y-auto lg:pr-1">
          <section className="space-y-2">
            {lines.map((line, lineIndex) => {
              const active = selectedLine === lineIndex;
              return (
                <article
                  id={`karaoke-line-${lineIndex}`}
                  key={lineIndex}
                  className={`scroll-mt-4 rounded-2xl border bg-[var(--panel)] transition ${
                    active ? "border-[var(--gold)]" : "border-[var(--line)]"
                  }`}
                >
                  <div className="flex items-center gap-1.5 p-2 sm:gap-2 sm:p-3">
                    <button
                      type="button"
                      onClick={() => chooseLine(active ? null : lineIndex)}
                      aria-expanded={active}
                      className="flex min-w-0 flex-1 items-center gap-2 rounded-xl px-2 py-2 text-left transition hover:bg-white/5 sm:gap-3"
                    >
                      <span className="w-11 shrink-0 text-xs text-[var(--muted)]">Câu {lineIndex + 1}</span>
                      <span className="shrink-0 text-xs tabular-nums text-[var(--muted)]">
                        {formatSeconds(line.start)}–{formatSeconds(line.end)}
                      </span>
                      <span className={`min-w-0 flex-1 truncate text-sm ${active ? "text-[var(--paper)]" : "text-[var(--muted)]"}`}>
                        {line.text || "(chưa có chữ)"}
                      </span>
                      {line.supplemented ? (
                        <Badge tone="info">bổ sung</Badge>
                      ) : line.uncertain ? (
                        <Badge tone="gold">ước lượng</Badge>
                      ) : null}
                    </button>
                    <button
                      type="button"
                      disabled={locked}
                      onClick={() => {
                        chooseLine(lineIndex);
                        playFrom(line.start);
                      }}
                      aria-label={`Nghe câu ${lineIndex + 1}`}
                      className="shrink-0 rounded-lg px-2 py-1 text-xs font-semibold text-[var(--gold)] transition hover:bg-white/5 disabled:opacity-40"
                    >
                      Nghe
                    </button>
                    <details className="relative shrink-0">
                      <summary
                        aria-label={`Menu câu ${lineIndex + 1}`}
                        className="cursor-pointer list-none rounded-lg px-2 py-1 text-sm text-[var(--muted)] transition hover:bg-white/5 hover:text-white"
                      >
                        ⋯
                      </summary>
                      <div className="absolute right-0 z-20 mt-1 w-44 rounded-xl border border-[var(--line)] bg-[var(--ink)] p-1 shadow-2xl">
                        <button
                          type="button"
                          disabled={locked}
                          onClick={() => {
                            chooseLine(lineIndex);
                            playFrom(line.start);
                          }}
                          className="block w-full rounded-lg px-3 py-2 text-left text-sm text-[var(--gold)] hover:bg-white/5 disabled:opacity-40"
                        >
                          Nghe câu này
                        </button>
                        <button
                          type="button"
                          disabled={locked}
                          onClick={() => removeLine(lineIndex)}
                          className="block w-full rounded-lg px-3 py-2 text-left text-sm text-[var(--muted)] hover:bg-red-950/40 hover:text-red-200 disabled:opacity-40"
                        >
                          Xoá câu
                        </button>
                      </div>
                    </details>
                  </div>

                  {active && (
                    <div className="space-y-3 border-t border-[var(--line)] p-3 sm:p-4">
                      {line.supplemented && (
                        <p className="rounded-xl bg-sky-950/50 px-3 py-2 text-xs leading-5 text-sky-100">
                          Whisper nghe được câu này nhưng lời bạn nhập chưa có. Sửa chữ nếu đó là hát bè hoặc đoạn kết.
                        </p>
                      )}
                      {line.uncertain && !line.supplemented && (
                        <p className="rounded-xl bg-amber-950/50 px-3 py-2 text-xs leading-5 text-amber-100">
                          Câu này được ghép ước lượng. Kéo vùng vàng trên sóng âm để dời cả câu, hoặc kéo từng chữ.
                        </p>
                      )}
                      <div className="flex flex-wrap gap-2">
                        {line.words.map((word, wordIndex) => (
                          <div
                            key={`${lineIndex}-${wordIndex}`}
                            className={`flex items-center gap-1 rounded-xl border bg-black/20 px-2 py-1 ${
                              selectedWord === wordIndex ? "border-[var(--gold)]" : "border-[var(--line)]"
                            }`}
                          >
                            <input
                              value={word.text}
                              disabled={locked}
                              onFocus={() => setSelectedWord(wordIndex)}
                              onChange={(event) => changeWord(lineIndex, wordIndex, event.target.value)}
                              aria-label={`Chữ ${wordIndex + 1} của câu ${lineIndex + 1}`}
                              className="w-20 bg-transparent text-xs outline-none disabled:opacity-60"
                            />
                            <button
                              type="button"
                              disabled={locked}
                              aria-label={`Xoá chữ ${word.text}`}
                              onClick={() => removeWord(lineIndex, wordIndex)}
                              className="text-xs text-[var(--muted)] transition hover:text-white disabled:opacity-40"
                            >
                              ×
                            </button>
                          </div>
                        ))}
                        <button
                          type="button"
                          disabled={locked}
                          onClick={() => addWord(lineIndex)}
                          className="rounded-xl border border-dashed border-[var(--line)] px-3 text-sm text-[var(--muted)] transition hover:text-[var(--paper)] disabled:opacity-40"
                        >
                          Thêm chữ
                        </button>
                        <button
                          type="button"
                          disabled={locked || splitWordIndex(lineIndex) == null}
                          onClick={() => splitLine(lineIndex)}
                          className="rounded-xl border border-dashed border-[var(--line)] px-3 text-sm text-[var(--muted)] transition hover:text-[var(--paper)] disabled:opacity-40"
                        >
                          Chia câu
                        </button>
                        <button
                          type="button"
                          disabled={locked}
                          onClick={() => removeLine(lineIndex)}
                          className="rounded-xl border border-dashed border-[var(--line)] px-3 text-sm text-[var(--muted)] transition hover:text-red-200 disabled:opacity-40"
                        >
                          Xoá câu
                        </button>
                      </div>
                    </div>
                  )}
                </article>
              );
            })}
          </section>

          {removed.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-[var(--line)] bg-[var(--panel)] px-3 py-2 text-sm text-[var(--muted)]">
              <span className="min-w-0 truncate">
                {removed.length > 1 ? `Đã xoá ${removed.length} câu. Gần nhất: ` : "Đã xoá câu: "}
                {removed[removed.length - 1].text}
              </span>
              <div className="flex shrink-0 items-center gap-3">
                <button type="button" disabled={locked} className="text-[var(--gold)] disabled:opacity-40" onClick={undoRemove}>
                  Hoàn tác
                </button>
                <button type="button" className="hover:text-white" onClick={() => setRemoved([])}>
                  Ẩn
                </button>
              </div>
            </div>
          )}

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
        </div>
      </div>

      <Drawer
        open={exportOpen}
        onClose={closeExport}
        title="Xuất video"
        description="Hai video: có lời và không lời. Tên file có hậu tố thời gian."
        width="max-w-2xl"
        footer={
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-xs text-[var(--muted)]">
              {exportGaps.length > 0
                ? "Còn thiếu file để xuất."
                : !exportDir.trim()
                  ? "Chọn thư mục xuất trước."
                  : outputs
                    ? "Xuất lại sẽ tạo bản mới."
                    : "Sẵn sàng xuất."}
            </span>
            <button
              type="button"
              disabled={locked || backgroundBuilding || lines.length === 0 || exportGaps.length > 0 || !exportDir.trim()}
              onClick={startExport}
              className={btnPrimary}
            >
              {locked
                ? `Đang xuất video…${renderPercent ? ` ${renderPercent}%` : ""}`
                : outputs
                  ? "Xuất lại hai video"
                  : "Xuất hai video"}
            </button>
          </div>
        }
      >
        <div className="space-y-5">
          <Segmented<"export" | "waveform" | "pages">
            value={exportTab}
            onChange={setExportTab}
            ariaLabel="Phần cài đặt xuất"
            options={[
              { value: "export", label: "Xuất" },
              { value: "waveform", label: "Waveform", badge: wave.enabled ? "bật" : undefined },
              { value: "pages", label: "Trang" },
            ]}
          />
          {exportTab === "export" ? (
          <div className="space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <SectionTitle>Trạng thái xuất</SectionTitle>
            <Badge tone={exportStatusTone}>{exportStatusLabel}</Badge>
          </div>
          <fieldset disabled={locked} className="space-y-3">
            <PathField
              label="Thư mục xuất"
              value={exportDir}
              placeholder="Chọn nơi lưu hai video"
              onBrowse={pickExportDir}
              busy={browsingOutput}
              hint="Tên file có hậu tố thời gian nên xuất lại không ghi đè bản trước."
            />
            <Field label="Định dạng" htmlFor="export-format">
              <Select
                id="export-format"
                value={exportFormat}
                onChange={(event) => setExportFormat(event.target.value as ExportOptions["format"])}
              >
                <option value="mp4">MP4</option>
                <option value="mov">MOV</option>
                <option value="mkv">MKV</option>
              </Select>
            </Field>
          </fieldset>

          {locked && (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs text-[var(--muted)]">
                <span>Đang xuất video…</span>
                <span>{renderPercent ? `${renderPercent}%` : "…"}</span>
              </div>
              <ProgressBar percent={renderPercent} />
            </div>
          )}

          {exportGaps.length > 0 && (
            <p className="rounded-xl border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-sm leading-6 text-amber-100">
              Chưa xuất được vì còn thiếu {exportGaps.join(", ")}.{" "}
              {onFillGaps ? (
                <button type="button" className="text-[var(--gold)]" onClick={() => void leaveIfSaved(onFillGaps)}>
                  Bổ sung file trong khai báo
                </button>
              ) : (
                "Hãy bổ sung file trong phần khai báo."
              )}
            </p>
          )}

          {outputs && (
            <div className="space-y-2">
              <SectionTitle>Bản đã xuất</SectionTitle>
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <button type="button" className={btnSecondary} onClick={() => onReveal(outputs.vocal)}>
                  {fileName(outputs.vocal)}
                </button>
                <button type="button" className={btnSecondary} onClick={() => onReveal(outputs.instrumental)}>
                  {fileName(outputs.instrumental)}
                </button>
                <button type="button" className={btnGhost} onClick={() => onReveal(outputs.directory)}>
                  Mở thư mục
                </button>
                {projectPath ? (
                  <a
                    className={btnGhost}
                    href={`/publish?project=${encodeURIComponent(projectPath)}`}
                  >
                    Đăng bài
                  </a>
                ) : null}
              </div>
            </div>
          )}

          {background && (
            <Panel className="space-y-3 bg-black/15">
              <SectionTitle
                action={
                  <span
                    className={
                      backgroundReady
                        ? "text-xs text-emerald-200"
                        : background?.state === "error"
                          ? "text-xs text-red-200"
                          : "text-xs text-[var(--muted)]"
                    }
                  >
                    {backgroundLabel}
                  </span>
                }
              >
                Nền có hiệu ứng
              </SectionTitle>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={locked || backgroundBuilding || exportGaps.length > 0}
                  onClick={() => onBuildBackground?.(backgroundReady || background?.state === "stale")}
                  className="rounded-xl border border-[var(--gold)] px-3 py-2 text-xs font-semibold text-[var(--gold)] disabled:opacity-50"
                >
                  {backgroundBuilding ? "Đang dựng…" : backgroundReady ? "Dựng lại nền" : "Dựng nền"}
                </button>
                {background.path && (
                  <button
                    type="button"
                    onClick={() => onReveal(background.path!.replace(/[\\/][^\\/]+$/, ""))}
                    className={btnSecondary}
                  >
                    Mở thư mục nền
                  </button>
                )}
                {onCleanupBackground && (
                  <button
                    type="button"
                    disabled={locked || backgroundBuilding}
                    onClick={onCleanupBackground}
                    className={btnSecondary}
                  >
                    Dọn bản nền cũ
                  </button>
                )}
              </div>
              <p className="text-[11px] leading-4 text-[var(--muted)]">
                Xuất video tự dựng nền nếu chưa có. Dựng trước giúp kiểm tra nền.
              </p>
              <Disclosure
                title="Nâng cao"
                summary={
                  typeof background.total_frames === "number" && background.total_frames > 0
                    ? `${background.total_frames} frame`
                    : undefined
                }
              >
                <div className="space-y-2">
                  {background.reason && background.state !== "ready" && (
                    <p className="text-xs leading-5 text-[var(--muted)]">{background.reason}</p>
                  )}
                  {typeof background.total_frames === "number" && background.total_frames > 0 && (
                    <p className="text-xs text-[var(--muted)]">
                      {background.total_frames} frame
                      {background.fps ? ` · ${(background.total_frames / background.fps).toFixed(1)}s` : ""}
                      {background.profile_id ? ` · ${background.profile_id}` : ""}
                    </p>
                  )}
                </div>
              </Disclosure>
            </Panel>
          )}

          <Disclosure title="Nhật ký" summary={`${log.length} dòng`}>
            {log.length === 0 ? (
              <p className="text-xs text-[var(--muted)]">Chưa có nhật ký.</p>
            ) : (
              <ol className="max-h-56 space-y-1 overflow-auto text-xs leading-5 text-[var(--muted)]">
                {log.map((line, index) => (
                  <li key={`${index}-${line}`}>{line}</li>
                ))}
              </ol>
            )}
          </Disclosure>
          </div>
          ) : null}

          {exportTab === "waveform" ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <SectionTitle>Waveform</SectionTitle>
                <Badge tone={waveSaving === "error" ? "danger" : waveSaving === "saving" ? "gold" : wave.enabled ? "success" : "muted"}>
                  {waveSaving === "saving"
                    ? "Đang lưu…"
                    : waveSaving === "error"
                      ? "Chưa lưu được"
                      : waveSaving === "saved"
                        ? "Đã lưu"
                        : wave.enabled
                          ? "Đang bật"
                          : "Đang tắt"}
                </Badge>
              </div>
              <p className="text-xs leading-5 text-[var(--muted)]">
                Dải sóng nhỏ chạy theo nhạc, tự đẩy lên khi chạm dải lời. Nguồn mặc định là instrumental; cả hai video
                dùng chung một lớp hình nên bản có giọng cũng hiện waveform không vocal.
              </p>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={wave.enabled}
                  onChange={(event) => commitWave({ enabled: event.target.checked }, true)}
                  className="accent-[var(--gold)]"
                  disabled={locked}
                />
                Bật waveform
              </label>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label="Kiểu vẽ">
                  <Select
                    value={wave.style}
                    onChange={(event) => commitWave({ style: event.target.value }, true)}
                    disabled={locked}
                  >
                    <option value="bars">Cột</option>
                    <option value="line">Đường sóng</option>
                    <option value="dots">Chấm</option>
                  </Select>
                </Field>
                <Field label="Nguồn">
                  <Select
                    value={wave.source}
                    onChange={(event) => commitWave({ source: event.target.value }, true)}
                    disabled={locked}
                  >
                    <option value="instrumental">Instrumental</option>
                    <option value="original">Bài gốc / có vocal</option>
                  </Select>
                </Field>
                <Field label="Kiểu hiển thị">
                  <Select
                    value={wave.mode}
                    onChange={(event) => commitWave({ mode: event.target.value }, true)}
                    disabled={locked}
                  >
                    <option value="progress">Toàn bài, tô đến vị trí hiện tại</option>
                  </Select>
                </Field>
                <div className="flex flex-col justify-end pb-1">
                  <label className="flex cursor-pointer items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={wave.background}
                      onChange={(event) => commitWave({ background: event.target.checked }, true)}
                      className="accent-[var(--gold)]"
                      disabled={locked}
                    />
                    Nền chip bo góc
                  </label>
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs text-[var(--muted)]">
                  <span>Khung {profile.label} · {profile.size}</span>
                  <span className="tabular-nums">
                    x {wave.x.toFixed(2)} · y {wave.y.toFixed(2)} · {Math.round(wave.width * 100)}×{Math.round(wave.height * 100)}%
                  </span>
                </div>
                <div
                  className="relative w-full touch-none overflow-hidden rounded-2xl border border-[var(--line)] bg-black/60"
                  style={{ aspectRatio: profile.aspect }}
                >
                  <div
                    className="pointer-events-none absolute inset-0 opacity-40"
                    style={{
                      backgroundImage:
                        "linear-gradient(rgba(255,255,255,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.06) 1px, transparent 1px)",
                      backgroundSize: "10% 10%",
                    }}
                  />
                  <div
                    role="slider"
                    aria-label="Kéo waveform"
                    aria-valuetext={`x ${wave.x.toFixed(2)}, y ${wave.y.toFixed(2)}`}
                    tabIndex={0}
                    onPointerDown={startWaveDrag}
                    onPointerMove={moveWaveDrag}
                    onPointerUp={endWaveDrag}
                    onPointerCancel={endWaveDrag}
                    className="absolute flex cursor-grab items-center gap-px overflow-hidden rounded-lg border border-[var(--gold)]/70 px-1 active:cursor-grabbing"
                    style={{
                      left: `${(wave.x - wave.width / 2) * 100}%`,
                      top: `${(wave.y - wave.height / 2) * 100}%`,
                      width: `${wave.width * 100}%`,
                      height: `${wave.height * 100}%`,
                      opacity: wave.enabled ? wave.opacity : 0.35,
                      background: wave.background ? "rgba(8, 6, 4, 0.62)" : "transparent",
                    }}
                  >
                    {waveBars.map((height, index) => {
                      const sung = index / waveBars.length <= playhead;
                      return (
                        <span
                          key={index}
                          className="flex-1 rounded-sm"
                          style={{
                            height: `${height * 100}%`,
                            background: sung ? wave.accent : wave.color,
                            minWidth: 1,
                          }}
                        />
                      );
                    })}
                    <span
                      aria-hidden
                      className="absolute inset-y-0 w-px bg-white/80"
                      style={{ left: `${playhead * 100}%` }}
                    />
                  </div>
                </div>
                <p className="text-[11px] leading-4 text-[var(--muted)]">
                  Kéo dải sóng trên khung để đổi vị trí. Vị trí là tâm dải, lưu ngay khi thả.
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-[var(--muted)]">Snap</span>
                {snapPoints.map((point) => (
                  <button
                    key={point.key}
                    type="button"
                    onClick={() => snapWave(point.x, point.y)}
                    disabled={locked}
                    aria-label={`Snap ${point.key}`}
                    className="rounded-lg border border-[var(--line)] px-2.5 py-1 text-sm transition hover:border-[var(--gold)] hover:text-[var(--gold)] disabled:opacity-40"
                  >
                    {point.label}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={() => snapWave(0.5, 0.84)}
                  disabled={locked}
                  className={btnGhost}
                >
                  Đặt lại
                </button>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <label className="block">
                  <span className="mb-1 flex items-center justify-between text-[11px] text-[var(--muted)]">
                    <span>Độ rộng</span>
                    <span className="tabular-nums text-[var(--paper)]">{Math.round(wave.width * 100)}%</span>
                  </span>
                  <input
                    type="range"
                    min={10}
                    max={90}
                    step={1}
                    value={Math.round(wave.width * 100)}
                    onChange={(event) => commitWave({ width: Number(event.target.value) / 100 }, true)}
                    disabled={locked}
                    className="w-full accent-[var(--gold)]"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 flex items-center justify-between text-[11px] text-[var(--muted)]">
                    <span>Độ cao</span>
                    <span className="tabular-nums text-[var(--paper)]">{Math.round(wave.height * 100)}%</span>
                  </span>
                  <input
                    type="range"
                    min={3}
                    max={30}
                    step={1}
                    value={Math.round(wave.height * 100)}
                    onChange={(event) => commitWave({ height: Number(event.target.value) / 100 }, true)}
                    disabled={locked}
                    className="w-full accent-[var(--gold)]"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 flex items-center justify-between text-[11px] text-[var(--muted)]">
                    <span>Độ đậm</span>
                    <span className="tabular-nums text-[var(--paper)]">{Math.round(wave.opacity * 100)}%</span>
                  </span>
                  <input
                    type="range"
                    min={10}
                    max={100}
                    step={1}
                    value={Math.round(wave.opacity * 100)}
                    onChange={(event) => commitWave({ opacity: Number(event.target.value) / 100 }, true)}
                    disabled={locked}
                    className="w-full accent-[var(--gold)]"
                  />
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <label className="block">
                    <span className="mb-1 block text-[11px] text-[var(--muted)]">Màu chính</span>
                    <input
                      type="color"
                      value={wave.color}
                      onChange={(event) => commitWave({ color: event.target.value }, true)}
                      disabled={locked}
                      aria-label="Màu chính waveform"
                      className="h-9 w-full cursor-pointer rounded-lg border border-[var(--line)] bg-black/25"
                    />
                  </label>
                  <label className="block">
                    <span className="mb-1 block text-[11px] text-[var(--muted)]">Màu đã hát</span>
                    <input
                      type="color"
                      value={wave.accent}
                      onChange={(event) => commitWave({ accent: event.target.value }, true)}
                      disabled={locked}
                      aria-label="Màu đã hát waveform"
                      className="h-9 w-full cursor-pointer rounded-lg border border-[var(--line)] bg-black/25"
                    />
                  </label>
                </div>
              </div>

              {waveError ? (
                <p className="rounded-xl border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs leading-5 text-amber-100">
                  {waveError} Vị trí vẫn được gửi kèm lần xuất này.
                </p>
              ) : null}
            </div>
          ) : null}

          {exportTab === "pages" ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <SectionTitle>Trang & chữ</SectionTitle>
                <button type="button" onClick={() => void leaveIfSaved(onBack)} className={btnSecondary}>
                  Sửa trong khai báo
                </button>
              </div>
              <p className="text-xs leading-5 text-[var(--muted)]">
                Tóm tắt cấu hình trang. Chỉnh chi tiết ở bước Chuẩn bị → “Trang & chữ” để tránh nhân đôi form.
              </p>
              <Panel className="space-y-3 bg-black/15">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-medium">Trang bìa</h3>
                  <Badge tone="muted">
                    {pagePresentation.cover.layout === "center"
                      ? "Giữa khung"
                      : pagePresentation.cover.layout === "bottom_left"
                        ? "Dưới trái"
                        : "Dưới giữa"}
                  </Badge>
                </div>
                <dl className="grid gap-1.5 text-xs sm:grid-cols-2">
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--muted)]">Tối nền / màn mờ</dt>
                    <dd>{pagePresentation.cover.bg_darken}% / {pagePresentation.cover.veil}%</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--muted)]">Màu tên / tác giả</dt>
                    <dd className="flex items-center gap-1.5">
                      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: pagePresentation.cover.text_color }} />
                      <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: pagePresentation.cover.accent_color }} />
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--muted)]">Nhãn trên tên</dt>
                    <dd>{pagePresentation.cover.show_label && pagePresentation.cover.label ? pagePresentation.cover.label : "Ẩn"}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-[var(--muted)]">Viết hoa</dt>
                    <dd>{pagePresentation.cover.uppercase ? "Có" : "Không"}</dd>
                  </div>
                </dl>
              </Panel>
              <Panel className="space-y-2 bg-black/15">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-medium">Nhạc dạo</h3>
                  <Badge tone={pagePresentation.interlude.enabled ? "success" : "muted"}>
                    {pagePresentation.interlude.enabled ? "Bật" : "Tắt"}
                  </Badge>
                </div>
                <p className="text-xs leading-5 text-[var(--muted)]">
                  Banner “{pagePresentation.interlude.text || "Nhạc dạo"}” khi khoảng lặng ≥ {pagePresentation.interlude.min_gap}s.
                  {pagePresentation.interlude.show_next_line ? " Có hiện câu tiếp theo." : ""}
                </p>
              </Panel>
              <Panel className="space-y-2 bg-black/15">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-medium">Trang kết</h3>
                  <Badge tone={pagePresentation.outro.enabled && pagePresentation.outro.duration > 0 ? "success" : "muted"}>
                    {pagePresentation.outro.enabled && pagePresentation.outro.duration > 0
                      ? `${pagePresentation.outro.duration}s`
                      : "Tắt"}
                  </Badge>
                </div>
                <p className="text-xs leading-5 text-[var(--muted)]">
                  {pagePresentation.outro.subtext || "Cảm ơn đã xem"}
                  {pagePresentation.outro.handle ? ` · ${pagePresentation.outro.handle}` : ""}
                  {pagePresentation.outro.image ? " · ảnh nền riêng" : " · dùng ảnh bìa"}
                </p>
              </Panel>
              <p className="text-[11px] leading-4 text-[var(--muted)]">
                Màu chữ lời, cỡ chữ và nền dải lời nằm cùng nhóm “Trang & chữ” trong khai báo.
              </p>
            </div>
          ) : null}
        </div>
      </Drawer>
    </div>
  );
}
