"use client";

import { useEffect, useRef, useState } from "react";

import { AiEffects } from "@/components/ai-effects";
import { ImageCards } from "@/components/image-cards";
import type { BackgroundEffect, StudioAsset } from "@/lib/types";

type PreviewResult = {
  id: string;
  url: string;
  width: number;
  height: number;
  duration: number;
};

const fieldClass =
  "w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm outline-none transition focus:border-[var(--gold)]";

const PROFILE_OPTIONS = [
  { value: "landscape_1080p", label: "Ngang 16:9 · 1920×1080" },
  { value: "portrait_1080x1920", label: "Dọc 9:16 · 1080×1920" },
  { value: "square_1080", label: "Vuông 1:1 · 1080×1080" },
  { value: "portrait_4x5", label: "Dọc 4:5 · 1080×1350" },
];

const PROFILE_SIZE: Record<string, string> = {
  landscape_1080p: "1920×1080",
  portrait_1080x1920: "1080×1920",
  square_1080: "1080×1080",
  portrait_4x5: "1080×1350",
};

function fileName(path: string) {
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

async function readError(response: Response) {
  const text = await response.text();
  try {
    const body = JSON.parse(text) as { detail?: unknown; message?: unknown };
    const detail = body.detail ?? body.message;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join("; ");
    }
    if (detail) return JSON.stringify(detail);
  } catch {
    /* not JSON: fall through */
  }
  if (text.trim() && text.length < 300 && !text.includes("<html")) return text.trim();
  return `Yêu cầu không thành công (${response.status}). Xem terminal Python để xem traceback.`;
}

function effectNames(effect: BackgroundEffect) {
  const names = (effect.effect_plan?.items || []).map((item) => item.effect).filter(Boolean);
  return names.length > 0 ? names.join(", ") : "chưa có hiệu ứng";
}

export function StudioWorkspace() {
  const [images, setImages] = useState<string[]>([]);
  const [effects, setEffects] = useState<BackgroundEffect[]>([]);
  const [profile, setProfile] = useState("landscape_1080p");
  const [standardDuration, setStandardDuration] = useState(8);
  const [busyBrowse, setBusyBrowse] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [renderingPath, setRenderingPath] = useState("");
  const [renderingMode, setRenderingMode] = useState<"preview" | "standard" | "">("");
  const [renderHint, setRenderHint] = useState("");
  const [previews, setPreviews] = useState<Record<string, PreviewResult>>({});
  const [savingPath, setSavingPath] = useState("");
  const [assets, setAssets] = useState<StudioAsset[]>([]);
  const [assetsError, setAssetsError] = useState("");
  const [resumeAsset, setResumeAsset] = useState<{ asset: StudioAsset; token: number } | null>(null);
  const renderAbortRef = useRef<AbortController | null>(null);
  const workRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    return () => {
      renderAbortRef.current?.abort();
    };
  }, []);

  async function loadAssets() {
    try {
      const response = await fetch("/api/studio/assets");
      if (!response.ok) {
        setAssetsError(await readError(response));
        return;
      }
      const data = (await response.json()) as { assets?: StudioAsset[] };
      setAssets(Array.isArray(data.assets) ? data.assets : []);
      setAssetsError("");
    } catch {
      setAssetsError("Không tải được asset đã lưu. Kiểm tra server Python.");
    }
  }

  useEffect(() => {
    void loadAssets();
  }, []);

  function effectFromAsset(asset: StudioAsset): BackgroundEffect | null {
    const plan = asset.effect_plan;
    if (!asset.source_image || !plan) return null;
    return {
      image_path: asset.source_image,
      image_hash: asset.image_hash,
      effect_prompt: `Asset ${asset.name}`,
      effect_consent_receipt: {
        timestamp: asset.created || new Date().toISOString(),
        image_hash: asset.image_hash,
        provider: "studio",
        model: "asset",
      },
      selected_object_ids: plan.selected_object_ids || [],
      effect_plan: plan,
      model: "studio",
      created: asset.created || new Date().toISOString(),
      asset_id: asset.id,
    };
  }

  async function openAsset(asset: StudioAsset) {
    setNotice("");
    let opened = asset;
    try {
      const response = await fetch(`/api/studio/assets/${asset.id}/open`, { method: "POST" });
      if (!response.ok) {
        setNotice(await readError(response));
        return;
      }
      const data = (await response.json()) as { asset?: StudioAsset };
      if (data.asset) opened = data.asset;
    } catch {
      setNotice("Không mở được asset. Kiểm tra server Python đang chạy.");
      return;
    }
    const effect = effectFromAsset(opened);
    if (!effect) {
      setNotice("Asset này thiếu ảnh nguồn hoặc effect plan nên chưa mở chỉnh sửa được.");
      return;
    }
    setImages((current) =>
      current.includes(opened.source_image) ? current : [...current, opened.source_image],
    );
    if (opened.profile_id && PROFILE_OPTIONS.some((option) => option.value === opened.profile_id)) {
      setProfile(opened.profile_id);
    }
    if (opened.asset_frames && opened.fps) {
      const seconds = opened.asset_frames / opened.fps;
      setStandardDuration(Math.max(0.5, Math.min(30, Number(seconds.toFixed(1)))));
    }
    setEffects((current) => [
      ...current.filter((item) => item.image_path !== opened.source_image),
      effect,
    ]);
    setPreviews({});
    setResumeAsset({ asset: opened, token: Date.now() });
    window.setTimeout(() => {
      workRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 50);
  }

  async function pickImages() {
    if (busyBrowse) return;
    setBusyBrowse(true);
    setNotice("");
    try {
      const response = await fetch("/api/browse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "images", initial_dir: "" }),
      });
      if (!response.ok) {
        setNotice(await readError(response));
        return;
      }
      const data = (await response.json()) as { paths?: string[] };
      const picked = (data.paths || []).filter(Boolean);
      if (picked.length === 0) return;
      setImages((current) => [...current, ...picked.filter((path) => !current.includes(path))]);
    } catch {
      setNotice("Không mở được hộp thoại chọn ảnh. Kiểm tra server Python đang chạy.");
    } finally {
      setBusyBrowse(false);
    }
  }

  function removeImage(path: string) {
    setImages((current) => current.filter((item) => item !== path));
    setEffects((current) => current.filter((item) => item.image_path !== path));
    setPreviews((current) => {
      const next = { ...current };
      delete next[path];
      return next;
    });
  }

  function changeProfile(value: string) {
    if (renderingPath) return;
    setProfile(value);
    // A preview is tied to the output profile and the plan; drop stale videos.
    setPreviews({});
    setError("");
  }

  function changeStandardDuration(value: number) {
    if (renderingPath) return;
    setStandardDuration(value);
    // A saved preview is tied to its duration; drop stale videos.
    setPreviews({});
    setError("");
  }

  async function renderPreview(effect: BackgroundEffect) {
    if (renderingPath) return;
    setRenderingPath(effect.image_path);
    setRenderingMode("preview");
    setRenderHint("");
    setError("");
    const controller = new AbortController();
    renderAbortRef.current = controller;
    try {
      const response = await fetch("/api/studio/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_path: effect.image_path,
          effect_plan: effect.effect_plan,
          duration: standardDuration,
          output_profile: profile,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        setError(await readError(response));
        return;
      }
      const data = (await response.json()) as PreviewResult;
      setPreviews((current) => {
        const existing = current[effect.image_path];
        if (existing && existing.duration >= standardDuration && data.duration < standardDuration) {
          return current;
        }
        return { ...current, [effect.image_path]: data };
      });
    } catch {
      if (!controller.signal.aborted) {
        setError("Không kết nối được server render. Kiểm tra server Python đang chạy rồi thử lại.");
      }
    } finally {
      if (renderAbortRef.current === controller) renderAbortRef.current = null;
      setRenderingPath("");
      setRenderingMode("");
    }
  }

  async function renderStandard(effect: BackgroundEffect) {
    if (renderingPath) return;
    setRenderingPath(effect.image_path);
    setRenderingMode("standard");
    setRenderHint(
      `Đang render bản chuẩn ${standardDuration}s ở ${PROFILE_SIZE[profile] || profile}; có thể mất 1–2 phút.`,
    );
    setError("");
    const controller = new AbortController();
    renderAbortRef.current = controller;
    try {
      const response = await fetch("/api/studio/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_path: effect.image_path,
          effect_plan: effect.effect_plan,
          duration: standardDuration,
          output_profile: profile,
          full_resolution: true,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        setError(await readError(response));
        return;
      }
      let data = (await response.json()) as PreviewResult | { job_id?: string };
      if ("job_id" in data && data.job_id) {
        const jobId = data.job_id;
        // Full renders run in a background job; poll until done.
        for (let attempt = 0; attempt < 480; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 1500));
          const statusResponse = await fetch(`/api/studio/render-job/${jobId}`, {
            signal: controller.signal,
          });
          if (statusResponse.status === 404) {
            setError(`Không thấy job render (server có thể đã restart). Bấm lại nút Bản chuẩn ${standardDuration}s.`);
            return;
          }
          if (!statusResponse.ok) {
            setError(await readError(statusResponse));
            return;
          }
          const status = (await statusResponse.json()) as {
            status: string;
            result?: PreviewResult;
            error?: string;
            percent?: number;
          };
          if (typeof status.percent === "number" && status.percent > 0) {
            setRenderHint(
              `Đang render bản chuẩn ${standardDuration}s ở ${PROFILE_SIZE[profile] || profile}: ${status.percent}%`,
            );
          }
          if (status.status === "done" && status.result) {
            setPreviews((current) => ({ ...current, [effect.image_path]: status.result! }));
            setRenderHint("");
            return;
          }
          if (status.status === "error") {
            setError(status.error || "Render bản chuẩn thất bại.");
            return;
          }
        }
        setError("Render bản chuẩn quá lâu. Xem terminal Python để kiểm tra.");
        return;
      }
      if ("url" in data && data.url) {
        setPreviews((current) => ({ ...current, [effect.image_path]: data as PreviewResult }));
        setRenderHint("");
      }
    } catch {
      if (!controller.signal.aborted) {
        setError("Không kết nối được server render. Kiểm tra server Python đang chạy rồi thử lại.");
      }
    } finally {
      if (renderAbortRef.current === controller) renderAbortRef.current = null;
      setRenderingPath("");
      setRenderingMode("");
    }
  }

  async function saveAsset(effect: BackgroundEffect) {
    const preview = previews[effect.image_path];
    if (!preview || savingPath) return;
    const suggested = fileName(effect.image_path).replace(/\.[^.]+$/, "");
    const name = window.prompt("Tên asset", suggested);
    if (name === null) return;
    setSavingPath(effect.image_path);
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/studio/assets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preview_id: preview.id,
          name,
          image_path: effect.image_path,
          image_hash: effect.image_hash,
          effect_plan: effect.effect_plan,
          duration: preview.duration,
          output_profile: profile,
        }),
      });
      if (!response.ok) {
        setError(await readError(response));
        return;
      }
      const data = (await response.json()) as { asset: { id: string; name: string } };
      setNotice(`Đã lưu asset “${data.asset.name}”. Asset sẽ hiện trong phần Hiệu ứng nền của dự án Karaoke.`);
      void loadAssets();
    } catch {
      setError("Không lưu được asset. Kiểm tra server Python đang chạy.");
    } finally {
      setSavingPath("");
    }
  }

  return (
    <div className="space-y-6">
      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(300px,0.9fr)]">
      <section ref={workRef} className="scroll-mt-4 space-y-5 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Ảnh nền</p>
            <h2 className="mt-1 text-xl font-semibold">Chọn ảnh để làm nền sống</h2>
          </div>
          <button
            type="button"
            onClick={pickImages}
            disabled={busyBrowse}
            className="rounded-xl border border-[var(--line)] px-3 py-2 text-sm transition hover:border-[var(--gold)] disabled:opacity-60"
          >
            {busyBrowse ? "Đang mở hộp thoại…" : "Chọn ảnh"}
          </button>
        </div>

        <p className="text-sm leading-6 text-[var(--muted)]">
          Ảnh được chọn qua hộp thoại của máy. Preview chỉ dựng hiệu ứng; bấm Lưu thành asset để dùng clip trong dự án Karaoke.
        </p>

        {images.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-[var(--line)] bg-black/15 p-4 text-sm leading-6 text-[var(--muted)]">
            Chưa có ảnh. Bấm “Chọn ảnh” để thêm một hoặc nhiều ảnh tĩnh.
          </div>
        ) : (
          <ImageCards paths={images} onRemove={removeImage} outputProfile={profile} />
        )}

        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Định dạng preview</span>
          <select
            className={fieldClass}
            value={profile}
            disabled={Boolean(renderingPath)}
            onChange={(event) => changeProfile(event.target.value)}
          >
            {PROFILE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Thời lượng bản chuẩn</span>
          <select
            className={fieldClass}
            value={standardDuration}
            disabled={Boolean(renderingPath)}
            onChange={(event) => changeStandardDuration(Number(event.target.value))}
          >
            <option value={8}>8 giây</option>
            <option value={16}>16 giây</option>
            {standardDuration !== 8 && standardDuration !== 16 && (
              <option value={standardDuration}>{standardDuration} giây (theo asset)</option>
            )}
          </select>
        </label>

        {notice && <p className="rounded-xl bg-red-950/60 px-3 py-2 text-sm text-red-100">{notice}</p>}

        <div className="space-y-3 border-t border-[var(--line)] pt-5">
          <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Hiệu ứng (AI)</h2>
          <AiEffects
            images={images}
            effects={effects}
            outputProfile={profile}
            imageDuration={String(standardDuration)}
            resumeAsset={resumeAsset}
            onAssetSaved={() => void loadAssets()}
            onEffectsChange={(updater) => {
              setEffects((current) => updater(current));
              // The old preview belongs to the previous plan; never show it
              // as if it reflected the newly applied effects.
              setPreviews({});
              setResumeAsset(null);
            }}
          />
        </div>
      </section>

      <aside className="space-y-4 lg:sticky lg:top-6">
        <section className="space-y-3 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Preview</h2>
            <span className="text-xs text-[var(--muted)]">{standardDuration}s</span>
          </div>

          {effects.length === 0 ? (
            <p className="text-sm leading-6 text-[var(--muted)]">
              Chưa có hiệu ứng. Phân tích một ảnh ở cột bên trái rồi bấm “Áp dụng”.
            </p>
          ) : (
            <ul className="space-y-3">
              {effects.map((effect) => {
                const preview = previews[effect.image_path];
                const rendering = renderingPath === effect.image_path;
                return (
                  <li
                    key={effect.image_path}
                    className="space-y-2 rounded-2xl border border-[var(--line)] bg-black/20 p-3"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm">{fileName(effect.image_path)}</p>
                      <p className="text-xs leading-5 text-[var(--muted)]">{effectNames(effect)}</p>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <button
                        type="button"
                        onClick={() => renderPreview(effect)}
                        disabled={Boolean(renderingPath)}
                        className="rounded-xl border border-[var(--gold)] px-3 py-2 text-sm font-semibold text-[var(--gold)] transition hover:bg-black/25 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {rendering && renderingMode === "preview" ? "Đang render…" : `Preview ${standardDuration}s`}
                      </button>
                      <button
                        type="button"
                        onClick={() => renderStandard(effect)}
                        disabled={Boolean(renderingPath)}
                        className="rounded-xl border border-[var(--line)] px-3 py-2 text-sm font-semibold text-[var(--muted)] transition hover:border-[var(--gold)] hover:text-[var(--gold)] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {rendering && renderingMode === "standard"
                          ? "Đang render…"
                          : `Bản chuẩn ${standardDuration}s`}
                      </button>
                    </div>
                    {preview && (
                      <div className="space-y-1.5">
                        <video
                          key={preview.url}
                          src={preview.url}
                          controls
                          autoPlay
                          loop
                          muted
                          playsInline
                          className="w-full rounded-xl border border-[var(--line)] bg-black"
                        />
                        <p className="text-xs text-[var(--muted)]">
                          {preview.width}×{preview.height} · {preview.duration}s
                        </p>
                        <button
                          type="button"
                          onClick={() => saveAsset(effect)}
                          disabled={Boolean(savingPath) || preview.duration < standardDuration}
                          className="w-full rounded-xl border border-[var(--gold)] px-3 py-2 text-sm font-semibold text-[var(--gold)] transition hover:bg-black/25 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {savingPath === effect.image_path
                            ? "Đang lưu asset…"
                            : preview.duration < standardDuration
                              ? `Render bản chuẩn ${standardDuration}s để lưu asset`
                              : "Lưu thành asset"}
                        </button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          {renderingPath && (
            <p className="text-xs leading-5 text-[var(--muted)]">
              FFmpeg đang dựng vòng hiệu ứng; lần đầu có thể mất vài chục giây.
            </p>
          )}
          {renderHint && (
            <p className="rounded-xl border border-[var(--line)] bg-black/20 px-3 py-2 text-xs leading-5 text-[var(--muted)]">
              {renderHint}
            </p>
          )}
          {error && (
            <p className="whitespace-pre-line rounded-xl border border-red-900 bg-red-950/60 px-3 py-2 text-sm leading-5 text-red-100">
              {error}
            </p>
          )}
        </section>

        <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5 text-xs leading-5 text-[var(--muted)]">
          Preview dùng cùng thời lượng với bản chuẩn ({standardDuration}s) ở độ phân giải thấp để tốc độ gió khớp.
          Render bản chuẩn rồi bấm “Lưu thành asset” để dùng clip đó trong phần Hiệu ứng nền của dự án Karaoke.
        </section>
      </aside>
      </div>

      <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Thư viện</p>
            <h2 className="mt-1 text-xl font-semibold">Asset đã lưu</h2>
            <p className="mt-1 text-sm leading-6 text-[var(--muted)]">
              Bấm “Mở để sửa” để nạp lại ảnh, effect plan, object và các tag thủ công vào Studio.
            </p>
          </div>
          <button
            type="button"
            onClick={() => void loadAssets()}
            className="rounded-xl border border-[var(--line)] px-3 py-2 text-sm text-[var(--muted)] hover:border-[var(--gold)]"
          >
            Tải lại
          </button>
        </div>
        {assetsError && <p className="mt-3 text-sm text-red-200">{assetsError}</p>}
        {assets.length === 0 ? (
          <p className="mt-3 rounded-2xl border border-dashed border-[var(--line)] p-4 text-sm leading-6 text-[var(--muted)]">
            Chưa có asset nào. Render bản chuẩn rồi bấm “Lưu thành asset”.
          </p>
        ) : (
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {assets.map((asset) => {
              const plan = asset.effect_plan;
              const effectList = (plan?.items || []).map((item) => item.effect).filter(Boolean);
              const manualLabels = (plan?.objects || [])
                .filter((object) => object.source === "manual")
                .map((object) => object.label);
              const seconds = asset.asset_frames && asset.fps ? asset.asset_frames / asset.fps : asset.loop_duration;
              return (
                <article
                  key={asset.id}
                  className="flex min-w-0 flex-col gap-2 rounded-2xl border border-[var(--line)] bg-black/20 p-3"
                >
                  <video
                    src={`/api/studio/assets/${asset.id}/clip`}
                    muted
                    loop
                    playsInline
                    preload="metadata"
                    className="aspect-video w-full rounded-xl border border-[var(--line)] bg-black object-cover"
                  />
                  <div className="flex items-start justify-between gap-2">
                    <p className="min-w-0 truncate text-sm font-semibold">{asset.name}</p>
                    <span className="shrink-0 text-xs text-[var(--muted)]">
                      {typeof seconds === "number" ? `${seconds.toFixed(1)}s` : ""}
                    </span>
                  </div>
                  <p className="truncate text-xs text-[var(--muted)]">
                    {fileName(asset.source_image)} · {asset.profile_id || "profile?"}
                  </p>
                  <p className="text-xs leading-5 text-[var(--muted)]">
                    Hiệu ứng: {effectList.length ? effectList.join(", ") : "—"}
                  </p>
                  {manualLabels.length > 0 && (
                    <p className="text-xs leading-5 text-[var(--muted)]">
                      Object thủ công: {manualLabels.join(", ")}
                    </p>
                  )}
                  <p className="text-[11px] text-[var(--muted)]">
                    {asset.created ? new Date(asset.created).toLocaleString("vi-VN") : ""}
                  </p>
                  <button
                    type="button"
                    onClick={() => openAsset(asset)}
                    className="mt-auto rounded-xl border border-[var(--gold)] px-3 py-2 text-sm font-semibold text-[var(--gold)] hover:bg-black/25"
                  >
                    Mở để sửa
                  </button>
                </article>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
