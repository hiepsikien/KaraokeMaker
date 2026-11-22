"use client";

import { Badge, SectionTitle, btnSecondary } from "@/components/ui/primitives";
import { inputClass } from "@/components/ui/field";
import {
  OVERLAY_DEFAULTS,
  overlayEffect,
  type OverlayEffect,
  type OverlayKind,
} from "@/lib/types";

type ParamMeta = {
  label: string;
  min: number;
  max: number;
  step: number;
  suffix?: string;
};

const CARD_META: Record<OverlayKind, { title: string; description: string; params: string[] }> = {
  snow: {
    title: "Tuyết",
    description: "Hạt tuyết 3 lớp, không cần Gemini.",
    params: ["intensity", "density", "speed", "size"],
  },
  leaf: {
    title: "Lá",
    description: "Lá rơi theo gió, có palette mùa.",
    params: ["intensity", "density", "speed", "size", "palette"],
  },
  bokeh: {
    title: "Bokeh",
    description: "Đốm sáng phim mờ, blend screen.",
    params: ["intensity", "density", "size", "speed", "opacity", "blend", "palette"],
  },
  lightleak: {
    title: "Light leak",
    description: "Dải sáng chéo, nhấp nháy nhẹ.",
    params: ["intensity", "speed", "angle", "opacity", "blend", "palette"],
  },
};

const PARAM_META: Record<string, ParamMeta> = {
  intensity: { label: "Cường độ", min: 0, max: 100, step: 5, suffix: "%" },
  density: { label: "Mật độ", min: 0, max: 150, step: 5, suffix: "%" },
  speed: { label: "Tốc độ", min: 20, max: 200, step: 5, suffix: "%" },
  size: { label: "Kích thước", min: 60, max: 160, step: 5, suffix: "%" },
  opacity: { label: "Độ đậm", min: 5, max: 80, step: 5, suffix: "%" },
  angle: { label: "Góc", min: 0, max: 180, step: 5, suffix: "°" },
};

const LEAF_PALETTES = [
  { value: "spring", label: "Xuân" },
  { value: "autumn", label: "Thu" },
  { value: "mixed", label: "Trộn" },
];

const ALL_PALETTES = [
  { value: "auto", label: "Tự động" },
  { value: "warm", label: "Ấm" },
  { value: "cool", label: "Lạnh" },
  { value: "pastel", label: "Pastel" },
  { value: "neon", label: "Neon" },
];

const BLENDS = [
  { value: "screen", label: "Screen" },
  { value: "soft_light", label: "Soft light" },
  { value: "normal", label: "Thường" },
];

function numberParam(params: Record<string, number | string>, key: string, fallback: number) {
  const value = Number(params[key]);
  return Number.isFinite(value) ? value : fallback;
}

export function OverlayEffectsEditor({
  value,
  onChange,
  onDrawCloud,
}: {
  value: OverlayEffect[];
  onChange: (updater: (current: OverlayEffect[]) => OverlayEffect[]) => void;
  onDrawCloud?: () => void;
}) {
  const effects = Array.isArray(value) ? value : [];

  function find(kind: OverlayKind) {
    return effects.find((item) => item.kind === kind);
  }

  function patch(kind: OverlayKind, updater: (effect: OverlayEffect) => OverlayEffect) {
    onChange((current) => {
      const list = Array.isArray(current) ? current : [];
      const existing = list.find((item) => item.kind === kind);
      if (!existing) {
        return [...list, updater(overlayEffect(kind))];
      }
      return list.map((item) => (item.kind === kind ? updater(item) : item));
    });
  }

  function setEnabled(kind: OverlayKind, enabled: boolean) {
    patch(kind, (effect) => ({ ...effect, enabled }));
  }

  function setParam(kind: OverlayKind, key: string, raw: string | number) {
    patch(kind, (effect) => ({
      ...effect,
      params: { ...effect.params, [key]: raw },
    }));
  }

  function setMask(kind: OverlayKind, mask: OverlayEffect["mask"]) {
    patch(kind, (effect) => ({ ...effect, mask }));
  }

  const enabledCount = effects.filter((item) => item.enabled).length;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionTitle>Hiệu ứng thêm (không cần AI)</SectionTitle>
        <Badge tone={enabledCount ? "gold" : "muted"}>{enabledCount} đang bật</Badge>
      </div>
      <p className="text-sm leading-6 text-[var(--muted)]">
        Phủ tuyết, lá, bokeh hoặc light leak lên toàn video. Không gửi ảnh đi đâu, không cần đồng ý Gemini.
        Khi bật “tránh dải lời”, hạt sẽ không che vùng chữ.
      </p>

      <div className="grid gap-3">
        {(Object.keys(CARD_META) as OverlayKind[]).map((kind) => {
          const meta = CARD_META[kind];
          const effect = find(kind);
          const enabled = Boolean(effect?.enabled);
          const params = { ...OVERLAY_DEFAULTS[kind], ...(effect?.params || {}) };
          const palettes = kind === "leaf" ? LEAF_PALETTES : ALL_PALETTES;
          const showPalette = meta.params.includes("palette");
          return (
            <div
              key={kind}
              className={`rounded-2xl border p-3 transition ${
                enabled ? "border-[var(--gold)]/60 bg-black/25" : "border-[var(--line)] bg-black/10"
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <label className="flex min-w-0 cursor-pointer items-start gap-2.5">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={(event) => setEnabled(kind, event.target.checked)}
                    className="mt-1 accent-[var(--gold)]"
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-medium">{meta.title}</span>
                    <span className="block text-xs leading-5 text-[var(--muted)]">{meta.description}</span>
                  </span>
                </label>
                {enabled && effect ? (
                  <select
                    aria-label={`Che vùng chữ của ${meta.title}`}
                    value={effect.mask || "avoid_lyrics"}
                    onChange={(event) => setMask(kind, event.target.value as OverlayEffect["mask"])}
                    className={`${inputClass} w-auto shrink-0 py-1.5 text-xs`}
                  >
                    <option value="avoid_lyrics">Tránh dải lời</option>
                    <option value="none">Phủ toàn khung</option>
                  </select>
                ) : null}
              </div>

              {enabled ? (
                <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  {meta.params
                    .filter((key) => key !== "palette" && key !== "blend")
                    .map((key) => {
                      const settings = PARAM_META[key];
                      if (!settings) return null;
                      const current = numberParam(params, key, Number(OVERLAY_DEFAULTS[kind][key] ?? 0));
                      return (
                        <label key={key} className="block">
                          <span className="mb-1 flex items-center justify-between text-[11px] text-[var(--muted)]">
                            <span>{settings.label}</span>
                            <span className="tabular-nums text-[var(--paper)]">
                              {Math.round(current)}
                              {settings.suffix || ""}
                            </span>
                          </span>
                          <input
                            type="range"
                            min={settings.min}
                            max={settings.max}
                            step={settings.step}
                            value={current}
                            onChange={(event) => setParam(kind, key, Number(event.target.value))}
                            className="w-full accent-[var(--gold)]"
                          />
                        </label>
                      );
                    })}
                  {showPalette ? (
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-[var(--muted)]">Palette</span>
                      <select
                        value={String(params.palette || palettes[0].value)}
                        onChange={(event) => setParam(kind, "palette", event.target.value)}
                        className={inputClass}
                      >
                        {palettes.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  {meta.params.includes("blend") ? (
                    <label className="block">
                      <span className="mb-1 block text-[11px] text-[var(--muted)]">Blend</span>
                      <select
                        value={String(params.blend || "screen")}
                        onChange={(event) => setParam(kind, "blend", event.target.value)}
                        className={inputClass}
                      >
                        {BLENDS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>

      <div className="rounded-2xl border border-dashed border-[var(--line)] bg-black/10 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="text-sm font-medium">Mây (bồng bềnh)</p>
            <p className="text-xs leading-5 text-[var(--muted)]">
              Mây được làm bằng cách khoanh vùng trong ảnh nền, không phải lớp phủ toàn video. Mở “Hiệu ứng nền” để vẽ.
            </p>
          </div>
          <button type="button" className={btnSecondary} onClick={onDrawCloud} disabled={!onDrawCloud}>
            Chọn vùng mây
          </button>
        </div>
      </div>
    </div>
  );
}
