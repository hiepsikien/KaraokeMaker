"use client";

import { useEffect, useRef, useState } from "react";

import { ImageTray } from "@/components/image-tray";
import { Badge, Disclosure, ProgressBar, Segmented, btnPrimary, btnSecondary } from "@/components/ui/primitives";
import { inputClass } from "@/components/ui/field";
import { useNotices } from "@/components/ui/toast";
import { useSettings } from "@/lib/settings";
import { PROFILE_OPTIONS } from "@/lib/format";
import type {
  BackgroundEffect,
  DirectorAnalysis,
  DirectorBox,
  DirectorObject,
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

function assetDateLabel(value: string | undefined) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  const year = date.getFullYear() === new Date().getFullYear() ? "" : `/${date.getFullYear()}`;
  return `${pad(date.getDate())}/${pad(date.getMonth() + 1)}${year} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function clipOptionLabel(asset: StudioAsset, newestId: string) {
  const seconds =
    asset.asset_frames && asset.fps
      ? `${(asset.asset_frames / asset.fps).toFixed(1)}s`
      : asset.loop_duration
        ? `${asset.loop_duration.toFixed(1)}s`
        : "clip";
  const date = assetDateLabel(asset.created);
  const latest = asset.id === newestId ? " · mới nhất" : "";
  return `${asset.name} · ${seconds}${date ? ` · ${date}` : ""}${latest}`;
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

const fieldClass = inputClass;

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

function analysisFromAsset(asset: StudioAsset): DirectorAnalysis | null {
  const plan = asset.effect_plan;
  if (!plan) return null;
  const objects = plan.objects || [];
  return {
    image: asset.source_image,
    image_hash: asset.image_hash,
    width: asset.width || 1920,
    height: asset.height || 1080,
    model: "studio",
    scene: `Asset ${asset.name}`,
    objects,
    regions: plan.regions || [],
    protect: plan.protect || [],
    intent: null,
    effect_plan: plan,
    usage: null,
    cached: true,
  };
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
  drawMode,
  onDrawBox,
  pendingBox,
}: {
  analysis: DirectorAnalysis | null;
  preview: string;
  frameWidth: number;
  frameHeight: number;
  selectedIds: string[];
  onToggle: (id: string) => void;
  onBoxChange: (id: string, box: DirectorBox) => void;
  drawMode: boolean;
  onDrawBox: (box: DirectorBox) => void;
  pendingBox: DirectorBox | null;
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
  const [drawStart, setDrawStart] = useState<DirectorPoint | null>(null);
  const [drawBox, setDrawBox] = useState<DirectorBox | null>(null);

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

  function boxFromPoints(first: DirectorPoint, second: DirectorPoint): DirectorBox {
    return {
      x1: Math.min(first.x, second.x),
      y1: Math.min(first.y, second.y),
      x2: Math.max(first.x, second.x),
      y2: Math.max(first.y, second.y),
    };
  }

  function startDraw(event: React.PointerEvent<SVGRectElement>) {
    if (!drawMode) return;
    const point = normalizedPoint(event);
    if (!point) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setDrawStart(point);
    setDrawBox({ x1: point.x, y1: point.y, x2: point.x, y2: point.y });
  }

  function moveDraw(event: React.PointerEvent<SVGRectElement>) {
    if (!drawStart) return;
    const point = normalizedPoint(event);
    if (!point) return;
    setDrawBox(boxFromPoints(drawStart, point));
  }

  function endDraw(event: React.PointerEvent<SVGRectElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const point = normalizedPoint(event);
    const start = drawStart;
    setDrawStart(null);
    setDrawBox(null);
    if (!start || !point) return;
    const box = boxFromPoints(start, point);
    if (box.x2 - box.x1 < 0.01 || box.y2 - box.y1 < 0.01) return;
    onDrawBox(box);
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
          {(drawBox || pendingBox) && (
            <rect
              {...boxRect((drawBox || pendingBox)!, width, height)}
              fill="rgba(125, 211, 252, 0.18)"
              stroke="#7dd3fc"
              strokeWidth={stroke}
              strokeDasharray={`${stroke * 3} ${stroke * 2}`}
            />
          )}
          {drawMode && (
            <rect
              x={0}
              y={0}
              width={width}
              height={height}
              fill="transparent"
              style={{ cursor: "crosshair" }}
              onPointerDown={startDraw}
              onPointerMove={moveDraw}
              onPointerUp={endDraw}
              onPointerCancel={endDraw}
            >
              <title>Kéo để vẽ vùng object mới</title>
            </rect>
          )}
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
  resumeAsset = null,
  onAssetSaved,
  layout = "stacked",
  assets: assetsProp,
  onAssetsRefresh,
  onAddImages,
  onRemoveImage,
  busyImages = false,
  onProfileChange,
  onDurationChange,
}: {
  images: string[];
  effects: BackgroundEffect[];
  outputProfile: string;
  imageDuration?: string;
  resumeAsset?: { asset: StudioAsset; token: number } | null;
  onEffectsChange: (updater: (current: BackgroundEffect[]) => BackgroundEffect[]) => void;
  onAssetDuration?: (seconds: number) => void;
  onAssetSaved?: () => void;
  layout?: "stacked" | "studio";
  assets?: StudioAsset[];
  onAssetsRefresh?: () => void;
  onAddImages?: () => void;
  onRemoveImage?: (path: string) => void;
  busyImages?: boolean;
  onProfileChange?: (value: string) => void;
  onDurationChange?: (value: number) => void;
}) {
  const imageSignature = images.join("\n");
  const { settings, save: saveSettings } = useSettings();
  const { push } = useNotices();
  const [selectedImage, setSelectedImage] = useState(() => images[0] || "");
  const [prompt, setPrompt] = useState("");
  const [consent, setConsentState] = useState(settings.gemini_consent);
  const [analysis, setAnalysis] = useState<DirectorAnalysis | null>(null);
  const [selectedObjectIds, setSelectedObjectIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [fact, setFact] = useState<InspectFact | null>(null);
  const [inspectError, setInspectError] = useState("");
  const [internalAssets, setInternalAssets] = useState<StudioAsset[]>([]);
  const assets = assetsProp ?? internalAssets;
  const [assetsError, setAssetsError] = useState("");
  const [renderStates, setRenderStates] = useState<Record<string, RenderState>>({});
  const [drawMode, setDrawMode] = useState(false);
  const [draftBox, setDraftBox] = useState<DirectorBox | null>(null);
  const [manualName, setManualName] = useState("");
  const [manualWind, setManualWind] = useState(true);
  const [stageTab, setStageTab] = useState<"image" | "clip">("image");
  const [quickPreview, setQuickPreview] = useState<PreviewResult | null>(null);
  const [quickBusy, setQuickBusy] = useState(false);
  const renderAbortRef = useRef<AbortController | null>(null);
  const renderTargetRef = useRef("");
  const renderJobRef = useRef("");

  useEffect(() => {
    setConsentState(settings.gemini_consent);
  }, [settings.gemini_consent]);

  useEffect(() => {
    setQuickPreview(null);
    setRenderStates({});
    renderAbortRef.current?.abort();
    renderAbortRef.current = null;
    renderTargetRef.current = "";
    if (renderJobRef.current) void cancelRenderJob(renderJobRef.current);
    renderJobRef.current = "";
    setStageTab("image");
  }, [selectedImage, outputProfile, imageDuration]);

  useEffect(() => {
    return () => {
      const jobId = renderJobRef.current;
      if (jobId) void cancelRenderJob(jobId);
    };
  }, []);

  useEffect(() => {
    return () => {
      renderAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    const current = imageSignature.split("\n").filter(Boolean);
    setSelectedImage((value) => (value && current.includes(value) ? value : current[0] || ""));
    setStageTab("image");
  }, [imageSignature]);

  useEffect(() => {
    // Leaving an image cancels its in-flight clip render.
    const target = renderTargetRef.current;
    renderAbortRef.current?.abort();
    renderAbortRef.current = null;
    renderTargetRef.current = "";
    if (target) setImageRender(target, null);
    if (resumeAsset?.asset.source_image === selectedImage) {
      // Restore the opened asset when switching back to its image.
      const restored = analysisFromAsset(resumeAsset.asset);
      if (restored) {
        const plan = restored.effect_plan;
        setAnalysis(restored);
        setSelectedObjectIds(
          plan.selected_object_ids?.length
            ? plan.selected_object_ids
            : restored.objects.map((object) => object.id),
        );
        setError("");
        setDrawMode(false);
        setDraftBox(null);
        setManualName("");
        return;
      }
    }
    setAnalysis(null);
    setSelectedObjectIds([]);
    setError("");
    setDrawMode(false);
    setDraftBox(null);
    setManualName("");
  }, [selectedImage]);

  useEffect(() => {
    if (!resumeAsset) return;
    const { asset } = resumeAsset;
    const analysis = analysisFromAsset(asset);
    if (!analysis) return;
    const plan = analysis.effect_plan;
    setSelectedImage(asset.source_image);
    setAnalysis(analysis);
    setSelectedObjectIds(
      plan.selected_object_ids?.length
        ? plan.selected_object_ids
        : analysis.objects.map((object) => object.id),
    );
    setPrompt("");
    setError("");
    setDrawMode(false);
    setDraftBox(null);
    setManualName("");
    setQuickPreview(null);
    setStageTab("clip");
  }, [resumeAsset?.token]);

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
    if (assetsProp) {
      onAssetsRefresh?.();
      setAssetsError("");
      return;
    }
    try {
      const response = await fetch("/api/studio/assets");
      if (!response.ok) {
        setAssetsError(await readError(response));
        return;
      }
      const data = (await response.json()) as { assets?: StudioAsset[] };
      setInternalAssets(Array.isArray(data.assets) ? data.assets : []);
      setAssetsError("");
    } catch {
      setAssetsError("Không tải được clip đã render. Kiểm tra server Python.");
    }
  }

  useEffect(() => {
    if (!assetsProp) void refreshAssets();
  }, [assetsProp]);

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
    const update = <T extends DirectorObject,>(object: T): T => {
      if (object.id !== id) return object;
      const next = { ...object, box };
      if (object.source === "manual") {
        next.anchors = {
          ...object.anchors,
          pivot: { x: (box.x1 + box.x2) / 2, y: box.y1 },
        };
      } else if (object.anchors?.pivot) {
        next.anchors = {
          ...object.anchors,
          pivot: {
            x: Math.min(box.x2, Math.max(box.x1, object.anchors.pivot.x)),
            y: Math.min(box.y2, Math.max(box.y1, object.anchors.pivot.y)),
          },
        };
      }
      return next;
    };
    setAnalysis((current) => {
      if (!current) return current;
      const source = current.objects.find((object) => object.id === id);
      const objects = current.objects.map(update);
      const mapProtect = (item: { label: string; box: DirectorBox }) => {
        if (!source) return item;
        const oldBox = source.box;
        const centerX = (item.box.x1 + item.box.x2) / 2;
        const centerY = (item.box.y1 + item.box.y2) / 2;
        if (
          centerX < oldBox.x1 ||
          centerX > oldBox.x2 ||
          centerY < oldBox.y1 ||
          centerY > oldBox.y2
        ) {
          return item;
        }
        const scaleX = (box.x2 - box.x1) / Math.max(1e-6, oldBox.x2 - oldBox.x1);
        const scaleY = (box.y2 - box.y1) / Math.max(1e-6, oldBox.y2 - oldBox.y1);
        const mapped = {
          x1: box.x1 + (item.box.x1 - oldBox.x1) * scaleX,
          y1: box.y1 + (item.box.y1 - oldBox.y1) * scaleY,
          x2: box.x1 + (item.box.x2 - oldBox.x1) * scaleX,
          y2: box.y1 + (item.box.y2 - oldBox.y1) * scaleY,
        };
        return {
          ...item,
          box: {
            x1: Math.max(0, Math.min(1, mapped.x1)),
            y1: Math.max(0, Math.min(1, mapped.y1)),
            x2: Math.max(0, Math.min(1, mapped.x2)),
            y2: Math.max(0, Math.min(1, mapped.y2)),
          },
        };
      };
      const protect = (current.protect || []).map(mapProtect);
      const planObjects = current.effect_plan.objects;
      const effectObjects = planObjects ? planObjects.map(update) : objects;
      const planProtect = (current.effect_plan.protect || []).map(mapProtect);
      return {
        ...current,
        objects,
        protect,
        effect_plan: {
          ...current.effect_plan,
          objects: effectObjects,
          protect: planProtect,
        },
      };
    });
  }

  function startManualDraw() {
    if (!analysis) return;
    setDrawMode(true);
    setDraftBox(null);
    setManualName("");
    setManualWind(true);
  }

  function finishManualDraw(box: DirectorBox) {
    setDrawMode(false);
    setDraftBox(box);
    setManualName("");
    setManualWind(true);
  }

  function addManualObject() {
    if (!analysis || !draftBox) return;
    const label = manualName.trim() || "vật thể";
    const existing = new Set(analysis.objects.map((object) => object.id));
    let id = `manual-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
    while (existing.has(id)) {
      id = `${id}-x`;
    }
    const object: DirectorObject = {
      id,
      label,
      box: draftBox,
      anchors: {
        pivot: { x: (draftBox.x1 + draftBox.x2) / 2, y: draftBox.y1 },
        opening: null,
      },
      affordances: manualWind ? ["wind_movable"] : [],
      confidence: 1,
      source: "manual",
    };
    setAnalysis((current) => {
      if (!current) return current;
      const objects = [...current.objects, object];
      const planObjects = current.effect_plan.objects;
      return {
        ...current,
        objects,
        effect_plan: {
          ...current.effect_plan,
          objects: planObjects ? [...planObjects, object] : objects,
        },
      };
    });
    setSelectedObjectIds((current) => (current.includes(id) ? current : [...current, id]));
    setDraftBox(null);
    setManualName("");
    setManualWind(true);
  }

  function cancelManualObject() {
    setDraftBox(null);
    setManualName("");
    setManualWind(true);
  }

  function removeManualObject(id: string) {
    setAnalysis((current) => {
      if (!current) return current;
      const object = current.objects.find((item) => item.id === id);
      if (object?.source !== "manual") return current;
      const objects = current.objects.filter((item) => item.id !== id);
      const planObjects = current.effect_plan.objects;
      return {
        ...current,
        objects,
        effect_plan: {
          ...current.effect_plan,
          objects: planObjects ? planObjects.filter((item) => item.id !== id) : objects,
        },
      };
    });
    setSelectedObjectIds((current) => current.filter((value) => value !== id));
  }

  async function analyze() {
    if (!canAnalyze) return;
    setBusy(true);
    setError("");
    setDrawMode(false);
    setDraftBox(null);
    setManualName("");
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

  async function cancelRenderJob(jobId: string) {
    try {
      await fetch(`/api/studio/render-job/${jobId}/cancel`, {
        method: "POST",
        keepalive: true,
      });
    } catch {
      /* the render process may already be gone */
    }
  }

  function cancelRender() {
    const jobId = renderJobRef.current;
    renderAbortRef.current?.abort();
    renderAbortRef.current = null;
    const target = renderTargetRef.current;
    renderTargetRef.current = "";
    renderJobRef.current = "";
    if (jobId) void cancelRenderJob(jobId);
    if (target) setImageRender(target, null);
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
        if (!asset.effect_plan || JSON.stringify(asset.effect_plan) !== JSON.stringify(plan)) return false;
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
        setQuickPreview(null);
        setStageTab("clip");
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
        renderJobRef.current = jobId;
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
          reuse: !force,
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
      renderJobRef.current = "";
      setImageRender(imagePath, { status: "done", preview });
      setStageTab("clip");
      push({
        tone: "success",
        message: `Đã lưu clip “${fileName(imagePath).replace(/\.[^.]+$/, "")}” vào Thư viện.`,
      });
      void refreshAssets();
      onAssetSaved?.();
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

  async function renderQuick() {
    const plan = existingEffect?.effect_plan || analysis?.effect_plan;
    if (!selectedImage || !plan) {
      push({ tone: "info", message: "Hãy phân tích ảnh và áp dụng hiệu ứng trước khi xem nhanh." });
      return;
    }
    const parsed = Number(imageDuration);
    const seconds = Number.isFinite(parsed) && parsed > 0 ? parsed : 8;
    setQuickBusy(true);
    setError("");
    try {
      const response = await fetch("/api/studio/render", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          image_path: selectedImage,
          effect_plan: plan,
          duration: seconds,
          output_profile: outputProfile,
        }),
      });
      if (!response.ok) {
        setError(await readError(response));
        return;
      }
      const data = (await response.json()) as PreviewResult;
      if (!data?.url) {
        setError("Render nhanh không trả về clip.");
        return;
      }
      setQuickPreview(data);
      setStageTab("clip");
    } catch {
      setError("Không kết nối được server render. Kiểm tra server Python đang chạy.");
    } finally {
      setQuickBusy(false);
    }
  }

  function applyEffect() {
    if (!analysis || !selectedImage) return;
    setQuickPreview(null);
    const manualWindIds = analysis.objects
      .filter(
        (object) =>
          object.source === "manual" &&
          selectedObjectIds.includes(object.id) &&
          (object.affordances || []).includes("wind_movable"),
      )
      .map((object) => object.id);
    let items = analysis.effect_plan.items;
    if (manualWindIds.length > 0) {
      let added = false;
      items = items.map((item) => {
        if (item.effect !== "wind") return item;
        added = true;
        return {
          ...item,
          target_ids: Array.from(new Set([...item.target_ids, ...manualWindIds])),
        };
      });
      if (!added) {
        items = [
          ...items,
          {
            effect: "wind",
            target_ids: manualWindIds,
            params: {
              intensity: 0.55,
              density: 1.0,
              speed: 1.0,
              loop_duration: 8,
              audio: false,
            },
          },
        ];
      }
    }
    const currentEffect = effects.find((item) => item.image_path === selectedImage);
    const effect: BackgroundEffect = {
      image_path: selectedImage,
      image_hash: analysis.image_hash,
      effect_prompt: prompt.trim() || currentEffect?.effect_prompt || "",
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
        items,
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
    setQuickPreview(null);
    setStageTab("image");
    onEffectsChange((current) => current.filter((item) => item.image_path !== selectedImage));
  }

  const imageAssets = assets
    .filter(
      (asset) =>
        asset.source_image === selectedImage ||
        Boolean(existingEffect?.image_hash && asset.image_hash === existingEffect.image_hash) ||
        Boolean(analysis?.image_hash && asset.image_hash === analysis.image_hash),
    )
    .sort((left, right) => {
      const a = Date.parse(left.created || "") || 0;
      const b = Date.parse(right.created || "") || 0;
      return b - a;
    });
  const newestAssetId = imageAssets[0]?.id || "";
  const selectedAsset = imageAssets.find((asset) => asset.id === existingEffect?.asset_id);
  const latestMatchingAsset = existingEffect?.effect_plan
    ? imageAssets.find(
        (asset) =>
          asset.id !== existingEffect.asset_id &&
          asset.effect_plan &&
          JSON.stringify(asset.effect_plan) === JSON.stringify(existingEffect.effect_plan),
      )
    : undefined;
  const currentRenderState = selectedImage ? renderStates[selectedImage] : undefined;
  const clipUrl =
    quickPreview?.url ||
    currentRenderState?.preview?.url ||
    (existingEffect?.asset_id ? `/api/studio/assets/${existingEffect.asset_id}/clip` : "");
  const clipMeta = quickPreview || currentRenderState?.preview || null;
  const [clipWidth, clipHeight] = PROFILE_DIMENSIONS[outputProfile] || PROFILE_DIMENSIONS.landscape_1080p;
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
    const asset = imageAssets.find((item) => item.id === assetId);
    onEffectsChange((current) =>
      current.map((item) => {
        if (item.image_path !== selectedImage) return item;
        if (!assetId) return { ...item, asset_id: null };
        if (!asset?.effect_plan) return { ...item, asset_id: assetId };
        return {
          ...item,
          asset_id: assetId,
          image_hash: asset.image_hash || item.image_hash,
          effect_plan: asset.effect_plan,
          selected_object_ids: asset.effect_plan.selected_object_ids || item.selected_object_ids,
        };
      }),
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
      selected_object_ids: plan.selected_object_ids || [],
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

  const planItems =
    analysis?.effect_plan.items || existingEffect?.effect_plan.items || [];
  const durationValue = Number(imageDuration) || 8;

  const imageSelector =
    images.length > 1 ? (
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
    ) : null;

  const renderSettings = (
    <Disclosure
      title="Thiết lập render"
      summary={`${profileLabel(outputProfile)} · ${imageDuration}s`}
    >
      <div className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-[11px] text-[var(--muted)]">Định dạng</span>
          <select
            className={fieldClass}
            value={outputProfile}
            disabled={!onProfileChange}
            onChange={(event) => {
              onProfileChange?.(event.target.value);
              setStageTab("image");
            }}
          >
            {PROFILE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label} · {option.size}
              </option>
            ))}
            {!PROFILE_OPTIONS.some((option) => option.value === outputProfile) ? (
              <option value={outputProfile}>{outputProfile}</option>
            ) : null}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-[var(--muted)]">Thời lượng clip</span>
          <select
            className={fieldClass}
            value={durationValue}
            disabled={!onDurationChange}
            onChange={(event) => {
              onDurationChange?.(Number(event.target.value));
              setStageTab("image");
            }}
          >
            <option value={8}>8 giây</option>
            <option value={16}>16 giây</option>
            {durationValue !== 8 && durationValue !== 16 ? (
              <option value={durationValue}>{durationValue} giây (theo clip)</option>
            ) : null}
          </select>
        </label>
      </div>
    </Disclosure>
  );

  const drawBlock = analysis ? (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-xs leading-5">
        {drawMode ? (
          <button
            type="button"
            onClick={() => setDrawMode(false)}
            className="rounded-xl border border-[var(--line)] px-3 py-1.5 text-[var(--muted)] hover:border-[var(--gold)]"
          >
            Huỷ vẽ
          </button>
        ) : (
          <button
            type="button"
            onClick={startManualDraw}
            className="rounded-xl border border-[var(--gold)] px-3 py-1.5 font-semibold text-[var(--gold)] hover:bg-black/25"
          >
            Vẽ object
          </button>
        )}
        <span className="text-[var(--muted)]">
          {drawMode
            ? "Kéo chuột trên ảnh để vẽ vùng object mới."
            : "Kéo 4 góc ô để chỉnh kích thước; hoặc vẽ object thủ công."}
        </span>
      </div>
      {draftBox ? (
        <div className="space-y-2 rounded-xl border border-[var(--gold)] bg-black/25 p-3">
          <p className="text-sm font-semibold text-[var(--gold)]">Object thủ công</p>
          <input
            value={manualName}
            onChange={(event) => setManualName(event.target.value)}
            placeholder="Tên object, vd. khăn, lọn tóc, vạt áo"
            className={fieldClass}
          />
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={manualWind}
              onChange={(event) => setManualWind(event.target.checked)}
              className="accent-[var(--gold)]"
            />
            Bay theo gió (wind_movable)
          </label>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={addManualObject}
              className="rounded-xl bg-[var(--gold)] px-3 py-1.5 text-sm font-semibold text-[#1a1206]"
            >
              Thêm object
            </button>
            <button
              type="button"
              onClick={cancelManualObject}
              className="rounded-xl border border-[var(--line)] px-3 py-1.5 text-sm text-[var(--muted)] hover:border-[var(--gold)]"
            >
              Huỷ
            </button>
          </div>
        </div>
      ) : null}
    </div>
  ) : null;

  const sourceStage = (
    <PlanOverlay
      analysis={analysis}
      preview={preview}
      frameWidth={frameWidth}
      frameHeight={frameHeight}
      selectedIds={selectedObjectIds}
      onToggle={toggleObject}
      onBoxChange={changeObjectBox}
      drawMode={drawMode}
      onDrawBox={finishManualDraw}
      pendingBox={draftBox}
    />
  );

  const clipStage = clipUrl ? (
    <div
      className="overflow-hidden rounded-2xl border border-[var(--line)] bg-black"
      style={{ aspectRatio: `${clipWidth} / ${clipHeight}` }}
    >
      <video
        key={clipUrl}
        src={clipUrl}
        controls
        autoPlay
        loop
        muted
        playsInline
        className="h-full w-full object-contain"
      />
    </div>
  ) : (
    <div className="grid min-h-40 place-items-center rounded-2xl border border-dashed border-[var(--line)] bg-black/20 p-6 text-center text-xs leading-5 text-[var(--muted)]">
      Chưa có clip. Bấm Áp dụng để tạo clip chuẩn, hoặc Xem nhanh sau khi đã có kế hoạch hiệu ứng.
    </div>
  );

  const stage = (
    <div className="space-y-2">
      {layout === "studio" ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Segmented<"image" | "clip">
            value={stageTab}
            onChange={setStageTab}
            ariaLabel="Xem ảnh hay clip"
            options={[
              { value: "image", label: "Ảnh" },
              { value: "clip", label: "Clip" },
            ]}
          />
          <div className="flex items-center gap-2">
            <Badge tone="muted">
              {profileLabel(outputProfile)} · {imageDuration}s
            </Badge>
            {clipMeta ? (
              <Badge tone="success">
                {clipMeta.width}×{clipMeta.height}
              </Badge>
            ) : null}
          </div>
        </div>
      ) : null}
      {layout === "studio" && stageTab === "clip" ? clipStage : sourceStage}
      {drawBlock}
    </div>
  );

  const imageMeta = (
    <div className="space-y-1 text-[11px] leading-4 text-[var(--muted)]">
      <p className="truncate text-xs text-[var(--paper)]">
        {fileName(selectedImage)}
        {fact && fact.width > 0 ? ` · ${fact.width}×${fact.height}` : ""}
      </p>
      {fact?.note ? <p>{fact.note}</p> : null}
      {portrait ? (
        <p>9:16: lời nằm trên vùng an toàn đáy; vùng trên/phải dành cho giao diện ứng dụng.</p>
      ) : null}
    </div>
  );

  const promptBlock = (
    <div>
      <label className="mb-1.5 block text-sm text-[var(--muted)]" htmlFor="ai-effects-prompt">
        Mô tả hiệu ứng
      </label>
      <textarea
        id="ai-effects-prompt"
        className={`${fieldClass} min-h-16 resize-y leading-6`}
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        placeholder="Ví dụ: mưa nhẹ trên phố, khói bốc từ bát mỳ…"
      />
      <div className="mt-2 flex flex-wrap gap-1.5">
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
  );

  const consentBlock = (
    <label className="flex items-start gap-2 rounded-xl border border-[var(--line)] bg-black/20 px-3 py-2">
      <input
        type="checkbox"
        checked={consent}
        onChange={(event) => {
          const next = event.target.checked;
          setConsentState(next);
          void saveSettings({ gemini_consent: next });
        }}
        className="mt-0.5 accent-[var(--gold)]"
      />
      <span className="text-xs leading-5">
        Gửi ảnh lên Google Gemini để phân tích
        <span className="mt-0.5 block text-[11px] leading-4 text-[var(--muted)]">
          Chỉ ảnh đang chọn và mô tả được gửi khi bấm Phân tích. Tuỳ chọn được lưu trong Cài đặt.
        </span>
      </span>
    </label>
  );

  const analyzeBlock = (
    <div className="space-y-2">
      <button
        type="button"
        onClick={analyze}
        disabled={!canAnalyze}
        className="w-full rounded-xl bg-[var(--gold)] px-4 py-2.5 text-sm font-semibold text-[#1a1206] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {busy ? "Đang phân tích ảnh…" : "Phân tích ảnh"}
      </button>
      {busy ? (
        <p className="text-[11px] leading-4 text-[var(--muted)]">
          Gemini đang đọc ảnh và lập kế hoạch hiệu ứng…
        </p>
      ) : analyzeHint ? (
        <p className="text-[11px] leading-4 text-[var(--muted)]">{analyzeHint}</p>
      ) : null}
    </div>
  );

  const errorBlock = error || inspectError ? (
    <p
      className={`whitespace-pre-line rounded-xl border px-3 py-2 text-xs leading-5 ${
        error
          ? "border-red-900 bg-red-950/60 text-red-100"
          : "border-amber-900/70 bg-amber-950/30 text-amber-100"
      }`}
    >
      {error || inspectError}
    </p>
  ) : null;

  const analysisMeta = analysis ? (
    <div className="space-y-1 rounded-xl border border-[var(--line)] bg-black/20 px-2.5 py-2 text-[11px] leading-4">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[var(--muted)]">{analysis.model}</span>
        {analysis.cached ? <Badge tone="gold">Cache</Badge> : null}
        {analysis.usage ? <span className="text-[var(--muted)]">{usageText(analysis.usage)}</span> : null}
      </div>
      {analysis.intent_fallback ? (
        <p className="text-amber-100/90">
          Không gọi được Gemini để hiểu prompt; đang dùng từ khoá dự phòng.
        </p>
      ) : null}
      {analysis.scene ? <p className="text-[var(--muted)]">{analysis.scene}</p> : null}
    </div>
  ) : null;

  const paramsBlock = planItems.length > 0 ? (
    <div className="space-y-2">
      {planItems.map((item, index) => {
        const keys = EFFECT_PARAMS[item.effect] || ["intensity"];
        return (
          <div key={`${item.effect}-${index}`} className="space-y-1">
            <p className="text-[11px] font-medium">{effectLabel(item.effect)}</p>
            {keys.map((key) => {
              const meta = PARAM_META[key];
              const raw = item.params?.[key];
              const value = typeof raw === "number" ? Math.round(raw * 100) : meta.default;
              const min = key === "speed" && item.effect === "wind" ? 0 : meta.min;
              return (
                <label key={key} className="block">
                  <span className="flex items-center justify-between text-[10px] text-[var(--muted)]">
                    <span>{meta.label}</span>
                    <span>{value}%</span>
                  </span>
                  <input
                    type="range"
                    min={min}
                    max={meta.max}
                    step={meta.step}
                    value={value}
                    onChange={(event) => updateParam(index, key, Number(event.target.value))}
                    className="mt-0.5 w-full accent-[var(--gold)]"
                  />
                </label>
              );
            })}
          </div>
        );
      })}
      <p className="text-[10px] leading-4 text-[var(--muted)]">
        Chỉnh cục bộ, không gọi lại Gemini. Bấm Áp dụng để lưu.
      </p>
    </div>
  ) : null;

  const appliedBlock = existingEffect ? (
    <div className="space-y-2 rounded-xl border border-[var(--gold)] bg-black/25 px-2.5 py-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-[var(--gold)]">Đã áp dụng</p>
          <p className="truncate text-[11px] leading-4 text-[var(--muted)]">
            {existingEffect.effect_prompt}
            {planItems.length ? ` · ${planItems.map((item) => item.effect).join(", ")}` : ""}
          </p>
        </div>
        <button type="button" onClick={removeEffect} className="shrink-0 text-[11px] text-[var(--muted)] hover:text-white">
          Xoá
        </button>
      </div>
      {currentRenderState?.status === "rendering" ? (
        <div className="space-y-1">
          <ProgressBar percent={currentRenderState.percent || 0} />
          <div className="flex items-center justify-between gap-2 text-[11px] text-[var(--muted)]">
            <span>
              Đang tạo clip…{currentRenderState.percent ? ` ${currentRenderState.percent}%` : ""}
            </span>
            <button type="button" onClick={cancelRender} className="text-red-200 hover:text-white">
              Huỷ
            </button>
          </div>
        </div>
      ) : null}
      {currentRenderState?.status === "error" ? (
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-red-200">{currentRenderState.error}</span>
          <button type="button" onClick={() => void startRender(existingEffect, true)} className="text-[var(--gold)] hover:text-white">
            Thử lại
          </button>
        </div>
      ) : null}
      {existingEffect.asset_id &&
      currentRenderState?.status !== "rendering" &&
      currentRenderState?.status !== "error" ? (
        <p className="text-[11px] text-emerald-200">Đã có clip trong thư viện</p>
      ) : null}
      <label className="block">
        <span className="mb-1 block text-[10px] text-[var(--muted)]">Clip đã render</span>
        <div className="flex gap-2">
          <select
            className={fieldClass}
            value={existingEffect.asset_id || ""}
            onChange={(event) => chooseAsset(event.target.value)}
          >
            <option value="">Render từ effect plan</option>
            {imageAssets.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {clipOptionLabel(asset, newestAssetId)}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void refreshAssets()}
            className="shrink-0 rounded-xl border border-[var(--line)] px-2.5 text-[11px] hover:border-[var(--gold)]"
          >
            Tải lại
          </button>
        </div>
        {imageAssets.length > 1 ? (
          <p className="mt-1 text-[10px] leading-4 text-[var(--muted)]">
            {imageAssets.length} clip cho ảnh này · xếp mới nhất trước.
          </p>
        ) : null}
        {selectedAsset ? (
          <p
            className={`mt-0.5 text-[10px] leading-4 ${
              selectedAsset.id === newestAssetId ? "text-emerald-200" : "text-amber-200"
            }`}
          >
            {selectedAsset.id === newestAssetId
              ? "Đang dùng bản mới nhất."
              : "Đang dùng bản cũ hơn; bản mới nhất ở đầu danh sách."}
          </p>
        ) : null}
      </label>
      {latestMatchingAsset ? (
        <button
          type="button"
          onClick={() => chooseAsset(latestMatchingAsset.id)}
          className="text-left text-[11px] text-[var(--gold)] hover:text-white"
        >
          Có clip mới khớp plan: dùng “{latestMatchingAsset.name}”
          {latestMatchingAsset.created ? ` · ${assetDateLabel(latestMatchingAsset.created)}` : ""}
        </button>
      ) : null}
      {currentRenderState?.status !== "rendering" && currentRenderState?.status !== "error" ? (
        <button
          type="button"
          onClick={() => void startRender(existingEffect, true)}
          className="text-[11px] text-[var(--gold)] hover:text-white"
        >
          {existingEffect.asset_id ? "Tạo lại clip chuẩn" : "Tạo clip chuẩn"}
        </button>
      ) : null}
      {assetsError ? <p className="text-[11px] text-red-200">{assetsError}</p> : null}
      {assetProfileMismatch ? (
        <p className="text-[10px] leading-4 text-amber-200">
          Clip ở {selectedAsset?.profile_id}, khác định dạng hiện tại ({outputProfile}). Export sẽ fallback render từ effect plan.
        </p>
      ) : null}
      {assetMismatch ? (
        <div className="space-y-1">
          <p className="text-[10px] leading-4 text-amber-200">
            Clip dài {assetSeconds?.toFixed(1)}s, khác “Mỗi ảnh nền” ({imageDuration}s). Export sẽ fallback render từ effect plan.
          </p>
          {onAssetDuration && assetSeconds ? (
            <button
              type="button"
              onClick={() => onAssetDuration(assetSeconds)}
              className="text-[10px] text-[var(--gold)] hover:text-white"
            >
              Đặt “Mỗi ảnh nền” = {assetSeconds.toFixed(1)}s
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  ) : null;

  const assetPickBlock = !existingEffect && selectedImage && imageAssets.length > 0 ? (
    <div className="space-y-1 rounded-xl border border-[var(--line)] bg-black/20 px-2.5 py-2">
      <p className="text-[11px] text-[var(--gold)]">Dùng clip Studio cho ảnh này</p>
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
            {clipOptionLabel(asset, newestAssetId)}
          </option>
        ))}
      </select>
      <p className="text-[10px] leading-4 text-[var(--muted)]">
        {imageAssets.length > 1 ? `${imageAssets.length} clip · mới nhất ở đầu danh sách. ` : ""}
        Chọn clip sẽ tạo hiệu ứng chỉ dùng asset, không cần phân tích Gemini.
      </p>
    </div>
  ) : null;

  const objectsBlock = analysis && (analysis.objects || []).length > 0 ? (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-xs text-[var(--muted)]">Object cho hiệu ứng</span>
        <span className="text-[11px] text-[var(--muted)]">
          {selectedObjectIds.length}/{analysis.objects.length} chọn
        </span>
      </div>
      <ul className={layout === "studio" ? "space-y-1.5" : "max-h-64 space-y-2 overflow-auto pr-1"}>
        {analysis.objects.map((object) => {
          const checked = selectedObjectIds.includes(object.id);
          const percent =
            object.confidence <= 1
              ? Math.round(Math.max(0, object.confidence) * 100)
              : Math.round(object.confidence);
          return (
            <li
              key={object.id}
              className={`rounded-lg border px-2 py-1.5 transition ${
                checked ? "border-[var(--gold)] bg-black/25" : "border-[var(--line)] bg-black/15"
              }`}
            >
              <label className="flex cursor-pointer items-start gap-2">
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleObject(object.id)}
                  className="mt-0.5 accent-[var(--gold)]"
                />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-baseline gap-1.5">
                    <span className="text-xs">{object.label}</span>
                    <span className="text-[10px] text-[var(--muted)]">{percent}%</span>
                    {object.source === "manual" ? <Badge tone="gold">thủ công</Badge> : null}
                  </span>
                  {(checked || layout !== "studio") && object.affordances.length > 0 ? (
                    <span className="mt-1 flex flex-wrap gap-1">
                      {object.affordances.map((affordance) => (
                        <span
                          key={affordance}
                          className="rounded-full border border-[var(--line)] px-1.5 py-0.5 text-[10px] text-[var(--muted)]"
                        >
                          {affordance}
                        </span>
                      ))}
                    </span>
                  ) : null}
                </span>
              </label>
              {object.source === "manual" ? (
                <button
                  type="button"
                  onClick={() => removeManualObject(object.id)}
                  className="mt-1 text-[10px] text-[var(--muted)] hover:text-red-200"
                >
                  Xoá object thủ công
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  ) : null;

  const regionsBlock =
    regions.length > 0 || protect.length > 0 ? (
      <div className="rounded-xl border border-[var(--line)] bg-black/20 px-2.5 py-2 text-[10px] leading-4 text-[var(--muted)]">
        {regions.length > 0 ? (
          <p>Vùng: {regions.map((region) => `${region.id} (${region.polygon.length} điểm)`).join(", ")}</p>
        ) : null}
        {protect.length > 0 ? (
          <p className={regions.length > 0 ? "mt-1" : ""}>Bảo vệ: {protect.map((item) => item.label).join(", ")}</p>
        ) : null}
      </div>
    ) : null;

  const applyBar = analysis ? (
    <div
      className={`rounded-2xl border border-[var(--gold)] bg-[var(--panel)] p-2 ${
        layout === "studio" ? "" : "sticky bottom-2 z-10 shadow-lg"
      }`}
    >
      <button
        type="button"
        onClick={applyEffect}
        disabled={busy}
        className="w-full rounded-xl bg-[var(--gold)] px-4 py-2.5 text-sm font-semibold text-[#1a1206] transition hover:brightness-105 disabled:opacity-50"
      >
        {existingEffect ? "Áp dụng lại" : "Áp dụng"}
      </button>
      <p className="mt-1 text-center text-[10px] leading-4 text-[var(--muted)]">
        Áp dụng xong clip chuẩn tự tạo ở nền. Xuất video vẫn dựng trực tiếp nếu clip chưa xong.
      </p>
    </div>
  ) : null;

  const stageActions =
    layout === "studio" && (analysis || existingEffect) ? (
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => void renderQuick()}
          disabled={quickBusy}
          className={btnSecondary}
        >
          {quickBusy ? "Đang render nhanh…" : "Xem nhanh (640px)"}
        </button>
        {quickBusy ? (
          <span className="self-center text-[10px] text-[var(--muted)]">
            Đang dựng bản 640px; có thể mất vài chục giây.
          </span>
        ) : null}
      </div>
    ) : null;

  if (images.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-[var(--line)] bg-black/15 p-6 text-center text-sm leading-6 text-[var(--muted)]">
        <p>Chưa có ảnh nền để phân tích. Thêm ảnh ở mục Hình; nếu bỏ trống, ảnh bìa sẽ được dùng.</p>
        {onAddImages ? (
          <button type="button" onClick={onAddImages} className={`${btnPrimary} mt-3`}>
            Chọn ảnh
          </button>
        ) : null}
      </div>
    );
  }

  if (layout === "studio") {
    return (
      <div className="grid items-start gap-4 xl:grid-cols-[220px_minmax(0,1fr)_340px]">
        <aside className="space-y-3 rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-3 xl:sticky xl:top-20 xl:max-h-[calc(100vh-6rem)] xl:overflow-y-auto">
          <ImageTray
            paths={images}
            selected={selectedImage}
            outputProfile={outputProfile}
            onSelect={(path) => {
              setSelectedImage(path);
              setStageTab("image");
            }}
            onRemove={(path) => onRemoveImage?.(path)}
            onAdd={() => onAddImages?.()}
            busyAdd={busyImages}
          />
          {renderSettings}
          {imageMeta}
          {assetsError ? <p className="text-[11px] text-red-200">{assetsError}</p> : null}
        </aside>

        <section className="min-w-0 space-y-3 rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-3 sm:p-4">
          {stage}
          {promptBlock}
          {consentBlock}
          {analyzeBlock}
          {errorBlock}
          {stageActions}
          {applyBar}
        </section>

        <aside className="space-y-2 rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-3 xl:sticky xl:top-20 xl:max-h-[calc(100vh-6rem)] xl:overflow-y-auto">
          {analysisMeta}
          {paramsBlock ? (
            <Disclosure title="Hiệu ứng" summary={`${planItems.length}`} defaultOpen>
              {paramsBlock}
            </Disclosure>
          ) : null}
          {appliedBlock}
          {assetPickBlock}
          {objectsBlock ? (
            <Disclosure
              title="Object"
              summary={`${selectedObjectIds.length}/${analysis?.objects.length || 0}`}
              defaultOpen
            >
              {objectsBlock}
            </Disclosure>
          ) : null}
          {regionsBlock}
        </aside>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {imageSelector}
      {stage}
      {imageMeta}
      {promptBlock}
      {consentBlock}
      {analyzeBlock}
      {errorBlock}
      {analysisMeta}
      {paramsBlock ? (
        <div className="rounded-xl border border-[var(--line)] bg-black/20 p-3">{paramsBlock}</div>
      ) : null}
      {appliedBlock}
      {assetPickBlock}
      {objectsBlock}
      {regionsBlock}
      {applyBar}
      <p className="text-xs leading-5 text-[var(--muted)]">
        Hiện render mưa, tuyết, nước, khói, gió/sway, lửa, tàn lửa, chim, tia nắng, sương mù và nhịp sáng.
      </p>
    </div>
  );
}
