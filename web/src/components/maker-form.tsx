"use client";

import { useEffect, useMemo, useState, type FormEvent } from "react";

type Outputs = {
  directory: string;
  vocal: string;
  instrumental: string;
  timing: string;
};

type Job = {
  id: string;
  status: "running" | "done" | "error";
  log: string[];
  error: string;
  outputs: Outputs | null;
};

type BrowseKind = "original" | "vocal" | "instrumental" | "cover" | "images" | "output";

const fieldClass =
  "w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm outline-none transition focus:border-[var(--gold)]";

function fileName(path: string) {
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

function sungCount(lyrics: string) {
  return lyrics
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !(line.startsWith("[") && line.endsWith("]"))).length;
}

async function readError(response: Response) {
  try {
    const body = await response.json();
    return body.detail || "Yêu cầu không thành công.";
  } catch {
    return "Yêu cầu không thành công.";
  }
}

function PathField({
  label,
  value,
  placeholder,
  onBrowse,
}: {
  label: string;
  value: string;
  placeholder: string;
  onBrowse: () => void;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm text-[var(--muted)]">{label}</span>
      <div className="flex gap-2">
        <input className={fieldClass} value={value} placeholder={placeholder} readOnly />
        <button
          type="button"
          onClick={onBrowse}
          className="shrink-0 rounded-xl border border-[var(--line)] px-3 text-sm hover:border-[var(--gold)]"
        >
          Chọn
        </button>
      </div>
    </label>
  );
}

export function MakerForm() {
  const [title, setTitle] = useState("");
  const [artist, setArtist] = useState("");
  const [coverLine, setCoverLine] = useState("");
  const [originalAudio, setOriginalAudio] = useState("");
  const [vocalAudio, setVocalAudio] = useState("");
  const [instrumentalAudio, setInstrumentalAudio] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [images, setImages] = useState<string[]>([]);
  const [coverImage, setCoverImage] = useState("");
  const [outputDir, setOutputDir] = useState("");
  const [coverDuration, setCoverDuration] = useState("5");
  const [imageDuration, setImageDuration] = useState("8");
  const [modelName, setModelName] = useState("small");
  const [device, setDevice] = useState("cpu");
  const [notice, setNotice] = useState("");
  const [busyBrowse, setBusyBrowse] = useState("");
  const [job, setJob] = useState<Job | null>(null);

  const lines = useMemo(() => sungCount(lyrics), [lyrics]);
  const running = job?.status === "running";

  useEffect(() => {
    fetch("/api/defaults")
      .then((response) => response.json())
      .then((data) => {
        if (data.output_dir) setOutputDir(data.output_dir);
      })
      .catch(() => {
        setNotice("Chưa kết nối được phần xử lý. Hãy chạy server Python rồi tải lại trang.");
      });
  }, []);

  useEffect(() => {
    if (!job || job.status !== "running") return;
    const timer = setInterval(async () => {
      const response = await fetch(`/api/jobs/${job.id}`);
      if (!response.ok) return;
      const next = (await response.json()) as Job;
      setJob(next);
    }, 1000);
    return () => clearInterval(timer);
  }, [job]);

  async function browse(kind: BrowseKind) {
    setNotice("");
    setBusyBrowse(kind);
    try {
      const response = await fetch("/api/browse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind }),
      });
      if (!response.ok) {
        setNotice(await readError(response));
        return;
      }
      const data = await response.json();
      const paths = (data.paths || []) as string[];
      if (paths.length === 0) return;
      if (kind === "images") {
        setImages((current) => [...current, ...paths.filter((path) => !current.includes(path))]);
        return;
      }
      const selected = paths[0];
      if (kind === "cover") setCoverImage(selected);
      if (kind === "output") setOutputDir(selected);
      if (kind === "original") setOriginalAudio(selected);
      if (kind === "vocal") setVocalAudio(selected);
      if (kind === "instrumental") setInstrumentalAudio(selected);
      if (kind === "original" || kind === "vocal" || kind === "instrumental") {
        await fillFromAudio(selected);
      }
    } catch {
      setNotice("Không mở được hộp thoại chọn file. Kiểm tra server Python đang chạy.");
    } finally {
      setBusyBrowse("");
    }
  }

  async function fillFromAudio(path: string) {
    const response = await fetch("/api/match", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!response.ok) return;
    const data = await response.json();
    if (data.vocal) setVocalAudio((current) => current || data.vocal);
    if (data.instrumental) setInstrumentalAudio((current) => current || data.instrumental);
    if (data.original) setOriginalAudio((current) => current || data.original);
    if (data.title) setTitle((current) => current || data.title);
  }

  async function reveal(path: string) {
    await fetch("/api/reveal", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setNotice("");
    if (!title.trim() || !artist.trim()) {
      setNotice("Nhập tên bài hát và tác giả.");
      return;
    }
    if (!originalAudio || !vocalAudio || !instrumentalAudio) {
      setNotice("Chọn đủ audio gốc, vocal và instrumental.");
      return;
    }
    if (lines === 0) {
      setNotice("Nhập lời hát. Dòng nằm trọn trong [ngoặc] sẽ được bỏ.");
      return;
    }
    if (!coverImage && images.length === 0) {
      setNotice("Chọn ảnh bìa hoặc ít nhất một ảnh nền.");
      return;
    }
    if (!outputDir) {
      setNotice("Chọn thư mục xuất.");
      return;
    }
    try {
      const response = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title,
          artist,
          cover_line: coverLine,
          original_audio: originalAudio,
          vocal_audio: vocalAudio,
          instrumental_audio: instrumentalAudio,
          lyrics,
          background_images: images,
          cover_image: coverImage,
          output_dir: outputDir,
          cover_duration: Number(coverDuration),
          image_duration: Number(imageDuration),
          language: "vi",
          model_name: modelName,
          device,
        }),
      });
      if (!response.ok) {
        setNotice(await readError(response));
        return;
      }
      const data = await response.json();
      setJob({ id: data.id, status: "running", log: ["Đã gửi việc."], error: "", outputs: null });
    } catch {
      setNotice("Không gửi được việc. Server Python chưa chạy.");
    }
  }

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]">
      <form onSubmit={submit} className="space-y-5 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5 sm:p-6">
        <section className="grid gap-4 sm:grid-cols-2">
          <label className="block sm:col-span-2">
            <span className="mb-1.5 block text-sm text-[var(--muted)]">Tên bài hát</span>
            <input className={fieldClass} value={title} onChange={(event) => setTitle(event.target.value)} />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm text-[var(--muted)]">Tác giả</span>
            <input className={fieldClass} value={artist} onChange={(event) => setArtist(event.target.value)} />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm text-[var(--muted)]">Dòng phụ trên bìa</span>
            <input className={fieldClass} value={coverLine} onChange={(event) => setCoverLine(event.target.value)} placeholder="Không bắt buộc" />
          </label>
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Audio</h2>
          <PathField label="Audio gốc" value={originalAudio} placeholder="Bài hát đầy đủ" onBrowse={() => browse("original")} />
          <PathField label="Vocal" value={vocalAudio} placeholder="File _(Vocals)" onBrowse={() => browse("vocal")} />
          <PathField label="Instrumental" value={instrumentalAudio} placeholder="File _(Instrumental)" onBrowse={() => browse("instrumental")} />
          <p className="text-xs leading-5 text-[var(--muted)]">
            Chọn một file vocal hoặc instrumental, ứng dụng sẽ tìm file còn lại và bài gốc cùng tên.
          </p>
        </section>

        <section>
          <div className="mb-1.5 flex items-center justify-between">
            <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Lời</h2>
            <span className="text-xs text-[var(--muted)]">{lines} câu hát</span>
          </div>
          <textarea
            className={`${fieldClass} min-h-52 resize-y leading-6`}
            value={lyrics}
            onChange={(event) => setLyrics(event.target.value)}
            placeholder={"Dán lời vào đây.\nDòng [Verse], [Intro] và ghi chú trong ngoặc sẽ không hiện trên video."}
          />
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Hình</h2>
          <PathField label="Ảnh bìa" value={coverImage} placeholder="Ảnh phía sau tên bài" onBrowse={() => browse("cover")} />
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-sm text-[var(--muted)]">Ảnh nền, lặp đến hết bài</span>
              <button type="button" onClick={() => browse("images")} className="text-sm text-[var(--gold)]">
                Thêm ảnh
              </button>
            </div>
            <ul className="space-y-2">
              {images.length === 0 && <li className="text-sm text-[var(--muted)]">Chưa có ảnh nền. Nếu bỏ trống, ảnh bìa sẽ được lặp lại.</li>}
              {images.map((image) => (
                <li key={image} className="flex items-center justify-between gap-3 rounded-xl border border-[var(--line)] px-3 py-2 text-sm">
                  <span className="truncate">{fileName(image)}</span>
                  <button type="button" className="text-[var(--muted)] hover:text-white" onClick={() => setImages((current) => current.filter((item) => item !== image))}>
                    Xóa
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-sm text-[var(--muted)]">Bìa (giây)</span>
              <input className={fieldClass} type="number" min="0" step="0.5" value={coverDuration} onChange={(event) => setCoverDuration(event.target.value)} />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm text-[var(--muted)]">Mỗi ảnh nền (giây)</span>
              <input className={fieldClass} type="number" min="1" step="0.5" value={imageDuration} onChange={(event) => setImageDuration(event.target.value)} />
            </label>
          </div>
        </section>

        <section className="grid gap-4 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1.5 block text-sm text-[var(--muted)]">Model căn lời</span>
            <select className={fieldClass} value={modelName} onChange={(event) => setModelName(event.target.value)}>
              <option value="tiny">Tiny, nhanh</option>
              <option value="base">Base</option>
              <option value="small">Small, cân bằng</option>
              <option value="medium">Medium, chậm hơn</option>
            </select>
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm text-[var(--muted)]">Thiết bị</span>
            <select className={fieldClass} value={device} onChange={(event) => setDevice(event.target.value)}>
              <option value="cpu">CPU</option>
              <option value="mps">GPU Apple</option>
            </select>
          </label>
          <div className="sm:col-span-2">
            <PathField label="Thư mục xuất" value={outputDir} placeholder="Nơi lưu hai video" onBrowse={() => browse("output")} />
          </div>
        </section>

        {notice && <p className="rounded-xl bg-red-950/60 px-3 py-2 text-sm text-red-100">{notice}</p>}
        {busyBrowse && <p className="text-sm text-[var(--muted)]">Đang mở hộp thoại trên máy…</p>}

        <button
          type="submit"
          disabled={running}
          className="w-full rounded-2xl bg-[var(--gold)] px-4 py-3 text-sm font-semibold text-[#1a1206] disabled:opacity-60"
        >
          {running ? "Đang tạo video…" : "Tạo hai video karaoke"}
        </button>
      </form>

      <aside className="space-y-4 lg:sticky lg:top-6">
        <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Tiến trình</h2>
          <ol className="mt-4 max-h-[28rem] space-y-2 overflow-auto text-sm leading-6 text-[var(--muted)]">
            {(job?.log || ["Chưa chạy."]).map((line, index) => (
              <li key={`${index}-${line}`}>{line}</li>
            ))}
          </ol>
          {job?.status === "error" && <p className="mt-3 text-sm text-red-200">{job.error}</p>}
        </section>
        {job?.status === "done" && job.outputs && (
          <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
            <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Đã xuất</h2>
            <div className="mt-4 space-y-3 text-sm">
              <button type="button" className="block text-left hover:text-[var(--gold)]" onClick={() => reveal(job.outputs!.vocal)}>
                {fileName(job.outputs.vocal)}
              </button>
              <button type="button" className="block text-left hover:text-[var(--gold)]" onClick={() => reveal(job.outputs!.instrumental)}>
                {fileName(job.outputs.instrumental)}
              </button>
              <button type="button" className="text-[var(--gold)]" onClick={() => reveal(job.outputs!.directory)}>
                Mở thư mục
              </button>
            </div>
          </section>
        )}
      </aside>
    </div>
  );
}
