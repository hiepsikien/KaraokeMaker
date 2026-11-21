"use client";

import { useEffect, useRef, useState } from "react";

import type {
  BackgroundEffect,
  DirectorAnalysis,
  DirectorBox,
  DirectorPoint,
  DirectorUsage,
  StudioAsset,
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

type PreviewResult = {
  id: string;
  url: string;
  width: number;
  height: number;
  duration: number;
};

type RenderState = {
  status: "rendering" | "done" | "error";
  percent?: number;
  preview?: PreviewResult;
  error?: string;
};

const QUICK_PROMPTS = ["mưa nhẹ", "khói bát mỳ", "gió đèn lồng", "sóng biển"];
const EFFECT_NAMES: Record<string, string> = {
  rain: "Mưa",
  snow: "Tuyết",
  water: "Nước",
  steam: "Khói",
  wind: "Gió / rung",
  birds: "Đàn chim",
  fire: "Lửa",
  embers: "Tàn lửa",
  sunrays: "Tia nắng",
  fog: "Sương mù",
  light: "Nhịp sáng",
};

const EFFECT_PARAMS: Record<string, string[]> = {
  rain: ["intensity", "density", "speed"],
  snow: ["intensity", "density", "speed"],
  water: ["intensity", "speed"],
  steam: ["intensity", "density", "speed"],
  wind: ["intensity", "speed"],
  birds: ["intensity", "speed"],
  fire: ["intensity", "speed"],
  embers: ["intensity", "density", "speed"],
  sunrays: ["intensity", "speed"],
  fog: ["intensity", "speed"],
  light: ["intensity", "speed"],
};

const PARAM_META: Record<string, { label: string; min: number; max: number; step: number; default: number }> = {
  intensity: { label: "Cường độ", min: 0, max: 100, step: 5, default: 55 },
  density: { label: "Mật độ", min: 0, max: 150, step: 5, default: 100 },
  speed: { label: "Tốc độ", min: 20, max: 200, step: 5, default: 100 },
};

function effectLabel(effect: string) {
  return EFFECT_NAMES[effect] || effect;
}

const PROFILE_LABELS: Record<string, string> = {
  landscape_1080p: "Ngang 16:9",
  portrait_1080x1920: "Dọc 9:16",
  square_1080: "Vuông 1:1",
  portrait_4x5: "Dọc 4:5",
};

const PROFILE_DIMENSIONS: Record<string, [number, number]> = {
  landscape_1080p: [1920, 1080],
  portrait_1080x1920: [1080, 1920],
  square_1080: [1080, 1080],
  portrait_4x5: [1080, 1350],
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
  onBoxChange,
}: {
  analysis: DirectorAnalysis | null;
  preview: string;
  frameWidth: number;
  frameHeight: number;
  selectedIds: string[];
  onToggle: (id: string) => void;
  onBoxChange: (id: string, box: DirectorBox) => void;
}) {
  const width = frameWidth > 0 ? frameWidth : 1920;
  const height = frameHeight > 0 ? frameHeight : 1080;
  const unit = Math.min(width, height);
  const fontSize = Math.max(14, Math.round(unit / 26));
  const stroke = Math.max(2, unit / 260);
  const anchorRadius = Math.max(4, unit / 70);
  const handleSize = Math.max(12, unit / 60);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [resize, setResize] = useState<{
    id: string;
    corner: "nw" | "ne" | "sw" | "se";
    start: DirectorBox;
  } | null>(null);

  const objects = analysis?.objects || [];
  const regions = analysis?.regions?.length ? analysis.regions : analysis?.effect_plan.regions || [];
  const protect = analysis?.protect?.length ? analysis.protect : analysis?.effect_plan.protect || [];

  function normalizedPoint(event: React.PointerEvent) {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return {
      x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
    };
  }

  function startResize(
    event: React.PointerEvent<SVGRectElement>,
    id: string,
    corner: "nw" | "ne" | "sw" | "se",
    box: DirectorBox,
  ) {
    event.stopPropagation();
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setResize({ id, corner, start: { ...box } });
  }

  function moveResize(event: React.PointerEvent<SVGRectElement>) {
    if (!resize) return;
    const point = normalizedPoint(event);
    if (!point) return;
    const box = { ...resize.start };
    const minSize = 0.01;
    if (resize.corner.includes("w")) box.x1 = Math.min(point.x, box.x2 - minSize);
    if (resize.corner.includes("e")) box.x2 = Math.max(point.x, box.x1 + minSize);
    if (resize.corner.includes("n")) box.y1 = Math.min(point.y, box.y2 - minSize);
    if (resize.corner.includes("s")) box.y2 = Math.max(point.y, box.y1 + minSize);
    box.x1 = Math.max(0, Math.min(1, box.x1));
    box.y1 = Math.max(0, Math.min(1, box.y1));
    box.x2 = Math.max(0, Math.min(1, box.x2));
    box.y2 = Math.max(0, Math.min(1, box.y2));
    onBoxChange(resize.id, box);
  }

  function endResize(event: React.PointerEvent<SVGRectElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setResize(null);
  }

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
          ref={svgRef}
          className="absolute inset-0 h-full w-full touch-none"
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
                {(
                  [
                    ["nw", rect.x, rect.y, "nwse-resize"],
                    ["ne", rect.x + rect.width, rect.y, "nesw-resize"],
                    ["sw", rect.x, rect.y + rect.height, "nesw-resize"],
                    ["se", rect.x + rect.width, rect.y + rect.height, "nwse-resize"],
                  ] as const
                ).map(([corner, x, y, cursor]) => (
                  <rect
                    key={`${object.id}-handle-${corner}`}
                    x={x - handleSize / 2}
                    y={y - handleSize / 2}
                    width={handleSize}
                    height={handleSize}
                    fill={color}
                    stroke="#110e0b"
                    strokeWidth={Math.max(1, stroke / 2)}
                    style={{ cursor }}
                    onPointerDown={(event) => startResize(event, object.id, corner, object.box)}
                    onPointerMove={moveResize}
                    onPointerUp={endResize}
                    onPointerCancel={endResize}
                    onClick={(event) => event.stopPropagation()}
                  >
                    <title>{`Kéo để chỉnh kích thước ${object.label}`}</title>
                  </rect>
                ))}
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
  imageDuration = "8",
  onEffectsChange,
  onAssetDuration,
}: {
  images: string[];
  effects: BackgroundEffect[];
  outputProfile: string;
  imageDuration?: string;
  onEffectsChange: (updater: (current: BackgroundEffect[]) => BackgroundEffect[]) => void;
  onAssetDuration?: (seconds: number) => void;
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
  const [assets, setAssets] = useState<StudioAsset[]>([]);
  const [assetsError, setAssetsError] = useState("");
  const [renderStates, setRenderStates] = useState<Record<string, RenderState>>({});
  const renderAbortRef = useRef<AbortController | null>(null);
  const renderTargetRef = useRef("");

  useEffect(() => {
    return () => {
      renderAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const current = imageSignature.split("\n").filter(Boolean);
    setSelectedImage((value) => (value && current.includes(value) ? value : current[0] || ""));
  }, [imageSignature]);

  useEffect(() => {
    // Leaving an image cancels its in-flight clip render.
    const target = renderTargetRef.current;
    renderAbortRef.current?.abort();
    renderAbortRef.current = null;
    renderTargetRef.current = "";
    if (target) setImageRender(target, null);
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
      body: JSON.stringify({
        paths: [selectedImage],
        output_profile: outputProfile,
        with_preview: true,
      }),
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
  }, [selectedImage, outputProfile]);

  async function refreshAssets() {
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
      setAssetsError("Không tải được asset đã render. Kiểm tra server Python.");
    }
  }

  useEffect(() => {
    void refreshAssets();
  }, []);

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

  function changeObjectBox(id: string, box: DirectorBox) {
    setAnalysis((current) => {
      if (!current) return current;
      const objects = current.objects.map((object) =>
        object.id === id ? { ...object, box } : object,
      );
      const planObjects = current.effect_plan.objects;
      const effectObjects = planObjects
        ? planObjects.map((object) => (object.id === id ? { ...object, box } : object))
        : objects;
      return {
        ...current,
        objects,
        effect_plan: { ...current.effect_plan, objects: effectObjects },
      };
    });
  }

  async function analyze() {
    if (!canAnalyze) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch("/api/director/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_path: selectedImage,
          prompt: prompt.trim(),
          consent,
          output_profile: outputProfile,
        }),
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

  function updateParam(index: number, key: string, percent: number) {
    setAnalysis((current) => {
      if (!current) return current;
      const items = current.effect_plan.items.map((item, itemIndex) =>
        itemIndex === index
          ? {
              ...item,
              params: { ...(item.params || {}), [key]: percent / 100 },
            }
          : item,
      );
      return {
        ...current,
        effect_plan: { ...current.effect_plan, items },
      };
    });
  }

  function setImageRender(imagePath: string, state: RenderState | null) {
    setRenderStates((current) => {
      const next = { ...current };
      if (state) next[imagePath] = state;
      else delete next[imagePath];
      return next;
    });
  }

  // Renders the full-resolution clip for one applied effect, then saves it as a
  // Studio asset so export can reuse it instead of rendering inline.
  async function startRender(effect: BackgroundEffect, force = false) {
    if (!effect.image_path) return;
    if (!force && effect.asset_id) return;
    const imagePath = effect.image_path;
    const plan = effect.effect_plan;
    const imageHash = effect.image_hash;
    const parsed = Number(imageDuration);
    const duration = Number.isFinite(parsed) && parsed > 0 ? parsed : 8;

    if (!force) {
      // Reuse an existing asset with the same image/plan duration instead of
      // rendering and saving another copy.
      const reusable = assets.find((asset) => {
        if (asset.image_hash !== imageHash || asset.profile_id !== outputProfile) return false;
        const seconds = asset.asset_frames && asset.fps ? asset.asset_frames / asset.fps : asset.loop_duration;
        if (typeof seconds !== "number") return false;
        return Math.abs(seconds - duration) <= 0.5 / Math.max(1, Number(asset.fps || 30));
      });
      if (reusable) {
        onEffectsChange((current) =>
          current.map((item) =>
            item.image_path === imagePath ? { ...item, asset_id: reusable.id } : item,
          ),
        );
        setImageRender(imagePath, { status: "done" });
        return;
      }
    }

    const previousTarget = renderTargetRef.current;
    renderAbortRef.current?.abort();
    if (previousTarget && previousTarget !== imagePath) setImageRender(previousTarget, null);
    const controller = new AbortController();
    renderAbortRef.current = controller;
    renderTargetRef.current = imagePath;
    setImageRender(imagePath, { status: "rendering", percent: 0 });

    try {
      const response = await fetch("/api/studio/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_path: imagePath,
          effect_plan: plan,
          duration,
          output_profile: outputProfile,
          full_resolution: true,
        }),
        signal: controller.signal,
      });
      if (!response.ok) {
        setImageRender(imagePath, { status: "error", error: await readError(response) });
        return;
      }
      let payload = (await response.json()) as PreviewResult | { job_id?: string };
      if ("job_id" in payload && payload.job_id) {
        const jobId = payload.job_id;
        let result: PreviewResult | null = null;
        // Full renders run in a background job; poll until done.
        for (let attempt = 0; attempt < 480; attempt += 1) {
          await new Promise((resolve) => setTimeout(resolve, 1500));
          if (controller.signal.aborted) return;
          const statusResponse = await fetch(`/api/studio/render-job/${jobId}`, {
            signal: controller.signal,
          });
          if (statusResponse.status === 404) {
            setImageRender(imagePath, {
              status: "error",
              error: "Không thấy job render (server có thể đã restart).",
            });
            return;
          }
          if (!statusResponse.ok) {
            setImageRender(imagePath, { status: "error", error: await readError(statusResponse) });
            return;
          }
          const status = (await statusResponse.json()) as {
            status: string;
            result?: PreviewResult;
            error?: string;
            percent?: number;
          };
          if (typeof status.percent === "number" && status.percent > 0) {
            setImageRender(imagePath, { status: "rendering", percent: status.percent });
          }
          if (status.status === "done" && status.result) {
            result = status.result;
            break;
          }
          if (status.status === "error") {
            setImageRender(imagePath, {
              status: "error",
              error: status.error || "Render clip thất bại.",
            });
            return;
          }
        }
        if (!result) {
          setImageRender(imagePath, {
            status: "error",
            error: "Render clip quá lâu. Xem terminal Python để kiểm tra.",
          });
          return;
        }
        payload = result;
      }

      const preview = payload as PreviewResult;
      if (!preview?.id) {
        setImageRender(imagePath, { status: "error", error: "Render không trả về clip." });
        return;
      }

      const assetResponse = await fetch("/api/studio/assets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preview_id: preview.id,
          name: fileName(imagePath).replace(/\.[^.]+$/, ""),
          image_path: imagePath,
          image_hash: imageHash,
          effect_plan: plan,
          duration: preview.duration,
          output_profile: outputProfile,
        }),
        signal: controller.signal,
      });
      if (!assetResponse.ok) {
        setImageRender(imagePath, {
          status: "error",
          error: `Đã render clip nhưng chưa lưu được asset: ${await readError(assetResponse)}`,
        });
        return;
      }
      const assetData = (await assetResponse.json()) as { asset?: { id?: string } };
      const assetId = assetData.asset?.id;
      if (!assetId) {
        setImageRender(imagePath, { status: "error", error: "Không nhận được asset từ server." });
        return;
      }
      onEffectsChange((current) =>
        current.map((item) =>
          item.image_path === imagePath ? { ...item, asset_id: assetId } : item,
        ),
      );
      setImageRender(imagePath, { status: "done", preview });
      void refreshAssets();
    } catch {
      if (controller.signal.aborted) return;
      setImageRender(imagePath, {
        status: "error",
        error: "Không kết nối được server render. Kiểm tra server Python đang chạy rồi thử lại.",
      });
    } finally {
      if (renderAbortRef.current === controller) {
        renderAbortRef.current = null;
        renderTargetRef.current = "";
      }
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
      effect_plan: {
        ...analysis.effect_plan,
        objects: analysis.objects,
        selected_object_ids: selectedObjectIds,
      },
      model: analysis.model,
      created: new Date().toISOString(),
      asset_id: null,
    };
    onEffectsChange((current) => [
      ...current.filter((item) => item.image_path !== selectedImage),
      effect,
    ]);
    // State first, then create the clip in the background (never blocks Apply).
    void startRender(effect);
  }

  function removeEffect() {
    if (!selectedImage) return;
    onEffectsChange((current) => current.filter((item) => item.image_path !== selectedImage));
  }

  const imageAssets = assets.filter(
    (asset) =>
      asset.source_image === selectedImage ||
      Boolean(existingEffect?.image_hash && asset.image_hash === existingEffect.image_hash) ||
      Boolean(analysis?.image_hash && asset.image_hash === analysis.image_hash),
  );
  const selectedAsset = imageAssets.find((asset) => asset.id === existingEffect?.asset_id);
  const currentRenderState = selectedImage ? renderStates[selectedImage] : undefined;
  const assetSeconds = selectedAsset?.asset_frames && selectedAsset?.fps
    ? selectedAsset.asset_frames / selectedAsset.fps
    : selectedAsset?.loop_duration;
  const assetMismatch =
    typeof assetSeconds === "number" &&
    Math.abs(assetSeconds - Number(imageDuration || 8)) > 0.5 / Math.max(1, Number(selectedAsset?.fps || 30));
  const assetProfileMismatch = Boolean(
    selectedAsset?.profile_id && selectedAsset.profile_id !== outputProfile,
  );

  function chooseAsset(assetId: string) {
    if (!selectedImage) return;
    onEffectsChange((current) =>
      current.map((item) =>
        item.image_path === selectedImage ? { ...item, asset_id: assetId || null } : item,
      ),
    );
  }

  function useAssetForImage(assetId: string) {
    if (!selectedImage) return;
    const asset = imageAssets.find((item) => item.id === assetId);
    if (!asset) return;
    const [frameWidth, frameHeight] = PROFILE_DIMENSIONS[outputProfile] || PROFILE_DIMENSIONS.landscape_1080p;
    const plan =
      asset.effect_plan ||
      ({
        version: 1,
        image_hash: asset.image_hash,
        profile_id: outputProfile,
        fit_transform: {
          mode: "contain",
          source_width: asset.width || frameWidth,
          source_height: asset.height || frameHeight,
          frame_width: frameWidth,
          frame_height: frameHeight,
          scale: 1,
          offset_x: 0,
          offset_y: 0,
        },
        items: [],
      } as BackgroundEffect["effect_plan"]);
    const effect: BackgroundEffect = {
      image_path: selectedImage,
      image_hash: asset.image_hash,
      effect_prompt: "Clip Studio",
      effect_consent_receipt: {
        timestamp: new Date().toISOString(),
        image_hash: asset.image_hash,
        provider: "studio",
        model: "asset",
      },
      selected_object_ids: [],
      effect_plan: plan,
      model: "studio",
      created: new Date().toISOString(),
      asset_id: asset.id,
    };
    onEffectsChange((current) => [
      ...current.filter((item) => item.image_path !== selectedImage),
      effect,
    ]);
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
        onBoxChange={changeObjectBox}
      />
      {analysis && (
        <p className="text-xs leading-5 text-[var(--muted)]">
          Kéo 4 góc ô để chỉnh kích thước object trước khi áp dụng.
        </p>
      )}

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
          {analysis.intent_fallback && (
            <p className="text-xs leading-5 text-amber-100/90">
              Không gọi được Gemini để hiểu prompt; đang dùng từ khóa dự phòng. Kết quả có thể thiếu hiệu ứng.
            </p>
          )}
          {analysis.scene && <p className="text-xs leading-5 text-[var(--muted)]">{analysis.scene}</p>}
        </div>
      )}

      {analysis && (analysis.effect_plan.items || []).length > 0 && (
        <div className="space-y-3 rounded-xl border border-[var(--line)] bg-black/20 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm">Cường độ / mật độ / tốc độ</span>
            <span className="text-[11px] text-[var(--muted)]">chỉnh trước khi Áp dụng</span>
          </div>
          {analysis.effect_plan.items.map((item, index) => {
            const keys = EFFECT_PARAMS[item.effect] || ["intensity"];
            return (
              <div key={`${item.effect}-${index}`} className="space-y-1">
                <p className="text-xs font-medium">{effectLabel(item.effect)}</p>
                {keys.map((key) => {
                  const meta = PARAM_META[key];
                  const raw = item.params?.[key];
                  const value = typeof raw === "number" ? Math.round(raw * 100) : meta.default;
                  // Wind can be fully stopped; other effects keep a small
                  // minimum so their particles/loops still resolve.
                  const min = key === "speed" && item.effect === "wind" ? 0 : meta.min;
                  return (
                    <label key={key} className="block">
                      <span className="flex items-center justify-between text-[11px] text-[var(--muted)]">
                        <span>{meta.label}</span>
                        <span>{value}%</span>
                      </span>
                      <input
                        type="range"
                        min={min}
                        max={meta.max}
                        step={meta.step}
                        value={value}
                        onChange={(event) =>
                          updateParam(index, key, Number(event.target.value))
                        }
                        className="mt-0.5 w-full accent-[var(--gold)]"
                      />
                    </label>
                  );
                })}
              </div>
            );
          })}
          <p className="text-[11px] leading-4 text-[var(--muted)]">
            Chỉ đổi kế hoạch cục bộ, không gọi lại Gemini. Bấm Áp dụng để lưu. Mật độ chỉ
            hiện với mưa/tuyết/khói/tàn lửa.
          </p>
        </div>
      )}

      {existingEffect && (
        <div className="space-y-2 rounded-xl border border-[var(--gold)] bg-black/25 px-3 py-2">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm text-[var(--gold)]">Đã áp dụng</p>
              <p className="text-xs leading-5 text-[var(--muted)]">
                {existingEffect.effect_prompt}
                {(existingEffect.effect_plan?.items?.length ?? 0) > 0
                  ? ` · ${existingEffect.effect_plan.items.map((item) => item.effect).join(", ")}`
                  : ""}
              </p>
            </div>
            <button type="button" onClick={removeEffect} className="shrink-0 text-xs text-[var(--muted)] hover:text-white">
              Xóa
            </button>
          </div>
          {currentRenderState?.status === "rendering" && (
            <p className="text-xs leading-5 text-[var(--muted)]">
              Đang tạo clip…
              {typeof currentRenderState.percent === "number" && currentRenderState.percent > 0
                ? ` ${currentRenderState.percent}%`
                : ""}
            </p>
          )}
          {currentRenderState?.status === "error" && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-red-200">{currentRenderState.error}</span>
              <button
                type="button"
                onClick={() => void startRender(existingEffect, true)}
                className="text-[var(--gold)] hover:text-white"
              >
                Thử lại
              </button>
            </div>
          )}
          {existingEffect.asset_id &&
            currentRenderState?.status !== "rendering" &&
            currentRenderState?.status !== "error" && (
              <p className="text-xs leading-5 text-emerald-200">Đã tạo clip</p>
            )}
          <label className="block">
            <span className="mb-1 block text-[11px] text-[var(--muted)]">Clip đã render (Studio)</span>
            <div className="flex gap-2">
              <select
                className={fieldClass}
                value={existingEffect.asset_id || ""}
                onChange={(event) => chooseAsset(event.target.value)}
              >
                <option value="">Render từ effect_plan</option>
                {imageAssets.map((asset) => (
                  <option key={asset.id} value={asset.id}>
                    {asset.name} · {asset.asset_frames && asset.fps ? `${(asset.asset_frames / asset.fps).toFixed(1)}s` : "clip"}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => void refreshAssets()}
                className="shrink-0 rounded-xl border border-[var(--line)] px-3 text-xs hover:border-[var(--gold)]"
              >
                Tải lại
              </button>
            </div>
          </label>
          {currentRenderState?.status !== "rendering" && currentRenderState?.status !== "error" && (
            <button
              type="button"
              onClick={() => void startRender(existingEffect, true)}
              className="text-xs text-[var(--gold)] hover:text-white"
            >
              {existingEffect.asset_id ? "Tạo lại clip" : "Tạo clip"}
            </button>
          )}
          {assetsError && <p className="text-[11px] text-red-200">{assetsError}</p>}
          {assetProfileMismatch && (
            <p className="text-[11px] leading-4 text-amber-200">
              Clip được render ở {selectedAsset?.profile_id}, khác định dạng hiện tại ({outputProfile}). Export sẽ
              fallback render từ effect_plan.
            </p>
          )}
          {assetMismatch && (
            <div className="space-y-1">
              <p className="text-[11px] leading-4 text-amber-200">
                Clip dài {assetSeconds?.toFixed(1)}s, khác “Mỗi ảnh nền” ({imageDuration}s). Export sẽ fallback render từ
                effect_plan; hãy render lại đúng thời lượng nếu muốn dùng clip này.
              </p>
              {onAssetDuration && assetSeconds ? (
                <button
                  type="button"
                  onClick={() => onAssetDuration(assetSeconds)}
                  className="text-[11px] text-[var(--gold)] hover:text-white"
                >
                  Đặt “Mỗi ảnh nền” = {assetSeconds.toFixed(1)}s
                </button>
              ) : null}
            </div>
          )}
        </div>
      )}

      {!existingEffect && selectedImage && imageAssets.length > 0 && (
        <div className="space-y-1 rounded-xl border border-[var(--line)] bg-black/20 px-3 py-2">
          <p className="text-xs text-[var(--gold)]">Dùng clip Studio cho ảnh này</p>
          <select
            className={fieldClass}
            defaultValue=""
            onChange={(event) => {
              if (event.target.value) useAssetForImage(event.target.value);
            }}
          >
            <option value="">Chọn clip đã render…</option>
            {imageAssets.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.name}
              </option>
            ))}
          </select>
          <p className="text-[11px] leading-4 text-[var(--muted)]">
            Chọn clip sẽ tạo một hiệu ứng chỉ dùng asset, không cần phân tích Gemini.
          </p>
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
            {existingEffect ? "Áp dụng lại" : "Áp dụng"}
          </button>
          <p className="mt-1 text-center text-[11px] leading-4 text-[var(--muted)]">
            Bấm Áp dụng sau khi chọn object — clip chuẩn tự tạo ở nền. Xuất video vẫn dựng trực tiếp nếu clip chưa xong.
          </p>
        </div>
      )}

      <p className="text-xs leading-5 text-[var(--muted)]">
        Hiện render mưa, tuyết, nước, khói, gió/sway, lửa, tàn lửa, chim, tia nắng, sương mù và nhịp sáng.
      </p>
    </div>
  );
}
