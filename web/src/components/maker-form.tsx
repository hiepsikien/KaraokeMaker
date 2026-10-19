"use client";

import { useEffect, useState, type FormEvent } from "react";

import { AudioRestore } from "@/components/audio-restore";
import { HomeGate } from "@/components/home-gate";
import { PhaseStepper } from "@/components/phase-stepper";
import { ProjectSetup } from "@/components/project-setup";
import { TimingEditor, type ExportOptions } from "@/components/timing-editor";
import type { TimedLine } from "@/lib/cue";
import {
  emptyFields,
  fieldsFromProject,
  fieldsPayload,
  sungCount,
  type Job,
  type Phase,
  type Project,
  type ProjectFields,
  type RestoreSettings,
} from "@/lib/types";

type BrowseKind = "original" | "vocal" | "instrumental" | "cover" | "images" | "output" | "project";

async function readError(response: Response) {
  try {
    const body = await response.json();
    return body.detail || "Yêu cầu không thành công.";
  } catch {
    return "Yêu cầu không thành công.";
  }
}

export function MakerForm() {
  const [phase, setPhase] = useState<Phase>("home");
  const [fields, setFields] = useState<ProjectFields>(() => emptyFields());
  const [hasLines, setHasLines] = useState(false);
  const [notice, setNotice] = useState("");
  const [busyBrowse, setBusyBrowse] = useState("");
  const [job, setJob] = useState<Job | null>(null);

  const aligning = job?.status === "running";
  const rendering = job?.status === "rendering";
  const exported = job?.status === "done";
  // Only highlight Xuất while rendering or right after a finished export.
  // Once the user edits again (status back to editing), return to Biên tập.
  const displayPhase: Phase =
    phase === "edit" && (rendering || exported) ? "export" : phase;

  useEffect(() => {
    fetch("/api/defaults")
      .then((response) => response.json())
      .then((data) => {
        if (data.output_dir) {
          setFields((current) => (current.output_dir ? current : { ...current, output_dir: data.output_dir }));
        }
      })
      .catch(() => {
        setNotice("Chưa kết nối được phần xử lý. Hãy chạy server Python rồi tải lại trang.");
      });
  }, []);

  useEffect(() => {
    if (!job || (job.status !== "running" && job.status !== "rendering")) return;
    const jobId = job.id;
    const timer = setInterval(async () => {
      const response = await fetch(`/api/jobs/${jobId}`);
      if (!response.ok) return;
      const next = (await response.json()) as Job;
      setJob(next);
      if (next.status === "editing") {
        setPhase("edit");
        setHasLines(Boolean(next.lines?.length));
        if (next.project) setFields(fieldsFromProject(next.project));
      }
      if (next.status === "done") setPhase("edit");
      // Keep phase "align" on error so the Ghép chữ screen can show job.error.
    }, 1000);
    return () => clearInterval(timer);
  }, [job?.id, job?.status]);

  async function browse(kind: BrowseKind) {
    setNotice("");
    setBusyBrowse(kind);
    try {
      const response = await fetch("/api/browse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, initial_dir: kind === "project" ? fields.output_dir : "" }),
      });
      if (!response.ok) {
        setNotice(await readError(response));
        return [];
      }
      const data = await response.json();
      return (data.paths || []) as string[];
    } catch {
      setNotice("Không mở được hộp thoại chọn file. Kiểm tra server Python đang chạy.");
      return [];
    } finally {
      setBusyBrowse("");
    }
  }

  async function browseSetup(kind: Exclude<BrowseKind, "project">) {
    const paths = await browse(kind);
    if (paths.length === 0) return;
    if (kind === "images") {
      setFields((current) => ({
        ...current,
        background_images: [...current.background_images, ...paths.filter((path) => !current.background_images.includes(path))],
      }));
      return;
    }
    const selected = paths[0];
    if (kind === "cover") {
      setFields((current) => ({ ...current, cover_image: selected }));
      return;
    }
    if (kind === "output") {
      setFields((current) => ({ ...current, output_dir: selected }));
      return;
    }

    let matched = { vocal: "", instrumental: "", original: "", title: "" };
    try {
      const response = await fetch("/api/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: selected }),
      });
      if (response.ok) matched = await response.json();
    } catch {
      /* keep manual selection */
    }
    setFields((current) => ({
      ...current,
      original_audio: kind === "original" ? selected : current.original_audio || matched.original || "",
      vocal_audio: kind === "vocal" ? selected : current.vocal_audio || matched.vocal || "",
      instrumental_audio: kind === "instrumental" ? selected : current.instrumental_audio || matched.instrumental || "",
      title: current.title || matched.title || "",
    }));
  }

  async function reveal(path: string) {
    await fetch("/api/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  }

  function startCreate() {
    setNotice("");
    setJob(null);
    setHasLines(false);
    setFields((current) => emptyFields(current.output_dir));
    setPhase("setup");
  }

  async function openProject() {
    setNotice("");
    const paths = await browse("project");
    const path = paths[0];
    if (!path) return;
    try {
      const response = await fetch("/api/projects/open", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      if (!response.ok) {
        setNotice(await readError(response));
        return;
      }
      const data = (await response.json()) as {
        phase: "setup" | "edit";
        project: Project;
        job: Job | null;
        notice?: string;
      };
      setFields(fieldsFromProject(data.project));
      setHasLines(Boolean(data.project.lines?.length));
      if (data.notice) setNotice(data.notice);
      if (data.phase === "edit" && data.job) {
        setJob(data.job);
        setPhase("edit");
        return;
      }
      setJob(null);
      setPhase("setup");
    } catch {
      setNotice("Không mở được dự án. Server Python chưa chạy bản mới.");
    }
  }

  function setupReady(requireStems: boolean) {
    if (!fields.title.trim() || !fields.artist.trim()) {
      setNotice("Nhập tên bài hát và tác giả.");
      return false;
    }
    if (!fields.original_audio || (requireStems && (!fields.vocal_audio || !fields.instrumental_audio))) {
      setNotice(requireStems ? "Chọn đủ audio gốc, vocal và instrumental." : "Chọn audio gốc.");
      return false;
    }
    if (sungCount(fields.lyrics) === 0) {
      setNotice("Nhập lời hát. Dòng nằm trọn trong [ngoặc] sẽ được bỏ.");
      return false;
    }
    if (!fields.cover_image && fields.background_images.length === 0) {
      setNotice("Chọn ảnh bìa hoặc ít nhất một ảnh nền.");
      return false;
    }
    if (!fields.output_dir) {
      setNotice("Chọn thư mục xuất.");
      return false;
    }
    return true;
  }

  async function continueRestore(event: FormEvent) {
    event.preventDefault();
    setNotice("");
    if (!setupReady(false)) return;
    try {
      const response = await fetch("/api/restore/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fieldsPayload(fields)),
      });
      if (!response.ok) {
        setNotice(await readError(response));
        return;
      }
      const data = (await response.json()) as { project: Project; job: Job };
      setFields(fieldsFromProject(data.project));
      setJob(data.job);
      setPhase("restore");
    } catch {
      setNotice("Không mở được bước tách và trộn. Server Python chưa chạy.");
    }
  }

  async function submitAlign(event?: FormEvent) {
    event?.preventDefault();
    setNotice("");
    if (!setupReady(true)) return;
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fieldsPayload(fields)),
      });
      if (!response.ok) {
        setNotice(await readError(response));
        return;
      }
      const data = await response.json();
      setPhase("align");
      setJob({
        id: data.id,
        status: "running",
        log: ["Đã gửi việc căn lời."],
        error: "",
        outputs: null,
        lines: null,
        duration: 0,
      });
    } catch {
      setNotice("Không gửi được việc. Server Python chưa chạy.");
    }
  }

  async function resumeEdit() {
    setNotice("");
    if (!fields.project_path) {
      setNotice("Chưa có file dự án để cập nhật.");
      return;
    }
    if (!fields.vocal_audio) {
      setNotice("Cần file vocal để nghe và biên tập.");
      return;
    }
    try {
      const response = await fetch("/api/projects/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(fieldsPayload(fields)),
      });
      if (!response.ok) {
        setNotice(await readError(response));
        return;
      }
      const data = (await response.json()) as { phase: string; project: Project; job: Job | null };
      setFields(fieldsFromProject(data.project));
      setHasLines(Boolean(data.project.lines?.length));
      if (data.job) {
        setJob(data.job);
        setPhase("edit");
        return;
      }
      setNotice("Chưa vào được biên tập. Kiểm tra lại vocal và timing trong dự án.");
    } catch {
      setNotice("Không cập nhật được dự án. Server Python chưa chạy.");
    }
  }

  function resumeEditing() {
    setJob((current) => {
      if (!current || (current.status !== "done" && current.status !== "error")) return current;
      return { ...current, status: "editing", error: "" };
    });
  }

  async function browseExportDir() {
    const paths = await browse("output");
    const selected = paths[0] || null;
    if (selected) {
      setFields((current) => ({ ...current, output_dir: selected }));
    }
    return selected;
  }

  async function renderVideos(nextLines: TimedLine[], options: ExportOptions) {
    if (!job) return;
    setNotice("");
    try {
      const response = await fetch(`/api/jobs/${job.id}/render`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lines: nextLines,
          output_dir: options.output_dir,
          format: options.format,
        }),
      });
      if (!response.ok) {
        setNotice(await readError(response));
        return;
      }
      setFields((current) => ({ ...current, output_dir: options.output_dir || current.output_dir }));
      setJob((current) => (current ? { ...current, status: "rendering", error: "" } : current));
    } catch {
      setNotice("Không gửi được lệnh xuất video. Server Python chưa chạy.");
    }
  }

  return (
    <div>
      <PhaseStepper phase={displayPhase} />

      {phase === "home" && (
        <HomeGate onCreate={startCreate} onOpen={openProject} busy={busyBrowse === "project"} notice={notice} />
      )}

      {phase === "setup" && (
        <ProjectSetup
          fields={fields}
          setFields={setFields}
          notice={notice}
          busyBrowse={busyBrowse}
          aligning={aligning}
          hasLines={hasLines}
          showAlignOptions={!hasLines}
          onBrowse={browseSetup}
          onContinue={continueRestore}
          onAlign={submitAlign}
          onResumeEdit={resumeEdit}
          onBackHome={() => {
            setJob(null);
            setPhase("home");
            setNotice("");
          }}
        />
      )}

      {phase === "restore" && job && (
        <AudioRestore
          job={job}
          notice={notice}
          onJob={(next) => {
            setJob(next);
            if (next.project) setFields(fieldsFromProject(next.project));
          }}
          onSeparate={async () => {
            setNotice("");
            const response = await fetch(`/api/restore/${job.id}/separate`, { method: "POST" });
            if (!response.ok) {
              setNotice(await readError(response));
              return;
            }
            setJob((current) => (current ? { ...current, status: "separating", error: "" } : current));
          }}
          onPreview={async (settings: RestoreSettings) => {
            setNotice("");
            const response = await fetch(`/api/restore/${job.id}/preview`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(settings),
            });
            if (!response.ok) {
              setNotice(await readError(response));
              return false;
            }
            return true;
          }}
          onExport={async (settings: RestoreSettings, format: string) => {
            setNotice("");
            const response = await fetch(`/api/restore/${job.id}/export`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ ...settings, format }),
            });
            if (!response.ok) {
              setNotice(await readError(response));
              return false;
            }
            const data = (await response.json()) as { project: Project };
            setFields(fieldsFromProject(data.project));
            setJob((current) =>
              current
                ? {
                    ...current,
                    project: data.project,
                    restore: data.project.restore,
                    status: "ready",
                    log: [...current.log, `Đã xuất nhạc nền.`],
                  }
                : current,
            );
            return true;
          }}
          onAlign={() => submitAlign()}
          onBack={() => {
            setNotice("");
            setPhase("setup");
          }}
        />
      )}

      {phase === "align" && job && (
        <section className="mx-auto max-w-2xl rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-6">
          <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Ghép chữ</p>
          <h2 className="mt-2 text-xl font-semibold">Đang căn lời vào vocal…</h2>
          <ol className="mt-5 max-h-[28rem] space-y-2 overflow-auto text-sm leading-6 text-[var(--muted)]">
            {job.log.map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ol>
          {job.status === "error" && (
            <div className="mt-4 space-y-3">
              <p className="text-sm text-red-200">{job.error}</p>
              <button
                type="button"
                onClick={() => setPhase("setup")}
                className="rounded-2xl border border-[var(--gold)] px-4 py-2 text-sm text-[var(--gold)]"
              >
                Quay lại khai báo
              </button>
            </div>
          )}
        </section>
      )}

      {phase === "edit" && job?.lines && (
        <TimingEditor
          key={job.id}
          jobId={job.id}
          initialLines={job.lines}
          locked={rendering}
          exported={exported}
          error={job.status === "error" ? job.error : notice}
          log={job.log}
          outputs={job.outputs}
          defaultOutputDir={fields.output_dir || job.outputs?.directory || ""}
          onRender={renderVideos}
          onReveal={reveal}
          onResumeEditing={resumeEditing}
          onBrowseOutput={browseExportDir}
          onBack={() => {
            setNotice("");
            setPhase("setup");
          }}
          onFillGaps={() => {
            setNotice("Bổ sung các file còn thiếu rồi bấm lưu khai báo và vào biên tập.");
            setPhase("setup");
          }}
          exportGaps={job.export_gaps || []}
        />
      )}
    </div>
  );
}
