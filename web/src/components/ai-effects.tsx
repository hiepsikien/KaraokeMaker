"use client";

import { useEffect, useState } from "react";

import type {
  BackgroundEffect,
  DirectorAnalysis,
  DirectorBox,
  DirectorPoint,
  DirectorUsage,
} from "@/lib/types";

type InspectFact = {
  path: string;
  width: number;
  height: number;
  ratio: string;
  fits_frame: boolean;
  note: string;
  thumbnail: string;
  preview?: string;
};

const QUICK_PROMPTS = ["mưa nhẹ", "khói bát mỳ", "gió đèn lồng", "sóng biển"];

const PROFILE_LABELS: Record<string, string> = {
  landscape_1080p: "Ngang 16:9",
  portrait_1080x1920: "Dọc 9:16",
  square_1080: "Vuông 1:1",
  portrait_4x5: "Dọc 4:5",
};

function profileLabel(id: string) {
  return PROFILE_LABELS[id] || PROFILE_LABELS.landscape_1080p;
}

const fieldClass =
  "w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm outline-none transition focus:border-[var(--gold)]";

function fileName(path: string) {
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

async function readError(response: Response) {
  try {
    const body = (await response.json()) as { detail?: unknown; message?: unknown };
    const detail = body.detail ?? body.message;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (Array.isArray(detail)) {
      return detail.map((item) => (typeof item === "string" ? item : JSON.stringify(item))).join("; ");
    }
    if (detail) return JSON.stringify(detail);
  } catch {
    /* fall through to the status message */
  }
  return `Yêu cầu không thành công (${response.status}).`;
}

function usageText(usage: DirectorUsage) {
  const parts: string[] = [];
  if (typeof usage.prompt === "number") parts.push(`vào ${usage.prompt}`);
  if (typeof usage.output === "number") parts.push(`ra ${usage.output}`);
  if (typeof usage.thoughts === "number") parts.push(`suy luận ${usage.thoughts}`);
  if (typeof usage.total === "number") parts.push(`tổng ${usage.total}`);
  return parts.length > 0 ? `Token · ${parts.join(" · ")}` : "";
}

function boxRect(box: DirectorBox, width: number, height: number) {
  return {
    x: box.x1 * width,
    y: box.y1 * height,
    width: Math.max(0, (box.x2 - box.x1) * width),
    height: Math.max(0, (box.y2 - box.y1) * height),
  };
}

function labelY(y: number, fontSize: number) {
  return Math.max(fontSize, y - fontSize * 0.4);
}

function Anchor({
  point,
  width,
  height,
  radius,
  color,
  label,
}: {
  point: DirectorPoint;
  width: number;
  height: number;
  radius: number;
  color: string;
  label: string;
}) {
  const cx = point.x * width;
  const cy = point.y * height;
  const stroke = Math.max(1.5, radius / 3);
  return (
    <g aria-label={label}>
      <circle cx={cx} cy={cy} r={radius} fill="none" stroke={color} strokeWidth={stroke} />
      <line x1={cx - radius * 1.7} y1={cy} x2={cx + radius * 1.7} y2={cy} stroke={color} strokeWidth={stroke} />
      <line x1={cx} y1={cy - radius * 1.7} x2={cx} y2={cy + radius * 1.7} stroke={color} strokeWidth={stroke} />
    </g>
  );
}

function PlanOverlay({
  analysis,
  preview,
  frameWidth,
  frameHeight,
  selectedIds,
  onToggle,
}: {
  analysis: DirectorAnalysis | null;
  preview: string;
  frameWidth: number;
  frameHeight: number;
  selectedIds: string[];
  onToggle: (id: string) => void;
}) {
  const width = frameWidth > 0 ? frameWidth : 1920;
  const height = frameHeight > 0 ? frameHeight : 1080;
  const unit = Math.min(width, height);
  const fontSize = Math.max(14, Math.round(unit / 26));
  const stroke = Math.max(2, unit / 260);
  const anchorRadius = Math.max(4, unit / 70);

  const objects = analysis?.objects || [];
  const regions = analysis?.regions?.length ? analysis.regions : analysis?.effect_plan.regions || [];
  const protect = analysis?.protect?.length ? analysis.protect : analysis?.effect_plan.protect || [];

  return (
    <div
      className="relative overflow-hidden rounded-2xl border border-[var(--line)] bg-black"
      style={{ aspectRatio: `${width} / ${height}` }}
    >
      {preview ? (
        <img src={preview} alt="" className="absolute inset-0 h-full w-full object-contain" />
      ) : (
        <div className="flex h-full items-center justify-center px-4 text-center text-xs text-[var(--muted)]">
          Đang đọc ảnh…
        </div>
      )}
      {analysis && (
        <svg
          className="absolute inset-0 h-full w-full"
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="Vùng, object và điểm neo do AI phân tích"
        >
          {regions.map((region, index) =>
            region.polygon && region.polygon.length >= 3 ? (
              <polygon
                key={`region-${region.id}-${index}`}
                points={region.polygon.map((point) => `${point.x * width},${point.y * height}`).join(" ")}
                fill="rgba(125, 211, 252, 0.08)"
                stroke="#7dd3fc"
                strokeWidth={stroke}
                strokeDasharray={`${stroke * 3} ${stroke * 2}`}
              >
                <title>{`Vùng ${region.id}`}</title>
              </polygon>
            ) : null,
          )}
          {protect.map((item, index) => {
            const rect = boxRect(item.box, width, height);
            return (
              <g key={`protect-${item.label}-${index}`}>
                <rect
                  {...rect}
                  fill="rgba(248, 113, 113, 0.16)"
                  stroke="#fb923c"
                  strokeWidth={stroke}
                  strokeDasharray={`${stroke * 2} ${stroke}`}
                />
                <text
                  x={rect.x + stroke * 2}
                  y={labelY(rect.y, fontSize * 0.8)}
                  fill="#fdba74"
                  fontSize={fontSize * 0.8}
                  paintOrder="stroke"
                  stroke="#110e0b"
                  strokeWidth={fontSize * 0.16}
                >
                  bảo vệ: {item.label}
                </text>
              </g>
            );
          })}
          {objects.map((object) => {
            const selected = selectedIds.includes(object.id);
            const rect = boxRect(object.box, width, height);
            const color = selected ? "#e4b15a" : "#8c8378";
            const toggle = () => onToggle(object.id);
            return (
              <g
                key={object.id}
                onClick={toggle}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    toggle();
                  }
                }}
                role="button"
                tabIndex={0}
                aria-pressed={selected}
                className="cursor-pointer"
              >
                <rect
                  {...rect}
                  fill={selected ? "rgba(228, 177, 90, 0.12)" : "rgba(140, 131, 120, 0.08)"}
                  stroke={color}
                  strokeWidth={stroke}
                />
                <text
                  x={rect.x + stroke * 2.5}
                  y={labelY(rect.y, fontSize)}
                  fill={selected ? "#f6dfae" : "#cfc6b8"}
                  fontSize={fontSize}
                  paintOrder="stroke"
                  stroke="#110e0b"
                  strokeWidth={fontSize * 0.18}
                >
                  {object.label}
                </text>
                {object.anchors?.pivot && (
                  <Anchor
                    point={object.anchors.pivot}
                    width={width}
                    height={height}
                    radius={anchorRadius}
                    color={color}
                    label={`pivot ${object.label}`}
                  />
                )}
                {object.anchors?.opening && (
                  <Anchor
                    point={object.anchors.opening}
                    width={width}
                    height={height}
                    radius={anchorRadius}
                    color={color}
                    label={`opening ${object.label}`}
                  />
                )}
              </g>
            );
          })}
        </svg>
      )}
    </div>
  );
}

export function AiEffects({
  images,
  effects,
  outputProfile,
  onEffectsChange,
}: {
  images: string[];
  effects: BackgroundEffect[];
  outputProfile: string;
  onEffectsChange: (updater: (current: BackgroundEffect[]) => BackgroundEffect[]) => void;
}) {
  const imageSignature = images.join("\n");
  const [selectedImage, setSelectedImage] = useState(() => images[0] || "");
  const [prompt, setPrompt] = useState("");
  const [consent, setConsent] = useState(false);
  const [analysis, setAnalysis] = useState<DirectorAnalysis | null>(null);
  const [selectedObjectIds, setSelectedObjectIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fact, setFact] = useState<InspectFact | null>(null);
  const [inspectError, setInspectError] = useState("");

  useEffect(() => {
    const current = imageSignature.split("\n").filter(Boolean);
    setSelectedImage((value) => (value && current.includes(value) ? value : current[0] || ""));
  }, [imageSignature]);

  useEffect(() => {
    setAnalysis(null);
    setSelectedObjectIds([]);
    setError("");
  }, [selectedImage]);

  useEffect(() => {
    setFact(null);
    setInspectError("");
    if (!selectedImage) return;
    const controller = new AbortController();
    fetch("/api/images/inspect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: [selectedImage] }),
      signal: controller.signal,
    })
      .then((response) => (response.ok ? response.json() : { images: [] }))
      .then((data: { images?: InspectFact[] }) => {
        const first = (data.images || [])[0];
        if (!first) {
          setInspectError("Không đọc được ảnh này. Hãy chọn lại ảnh nền.");
          return;
        }
        setFact(first);
      })
      .catch(() => {
        if (!controller.signal.aborted) setInspectError("Không đọc được ảnh nền. Kiểm tra server Python đang chạy.");
      });
    return () => controller.abort();
  }, [selectedImage]);

  const existingEffect = selectedImage ? effects.find((item) => item.image_path === selectedImage) : undefined;
  const preview = fact?.preview || fact?.thumbnail || "";
  const frameWidth = fact && fact.width > 0 ? fact.width : analysis?.width || 1920;
  const frameHeight = fact && fact.height > 0 ? fact.height : analysis?.height || 1080;
  const portrait = outputProfile === "portrait_1080x1920";

  const canAnalyze = Boolean(selectedImage) && prompt.trim().length > 0 && consent && !busy;
  const analyzeHint = !selectedImage
    ? "Chọn ảnh nền trước."
    : prompt.trim().length === 0
      ? "Nhập mô tả hiệu ứng."
      : !consent
        ? "Cần bật đồng ý gửi ảnh lên Gemini."
        : "";

  const regions = analysis?.regions?.length ? analysis.regions : analysis?.effect_plan.regions || [];
  const protect = analysis?.protect?.length ? analysis.protect : analysis?.effect_plan.protect || [];

  function toggleObject(id: string) {
    setSelectedObjectIds((current) =>
      current.includes(id) ? current.filter((value) => value !== id) : [...current, id],
    );
  }

  async function analyze() {
    if (!canAnalyze) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/director/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_path: selectedImage, prompt: prompt.trim(), consent }),
      });
      if (!response.ok) {
        const detail = await readError(response);
        setError(
          response.status === 502
            ? `${detail}\nKiểm tra GEMINI_API_KEY trong .env rồi khởi động lại server.`
            : detail,
        );
        return;
      }
      const data = (await response.json()) as DirectorAnalysis;
      setAnalysis(data);
      setSelectedObjectIds((data.objects || []).map((object) => object.id));
    } catch {
      setError("Không kết nối được server phân tích. Kiểm tra server Python đang chạy rồi thử lại.");
    } finally {
      setBusy(false);
    }
  }

  function applyEffect() {
    if (!analysis || !selectedImage) return;
    const effect: BackgroundEffect = {
      image_path: selectedImage,
      image_hash: analysis.image_hash,
      effect_prompt: prompt.trim(),
      effect_consent_receipt: {
        timestamp: new Date().toISOString(),
        image_hash: analysis.image_hash,
        provider: "gemini",
        model: analysis.model,
      },
      selected_object_ids: selectedObjectIds,
      effect_plan: analysis.effect_plan,
      model: analysis.model,
      created: new Date().toISOString(),
      asset_id: null,
    };
    onEffectsChange((current) => [
      ...current.filter((item) => item.image_path !== selectedImage),
      effect,
    ]);
  }

  function removeEffect() {
    if (!selectedImage) return;
    onEffectsChange((current) => current.filter((item) => item.image_path !== selectedImage));
  }

  if (images.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-[var(--line)] bg-black/15 p-4 text-sm leading-6 text-[var(--muted)]">
        Chưa có ảnh nền để phân tích. Thêm ảnh ở mục Hình; nếu bỏ trống, ảnh bìa sẽ được dùng.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {images.length > 1 && (
        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Ảnh phân tích</span>
          <select
            className={fieldClass}
            value={selectedImage}
            onChange={(event) => setSelectedImage(event.target.value)}
          >
            {images.map((path) => (
              <option key={path} value={path}>
                {fileName(path)}
              </option>
            ))}
          </select>
        </label>
      )}

      <PlanOverlay
        analysis={analysis}
        preview={preview}
        frameWidth={frameWidth}
        frameHeight={frameHeight}
        selectedIds={selectedObjectIds}
        onToggle={toggleObject}
      />

      <div className="flex flex-wrap items-center gap-2 text-xs leading-5 text-[var(--muted)]">
        <span>
          {fileName(selectedImage)}
          {fact && fact.width > 0 ? ` · ${fact.width}×${fact.height} · ${fact.ratio}` : ""}
        </span>
        <span className="rounded-full border border-[var(--gold)] px-2 py-0.5 text-[var(--gold)]">
          Khung xuất: {profileLabel(outputProfile)}
        </span>
      </div>
      {portrait && (
        <p className="text-xs leading-5 text-[var(--muted)]">
          9:16: dải lời nằm trên vùng an toàn đáy; vùng an toàn trên và phải dành cho giao diện ứng dụng. Overlay vẫn vẽ
          trên ảnh gốc; bản render cuối sẽ khớp khung 9:16.
        </p>
      )}
      {inspectError && <p className="text-xs leading-5 text-amber-100/90">{inspectError}</p>}

      <div>
        <label className="mb-1.5 block text-sm text-[var(--muted)]" htmlFor="ai-effects-prompt">
          Mô tả hiệu ứng
        </label>
        <textarea
          id="ai-effects-prompt"
          className={`${fieldClass} min-h-20 resize-y leading-6`}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="Ví dụ: mưa nhẹ trên phố, khói bốc từ bát mỳ…"
        />
        <div className="mt-2 flex flex-wrap gap-2">
          {QUICK_PROMPTS.map((chip) => (
            <button
              key={chip}
              type="button"
              onClick={() => setPrompt(chip)}
              className={`rounded-full border px-3 py-1 text-xs transition ${
                prompt === chip
                  ? "border-[var(--gold)] bg-black/40 text-[var(--gold)]"
                  : "border-[var(--line)] text-[var(--muted)] hover:border-[var(--gold)] hover:text-white"
              }`}
            >
              {chip}
            </button>
          ))}
        </div>
      </div>

      <label className="flex items-start gap-2 rounded-xl border border-[var(--line)] bg-black/20 p-3">
        <input
          type="checkbox"
          checked={consent}
          onChange={(event) => setConsent(event.target.checked)}
          className="mt-0.5 accent-[var(--gold)]"
        />
        <span className="text-sm leading-5">
          Gửi ảnh lên Google Gemini để phân tích
          <span className="mt-1 block text-xs leading-4 text-[var(--muted)]">
            Mặc định tắt. Chỉ ảnh đang chọn cùng mô tả hiệu ứng được gửi khi bạn bấm Phân tích ảnh.
          </span>
        </span>
      </label>

      <div className="space-y-2">
        <button
          type="button"
          onClick={analyze}
          disabled={!canAnalyze}
          className="w-full rounded-2xl bg-[var(--gold)] px-4 py-2.5 text-sm font-semibold text-[#1a1206] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Đang phân tích ảnh…" : "Phân tích ảnh"}
        </button>
        {busy && (
          <p className="text-xs leading-5 text-[var(--muted)]">Gemini đang đọc ảnh và lập kế hoạch hiệu ứng…</p>
        )}
        {!busy && analyzeHint && <p className="text-xs leading-5 text-[var(--muted)]">{analyzeHint}</p>}
      </div>

      {error && (
        <p className="whitespace-pre-line rounded-xl border border-red-900 bg-red-950/60 px-3 py-2 text-sm leading-5 text-red-100">
          {error}
        </p>
      )}

      {analysis && (
        <div className="space-y-2 rounded-xl border border-[var(--line)] bg-black/20 p-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-[var(--muted)]">{analysis.model}</span>
            {analysis.cached && (
              <span className="rounded-full border border-[var(--gold)] px-2 py-0.5 text-[var(--gold)]">
                Đã lưu cache
              </span>
            )}
            {analysis.usage && <span className="text-[var(--muted)]">{usageText(analysis.usage)}</span>}
          </div>
          {analysis.scene && <p className="text-xs leading-5 text-[var(--muted)]">{analysis.scene}</p>}
        </div>
      )}

      {existingEffect && (
        <div className="flex items-start justify-between gap-3 rounded-xl border border-[var(--gold)] bg-black/25 px-3 py-2">
          <div>
            <p className="text-sm text-[var(--gold)]">Đã áp dụng</p>
            <p className="text-xs leading-5 text-[var(--muted)]">
              {existingEffect.effect_prompt}
              {existingEffect.effect_plan.items.length > 0
                ? ` · ${existingEffect.effect_plan.items.map((item) => item.effect).join(", ")}`
                : ""}
            </p>
          </div>
          <button type="button" onClick={removeEffect} className="shrink-0 text-xs text-[var(--muted)] hover:text-white">
            Xóa
          </button>
        </div>
      )}

      {analysis && (analysis.objects || []).length > 0 && (
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-sm text-[var(--muted)]">Object cho hiệu ứng</span>
            <span className="text-xs text-[var(--muted)]">
              {selectedObjectIds.length}/{analysis.objects.length} chọn
            </span>
          </div>
          <ul className="max-h-64 space-y-2 overflow-auto pr-1">
            {analysis.objects.map((object) => {
              const checked = selectedObjectIds.includes(object.id);
              const percent =
                object.confidence <= 1
                  ? Math.round(Math.max(0, object.confidence) * 100)
                  : Math.round(object.confidence);
              return (
                <li
                  key={object.id}
                  className={`rounded-xl border px-3 py-2 transition ${
                    checked ? "border-[var(--gold)] bg-black/25" : "border-[var(--line)] bg-black/15"
                  }`}
                >
                  <label className="flex cursor-pointer items-start gap-2">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleObject(object.id)}
                      className="mt-1 accent-[var(--gold)]"
                    />
                    <span className="min-w-0">
                      <span className="flex flex-wrap items-baseline gap-2">
                        <span className="text-sm">{object.label}</span>
                        <span className="text-xs text-[var(--muted)]">{percent}%</span>
                      </span>
                      {object.affordances.length > 0 && (
                        <span className="mt-1 flex flex-wrap gap-1">
                          {object.affordances.map((affordance) => (
                            <span
                              key={affordance}
                              className="rounded-full border border-[var(--line)] px-2 py-0.5 text-[11px] text-[var(--muted)]"
                            >
                              {affordance}
                            </span>
                          ))}
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {(regions.length > 0 || protect.length > 0) && (
        <div className="rounded-xl border border-[var(--line)] bg-black/20 p-3 text-xs leading-5 text-[var(--muted)]">
          {regions.length > 0 && (
            <p>Vùng: {regions.map((region) => `${region.id} (${region.polygon.length} điểm)`).join(", ")}</p>
          )}
          {protect.length > 0 && <p className={regions.length > 0 ? "mt-1" : ""}>Bảo vệ: {protect.map((item) => item.label).join(", ")}</p>}
        </div>
      )}

      {analysis && (
        <div className="sticky bottom-2 z-10 rounded-2xl border border-[var(--gold)] bg-[var(--panel)] p-2 shadow-lg">
          <button
            type="button"
            onClick={applyEffect}
            disabled={busy}
            className="w-full rounded-xl bg-[var(--gold)] px-4 py-2.5 text-sm font-semibold text-[#1a1206] transition hover:brightness-105 disabled:opacity-50"
          >
            Áp dụng
          </button>
          <p className="mt-1 text-center text-[11px] leading-4 text-[var(--muted)]">
            Sau khi chọn object, bấm Áp dụng rồi Render preview (Studio) hoặc Xuất video (Karaoke).
          </p>
        </div>
      )}

      <p className="text-xs leading-5 text-[var(--muted)]">
        Hiện render mưa, tuyết, nước, khói, gió/sway, lửa, sương mù và nhịp sáng.
      </p>
    </div>
  );
}
