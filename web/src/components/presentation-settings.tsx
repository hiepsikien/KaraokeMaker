"use client";

import { useEffect, useRef, useState } from "react";

import { Field, Select, TextInput } from "@/components/ui/field";
import { SectionTitle } from "@/components/ui/primitives";
import type { Presentation, Typography } from "@/lib/types";

function ColorField({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[11px] text-[var(--muted)]">{label}</span>
      <span className="flex items-center gap-2">
        <input
          type="color"
          value={value || "#ffffff"}
          onChange={(event) => onChange(event.target.value)}
          aria-label={label}
          className="h-9 w-11 shrink-0 cursor-pointer rounded-lg border border-[var(--line)] bg-black/25"
        />
        <TextInput
          value={value}
          onChange={(event) => onChange(event.target.value)}
          spellCheck={false}
          className="min-w-0 py-1.5 font-mono text-xs uppercase"
        />
      </span>
      {hint ? <span className="mt-1 block text-[10px] leading-4 text-[var(--muted)]">{hint}</span> : null}
    </label>
  );
}

function SliderField({
  label,
  value,
  min,
  max,
  step = 1,
  suffix = "",
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center justify-between text-[11px] text-[var(--muted)]">
        <span>{label}</span>
        <span className="tabular-nums text-[var(--paper)]">
          {value}
          {suffix}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
        className="w-full accent-[var(--gold)]"
      />
    </label>
  );
}

function OptionalNumberField({
  label,
  value,
  min,
  max,
  step = 1,
  suffix,
  placeholder = "Mặc định",
  onChange,
}: {
  label: string;
  value: number | null;
  min: number;
  max: number;
  step?: number;
  suffix?: string;
  placeholder?: string;
  onChange: (value: number | null) => void;
}) {
  return (
    <Field label={suffix ? `${label} (${suffix})` : label}>
      <TextInput
        type="number"
        min={min}
        max={max}
        step={step}
        value={value == null ? "" : value}
        placeholder={placeholder}
        onChange={(event) => {
          const raw = event.target.value.trim();
          if (raw === "") {
            onChange(null);
            return;
          }
          const parsed = Number(raw);
          if (!Number.isFinite(parsed)) return;
          onChange(Math.max(min, Math.min(max, parsed)));
        }}
      />
    </Field>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="accent-[var(--gold)]"
      />
      {label}
    </label>
  );
}

export function PresentationSettings({
  presentation,
  typography,
  onPresentationChange,
  onTypographyChange,
  onBrowseOutro,
  outroBusy = false,
  coverImage = "",
  title = "",
  artist = "",
  coverLine = "",
  outputProfile = "landscape_1080p",
}: {
  presentation: Presentation;
  typography: Typography;
  onPresentationChange: (updater: (current: Presentation) => Presentation) => void;
  onTypographyChange: (updater: (current: Typography) => Typography) => void;
  onBrowseOutro?: () => void;
  outroBusy?: boolean;
  coverImage?: string;
  title?: string;
  artist?: string;
  coverLine?: string;
  outputProfile?: string;
}) {
  const cover = presentation.cover;
  const interlude = presentation.interlude;
  const outro = presentation.outro;
  const presentationKey = JSON.stringify(presentation.cover);
  const previewAspect =
    outputProfile === "portrait_1080x1920"
      ? "9 / 16"
      : outputProfile === "square_1080"
        ? "1 / 1"
        : outputProfile === "portrait_4x5"
          ? "4 / 5"
          : "16 / 9";
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const previewRef = useRef("");

  useEffect(() => {
    if (!coverImage) {
      setPreviewUrl("");
      setPreviewError("");
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setPreviewBusy(true);
      setPreviewError("");
      try {
        const response = await fetch("/api/presentation/cover-preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            image_path: coverImage,
            title,
            artist,
            cover_line: coverLine,
            output_profile: outputProfile,
            presentation,
          }),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("preview");
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        if (previewRef.current) URL.revokeObjectURL(previewRef.current);
        previewRef.current = url;
        setPreviewUrl(url);
      } catch (error) {
        if (!controller.signal.aborted) setPreviewError("Chưa render được preview bìa.");
      } finally {
        if (!controller.signal.aborted) setPreviewBusy(false);
      }
    }, 450);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [coverImage, title, artist, coverLine, outputProfile, presentationKey]);

  useEffect(() => () => {
    if (previewRef.current) URL.revokeObjectURL(previewRef.current);
  }, []);

  function patchCover(patch: Partial<Presentation["cover"]>) {
    onPresentationChange((current) => ({ ...current, cover: { ...current.cover, ...patch } }));
  }

  function patchInterlude(patch: Partial<Presentation["interlude"]>) {
    onPresentationChange((current) => ({
      ...current,
      interlude: { ...current.interlude, ...patch },
    }));
  }

  function patchOutro(patch: Partial<Presentation["outro"]>) {
    onPresentationChange((current) => ({ ...current, outro: { ...current.outro, ...patch } }));
  }

  function patchTypography(patch: Partial<Typography>) {
    onTypographyChange((current) => ({ ...current, ...patch }));
  }

  return (
    <div className="space-y-5">
      <section className="space-y-3">
        <SectionTitle>Trang bìa</SectionTitle>
        {coverImage ? (
          <div className="relative overflow-hidden rounded-xl border border-[var(--line)] bg-black/40">
            <div className="w-full" style={{ aspectRatio: previewAspect }}>
              {previewUrl ? (
                <img src={previewUrl} alt="Preview trang bìa" className="h-full w-full object-contain" />
              ) : (
                <div className="flex h-full items-center justify-center px-3 text-center text-xs text-[var(--muted)]">
                  {previewBusy ? "Đang render preview…" : previewError || "Chưa có preview."}
                </div>
              )}
            </div>
            {previewError && previewUrl ? (
              <div className="absolute inset-x-2 top-2 rounded-lg bg-amber-950/80 px-2 py-1 text-[10px] text-amber-100">
                {previewError} Đang hiện bản cũ.
              </div>
            ) : null}
            <span className="absolute bottom-2 right-2 rounded-full bg-black/60 px-2 py-0.5 text-[10px] text-[var(--muted)]">
              {previewBusy ? "Đang cập nhật…" : `Preview · ${outputProfile}`}
            </span>
          </div>
        ) : (
          <p className="rounded-xl border border-dashed border-[var(--line)] px-3 py-2 text-xs text-[var(--muted)]">
            Chọn ảnh bìa để xem preview.
          </p>
        )}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Bố cục">
            <Select value={cover.layout} onChange={(event) => patchCover({ layout: event.target.value })}>
              <option value="center">Giữa khung</option>
              <option value="bottom_left">Dưới trái</option>
              <option value="bottom_center">Dưới giữa</option>
            </Select>
          </Field>
          <Field label="Chữ nhỏ trên tên bài">
            <TextInput
              value={cover.label}
              onChange={(event) => patchCover({ label: event.target.value })}
              placeholder="KARAOKE"
            />
          </Field>
          <OptionalNumberField
            label="Cỡ tên bài"
            value={cover.title_size}
            min={20}
            max={180}
            suffix="px"
            onChange={(value) => patchCover({ title_size: value })}
          />
          <OptionalNumberField
            label="Cỡ tác giả"
            value={cover.artist_size}
            min={14}
            max={140}
            suffix="px"
            onChange={(value) => patchCover({ artist_size: value })}
          />
          <OptionalNumberField
            label="Cỡ dòng phụ"
            value={cover.line_size}
            min={12}
            max={96}
            suffix="px"
            onChange={(value) => patchCover({ line_size: value })}
          />
          <div className="flex flex-col justify-end gap-2 pb-1">
            <Toggle
              label="Hiện chữ nhỏ trên tên"
              checked={cover.show_label}
              onChange={(value) => patchCover({ show_label: value })}
            />
            <Toggle
              label="Viết hoa toàn bộ"
              checked={cover.uppercase}
              onChange={(value) => patchCover({ uppercase: value })}
            />
            <Toggle label="Đổ bóng chữ" checked={cover.shadow} onChange={(value) => patchCover({ shadow: value })} />
          </div>
          <ColorField
            label="Màu tên bài"
            value={cover.text_color}
            onChange={(value) => patchCover({ text_color: value })}
          />
          <ColorField
            label="Màu tác giả / nhãn"
            value={cover.accent_color}
            onChange={(value) => patchCover({ accent_color: value })}
          />
          <ColorField
            label="Màu dòng phụ"
            value={cover.line_color}
            onChange={(value) => patchCover({ line_color: value })}
          />
          <SliderField
            label="Viền chữ"
            value={cover.stroke}
            min={0}
            max={10}
            onChange={(value) => patchCover({ stroke: value })}
          />
          <SliderField
            label="Làm mờ nền"
            value={cover.bg_blur}
            min={0}
            max={40}
            suffix=" px"
            onChange={(value) => patchCover({ bg_blur: value })}
          />
          <SliderField
            label="Tối nền"
            value={cover.bg_darken}
            min={0}
            max={80}
            suffix="%"
            onChange={(value) => patchCover({ bg_darken: value })}
          />
          <SliderField
            label="Màn mờ"
            value={cover.veil}
            min={0}
            max={100}
            suffix="%"
            onChange={(value) => patchCover({ veil: value })}
          />
          <SliderField
            label="Tối góc"
            value={cover.vignette}
            min={0}
            max={100}
            suffix="%"
            onChange={(value) => patchCover({ vignette: value })}
          />
          <Field label="Dải tối">
            <Select value={cover.scrim} onChange={(event) => patchCover({ scrim: event.target.value })}>
              <option value="none">Không</option>
              <option value="bottom">Dưới khung</option>
              <option value="full">Toàn khung</option>
            </Select>
          </Field>
          <SliderField
            label="Độ đậm dải tối"
            value={cover.scrim_opacity}
            min={0}
            max={100}
            suffix="%"
            onChange={(value) => patchCover({ scrim_opacity: value })}
          />
        </div>
      </section>

      <section className="space-y-3 border-t border-[var(--line)] pt-4">
        <SectionTitle>Nhạc dạo</SectionTitle>
        <p className="text-xs leading-5 text-[var(--muted)]">
          Banner “Nhạc dạo” hiện trong dải lời khi khoảng lặng đủ dài. Mặc định tắt để dự án cũ giữ nguyên.
        </p>
        <Toggle
          label="Bật banner nhạc dạo"
          checked={interlude.enabled}
          onChange={(value) => patchInterlude({ enabled: value })}
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Khoảng lặng tối thiểu (giây)" hint="Tối thiểu 8 giây; ngưỡng thấp hơn sẽ bị từ chối.">
            <TextInput
              type="number"
              min={8}
              step={0.5}
              value={interlude.min_gap}
              onChange={(event) =>
                patchInterlude({ min_gap: Math.max(8, Number(event.target.value) || 8) })
              }
            />
          </Field>
          <Field label="Chữ hiển thị">
            <TextInput
              value={interlude.text}
              onChange={(event) => patchInterlude({ text: event.target.value })}
              placeholder="Nhạc dạo"
            />
          </Field>
          <Field label="Kiểu hiển thị">
            <Select value={interlude.mode} onChange={(event) => patchInterlude({ mode: event.target.value })}>
              <option value="strip">Banner trong dải lời</option>
            </Select>
          </Field>
          <div className="flex flex-col justify-end gap-2 pb-1">
            <Toggle
              label="Hiện câu tiếp theo"
              checked={interlude.show_next_line}
              onChange={(value) => patchInterlude({ show_next_line: value })}
            />
          </div>
          <SliderField
            label="Fade"
            value={interlude.fade}
            min={0}
            max={2}
            step={0.1}
            suffix="s"
            onChange={(value) => patchInterlude({ fade: value })}
          />
        </div>
      </section>

      <section className="space-y-3 border-t border-[var(--line)] pt-4">
        <SectionTitle>Trang kết</SectionTitle>
        <Toggle
          label="Bật trang kết"
          checked={outro.enabled}
          onChange={(value) => patchOutro({ enabled: value })}
        />
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Thời lượng (giây)" hint="0 = tắt trang kết.">
            <TextInput
              type="number"
              min={0}
              max={20}
              step={0.5}
              value={outro.duration}
              onChange={(event) => patchOutro({ duration: Math.max(0, Number(event.target.value) || 0) })}
            />
          </Field>
          <Field label="Ảnh nền trang kết" hint="Để trống sẽ dùng ảnh bìa.">
            <div className="flex gap-2">
              <TextInput
                value={outro.image}
                readOnly
                placeholder="Dùng ảnh bìa"
                className="min-w-0"
              />
              <button
                type="button"
                onClick={onBrowseOutro}
                disabled={!onBrowseOutro || outroBusy}
                className="shrink-0 rounded-xl border border-[var(--line)] px-3 text-sm transition hover:border-[var(--gold)] disabled:opacity-50"
              >
                {outroBusy ? "…" : "Chọn"}
              </button>
              {outro.image ? (
                <button
                  type="button"
                  onClick={() => patchOutro({ image: "" })}
                  aria-label="Bỏ ảnh trang kết"
                  className="shrink-0 rounded-xl border border-[var(--line)] px-2.5 text-sm text-[var(--muted)] transition hover:border-red-800 hover:text-red-200"
                >
                  ×
                </button>
              ) : null}
            </div>
          </Field>
          <Field label="Tiêu đề" hint="Để trống sẽ dùng tên bài.">
            <TextInput
              value={outro.title}
              onChange={(event) => patchOutro({ title: event.target.value })}
              placeholder="Tên bài"
            />
          </Field>
          <Field label="Dòng cảm ơn">
            <TextInput
              value={outro.subtext}
              onChange={(event) => patchOutro({ subtext: event.target.value })}
              placeholder="Cảm ơn đã xem"
            />
          </Field>
          <Field label="Tên kênh / handle">
            <TextInput
              value={outro.handle}
              onChange={(event) => patchOutro({ handle: event.target.value })}
              placeholder="@kenh"
            />
          </Field>
          <div className="flex flex-col justify-end gap-2 pb-1">
            <Toggle
              label="Hiện tiêu đề"
              checked={outro.show_title}
              onChange={(value) => patchOutro({ show_title: value })}
            />
            <Toggle
              label="Hiện tác giả"
              checked={outro.show_artist}
              onChange={(value) => patchOutro({ show_artist: value })}
            />
          </div>
          <SliderField
            label="Fade"
            value={outro.fade}
            min={0}
            max={2}
            step={0.1}
            suffix="s"
            onChange={(value) => patchOutro({ fade: value })}
          />
        </div>
      </section>

      <section className="space-y-3 border-t border-[var(--line)] pt-4">
        <SectionTitle>Chữ lời</SectionTitle>
        <div className="grid gap-3 sm:grid-cols-2">
          <OptionalNumberField
            label="Cỡ chữ lời"
            value={typography.lyric_size}
            min={12}
            max={120}
            suffix="px"
            onChange={(value) => patchTypography({ lyric_size: value })}
          />
          <OptionalNumberField
            label="Khoảng cách dòng"
            value={typography.row_gap}
            min={0}
            max={80}
            suffix="px"
            placeholder="Mặc định (0 hợp lệ)"
            onChange={(value) => patchTypography({ row_gap: value })}
          />
          <ColorField
            label="Màu chữ thường"
            value={typography.lyric_color}
            onChange={(value) => patchTypography({ lyric_color: value })}
          />
          <ColorField
            label="Màu chữ đã hát"
            value={typography.sung_color}
            onChange={(value) => patchTypography({ sung_color: value })}
          />
          <ColorField
            label="Màu nền dải lời"
            value={typography.bar_color}
            onChange={(value) => patchTypography({ bar_color: value })}
          />
          <SliderField
            label="Độ đậm nền dải lời"
            value={typography.bar_opacity}
            min={0}
            max={255}
            onChange={(value) => patchTypography({ bar_opacity: value })}
          />
          <SliderField
            label="Viền chữ"
            value={typography.stroke}
            min={0}
            max={10}
            onChange={(value) => patchTypography({ stroke: value })}
          />
          <div className="flex flex-col justify-end pb-1">
            <Toggle
              label="Đổ bóng chữ"
              checked={typography.shadow}
              onChange={(value) => patchTypography({ shadow: value })}
            />
          </div>
        </div>
      </section>
    </div>
  );
}
