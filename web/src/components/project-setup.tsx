"use client";

import { useMemo, type FormEvent } from "react";

import { ImageCards } from "@/components/image-cards";
import { sungCount, type ProjectFields } from "@/lib/types";

type BrowseKind = "original" | "vocal" | "instrumental" | "cover" | "images" | "output";

const fieldClass =
  "w-full rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2.5 text-sm outline-none transition focus:border-[var(--gold)]";

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

export function ProjectSetup({
  fields,
  setFields,
  notice,
  busyBrowse,
  aligning,
  hasLines,
  onBrowse,
  onContinue,
  onAlign,
  onResumeEdit,
  onBackHome,
}: {
  fields: ProjectFields;
  setFields: (updater: (current: ProjectFields) => ProjectFields) => void;
  notice: string;
  busyBrowse: string;
  aligning: boolean;
  hasLines: boolean;
  onBrowse: (kind: BrowseKind) => void;
  onContinue: (event: FormEvent) => void;
  onAlign: () => void;
  onResumeEdit: () => void;
  onBackHome: () => void;
}) {
  const lines = useMemo(() => sungCount(fields.lyrics), [fields.lyrics]);

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]">
      <form onSubmit={onContinue} className="space-y-5 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Khai báo</p>
            <h2 className="mt-1 text-xl font-semibold">Thông tin và file của dự án</h2>
          </div>
          <button type="button" onClick={onBackHome} className="text-sm text-[var(--muted)] hover:text-white">
            Về màn hình đầu
          </button>
        </div>

        <section className="grid gap-4 sm:grid-cols-2">
          <label className="block sm:col-span-2">
            <span className="mb-1.5 block text-sm text-[var(--muted)]">Tên bài hát</span>
            <input
              className={fieldClass}
              value={fields.title}
              onChange={(event) => setFields((current) => ({ ...current, title: event.target.value }))}
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm text-[var(--muted)]">Tác giả</span>
            <input
              className={fieldClass}
              value={fields.artist}
              onChange={(event) => setFields((current) => ({ ...current, artist: event.target.value }))}
            />
          </label>
          <label className="block">
            <span className="mb-1.5 block text-sm text-[var(--muted)]">Dòng phụ trên bìa</span>
            <input
              className={fieldClass}
              value={fields.cover_line}
              onChange={(event) => setFields((current) => ({ ...current, cover_line: event.target.value }))}
              placeholder="Không bắt buộc"
            />
          </label>
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Audio</h2>
          <PathField
            label="Audio gốc"
            value={fields.original_audio}
            placeholder="Bài hát đầy đủ"
            onBrowse={() => onBrowse("original")}
          />
          <PathField label="Vocal" value={fields.vocal_audio} placeholder="File _(Vocals)" onBrowse={() => onBrowse("vocal")} />
          <PathField
            label="Instrumental"
            value={fields.instrumental_audio}
            placeholder="File _(Instrumental)"
            onBrowse={() => onBrowse("instrumental")}
          />
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
            value={fields.lyrics}
            onChange={(event) => setFields((current) => ({ ...current, lyrics: event.target.value }))}
            placeholder={"Dán lời vào đây.\nDòng [Verse], [Intro] và ghi chú trong ngoặc sẽ không hiện trên video."}
            disabled={hasLines}
          />
          {hasLines && (
            <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
              Dự án đã có timing. Lời trên video sửa ở bước Biên tập; muốn căn lại thì tạo dự án mới.
            </p>
          )}
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Hình</h2>
          <p className="text-sm leading-6 text-[var(--muted)]">
            Khung video là 1920×1080, tỉ lệ 16:9. Ảnh đúng tỉ lệ này sẽ đầy khung. Ảnh vuông hoặc 3:2 vẫn dùng được, nằm giữa khung và có viền tối hai bên.
          </p>
          <PathField
            label="Ảnh bìa"
            value={fields.cover_image}
            placeholder="Ảnh phía sau tên bài, nên 1920×1080"
            onBrowse={() => onBrowse("cover")}
          />
          {fields.cover_image && (
            <ImageCards paths={[fields.cover_image]} onRemove={() => setFields((current) => ({ ...current, cover_image: "" }))} />
          )}
          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <span className="text-sm text-[var(--muted)]">Ảnh nền, lặp đến hết bài</span>
              <button type="button" onClick={() => onBrowse("images")} className="text-sm text-[var(--gold)]">
                Thêm ảnh
              </button>
            </div>
            {fields.background_images.length === 0 && (
              <p className="text-sm text-[var(--muted)]">Chưa có ảnh nền. Nếu bỏ trống, ảnh bìa sẽ được lặp lại.</p>
            )}
            <ImageCards
              paths={fields.background_images}
              onRemove={(path) =>
                setFields((current) => ({
                  ...current,
                  background_images: current.background_images.filter((item) => item !== path),
                }))
              }
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block">
              <span className="mb-1.5 block text-sm text-[var(--muted)]">Bìa (giây)</span>
              <input
                className={fieldClass}
                type="number"
                min="0"
                step="0.5"
                value={fields.cover_duration}
                onChange={(event) => setFields((current) => ({ ...current, cover_duration: event.target.value }))}
              />
            </label>
            <label className="block">
              <span className="mb-1.5 block text-sm text-[var(--muted)]">Mỗi ảnh nền (giây)</span>
              <input
                className={fieldClass}
                type="number"
                min="1"
                step="0.5"
                value={fields.image_duration}
                onChange={(event) => setFields((current) => ({ ...current, image_duration: event.target.value }))}
              />
            </label>
          </div>
        </section>

        <label className="block">
          <span className="mb-1.5 block text-sm text-[var(--muted)]">Thiết bị tách giọng</span>
          <select
            className={fieldClass}
            value={fields.device}
            onChange={(event) => setFields((current) => ({ ...current, device: event.target.value }))}
          >
            <option value="cpu">CPU</option>
            <option value="mps">GPU Apple</option>
          </select>
        </label>

        <div>
          <PathField
            label="Thư mục xuất"
            value={fields.output_dir}
            placeholder="Nơi lưu dự án và hai video"
            onBrowse={() => onBrowse("output")}
          />
          {fields.project_path && (
            <p className="mt-2 text-xs leading-5 text-[var(--muted)]">
              File dự án hiện tại: {fields.project_path}. Đổi tên bài hoặc thư mục xuất rồi lưu sẽ ghi sang file mới và gỡ bản cũ — trừ khi tên mới trùng dự án khác (bị từ chối).
            </p>
          )}
        </div>

        {notice && <p className="rounded-xl bg-red-950/60 px-3 py-2 text-sm text-red-100">{notice}</p>}
        {busyBrowse && <p className="text-sm text-[var(--muted)]">Đang mở hộp thoại trên máy…</p>}

        {hasLines ? (
          <button
            type="button"
            disabled={aligning}
            onClick={onResumeEdit}
            className="w-full rounded-2xl bg-[var(--gold)] px-4 py-3 text-sm font-semibold text-[#1a1206] disabled:opacity-60"
          >
            Lưu khai báo và vào biên tập
          </button>
        ) : (
          <div className="space-y-2">
            <button
              type="submit"
              disabled={aligning}
              className="w-full rounded-2xl bg-[var(--gold)] px-4 py-3 text-sm font-semibold text-[#1a1206] disabled:opacity-60"
            >
              Tiếp tục tách và trộn
            </button>
            {fields.vocal_audio && fields.instrumental_audio && (
              <button
                type="button"
                disabled={aligning}
                onClick={() => onAlign()}
                className="w-full rounded-2xl border border-[var(--gold)] px-4 py-3 text-sm font-semibold text-[var(--gold)] disabled:opacity-60"
              >
                Bỏ qua, căn lời với file đã có
              </button>
            )}
          </div>
        )}
      </form>

      <aside className="space-y-4 lg:sticky lg:top-6">
        <section className="rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5">
          <h2 className="text-sm font-semibold tracking-[0.16em] text-[var(--gold)] uppercase">Luồng</h2>
          <ol className="mt-4 space-y-2 text-sm leading-6 text-[var(--muted)]">
            <li>1. Khai báo audio, lời, ảnh</li>
            <li>2. Tách Kim Vocal 2 và trộn nhạc nền</li>
            <li>3. Căn / ghép chữ</li>
            <li>4. Biên tập timing</li>
            <li>5. Xuất hai video</li>
          </ol>
        </section>
      </aside>
    </div>
  );
}
