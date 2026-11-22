"use client";

import { useCallback, useMemo, useState, type FormEvent } from "react";

import { AiEffects } from "@/components/ai-effects";
import { ImageCards } from "@/components/image-cards";
import { Drawer } from "@/components/ui/dialog";
import { Field, PathField, Select, TextArea, TextInput } from "@/components/ui/field";
import {
  Badge,
  Disclosure,
  Panel,
  SectionTitle,
  btnGhost,
  btnSecondary,
} from "@/components/ui/primitives";
import { PROFILE_OPTIONS, profileMeta } from "@/lib/format";
import { useSettings } from "@/lib/settings";
import { sungCount, type ProjectFields } from "@/lib/types";

type BrowseKind = "original" | "vocal" | "instrumental" | "cover" | "images" | "output";

export function ProjectSetup({
  fields,
  setFields,
  notice,
  busyBrowse,
  aligning,
  hasLines,
  onBrowse,
  onContinue,
  onRestore,
  onResumeEdit,
  onNewProject,
}: {
  fields: ProjectFields;
  setFields: (updater: (current: ProjectFields) => ProjectFields) => void;
  notice: string;
  busyBrowse: string;
  aligning: boolean;
  hasLines: boolean;
  onBrowse: (kind: BrowseKind) => void;
  onContinue: (event: FormEvent) => void;
  onRestore: () => void;
  onResumeEdit: () => void;
  onNewProject?: () => void;
}) {
  const { openSettings } = useSettings();
  const [effectsOpen, setEffectsOpen] = useState(false);
  const closeEffects = useCallback(() => setEffectsOpen(false), []);

  const lines = useMemo(() => sungCount(fields.lyrics), [fields.lyrics]);
  const stemsReady = Boolean(fields.vocal_audio && fields.instrumental_audio);
  const primaryAligns = !hasLines && stemsReady;
  const profile = profileMeta(fields.output_profile);
  const effectImages = fields.background_images.length
    ? fields.background_images
    : fields.cover_image
      ? [fields.cover_image]
      : [];
  const effectCount = fields.background_effects.length;

  const checklist = [
    {
      label: "Audio gốc",
      done: Boolean(fields.original_audio),
      href: "#audio-goc",
      detail: "",
      optional: false,
    },
    {
      label: "Vocal",
      done: Boolean(fields.vocal_audio),
      href: "#vocal",
      detail: "",
      optional: true,
    },
    {
      label: "Instrumental",
      done: Boolean(fields.instrumental_audio),
      href: "#instrumental",
      detail: "",
      optional: true,
    },
    {
      label: "Số câu lời",
      done: lines > 0,
      href: "#loi",
      detail: lines > 0 ? `${lines} câu` : "Chưa nhập lời",
      optional: false,
    },
    {
      label: "Ảnh bìa / ảnh nền",
      done: Boolean(fields.cover_image) || fields.background_images.length > 0,
      href: "#hinh",
      detail: fields.background_images.length ? `${fields.background_images.length} ảnh nền` : "",
      optional: false,
    },
    {
      label: "Thư mục xuất",
      done: Boolean(fields.output_dir),
      href: "#thu-muc-xuat",
      detail: "",
      optional: false,
    },
  ];
  const requiredItems = checklist.filter((item) => !item.optional);
  const doneCount = requiredItems.filter((item) => item.done).length;
  const deviceLabel = fields.device === "mps" ? "GPU Apple" : "CPU";

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1.4fr)_minmax(280px,0.8fr)]">
      <form
        onSubmit={onContinue}
        className="space-y-5 rounded-3xl border border-[var(--line)] bg-[var(--panel)] p-5 sm:p-6"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold tracking-[0.18em] text-[var(--gold)] uppercase">Chuẩn bị</p>
            <h2 className="mt-1 text-xl font-semibold">Thông tin và file của dự án</h2>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            {onNewProject && (
              <button type="button" onClick={onNewProject} className={btnGhost}>
                Dự án mới
              </button>
            )}
          </div>
        </div>

        {hasLines && (
          <p className="rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2 text-sm leading-6 text-[var(--muted)]">
            Đổi tên bài, ảnh hoặc thời lượng tại đây rồi bấm{" "}
            <span className="text-[var(--paper)]">Lưu khai báo và vào biên tập</span>. Lời từng chữ sửa ở Biên tập.
          </p>
        )}

        <section className="space-y-4">
          <SectionTitle>Thông tin</SectionTitle>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Tên bài hát" htmlFor="project-title" className="sm:col-span-2">
              <TextInput
                id="project-title"
                value={fields.title}
                onChange={(event) => setFields((current) => ({ ...current, title: event.target.value }))}
              />
            </Field>
            <Field label="Tác giả" htmlFor="project-artist">
              <TextInput
                id="project-artist"
                value={fields.artist}
                onChange={(event) => setFields((current) => ({ ...current, artist: event.target.value }))}
              />
            </Field>
            <Field label="Dòng phụ trên bìa" htmlFor="project-cover-line">
              <TextInput
                id="project-cover-line"
                value={fields.cover_line}
                onChange={(event) => setFields((current) => ({ ...current, cover_line: event.target.value }))}
                placeholder="Không bắt buộc"
              />
            </Field>
          </div>
        </section>

        <section className="space-y-3">
          <SectionTitle>Audio</SectionTitle>
          <div id="audio-goc" className="scroll-mt-24">
            <PathField
              label="Audio gốc"
              value={fields.original_audio}
              placeholder="Bài hát đầy đủ"
              onBrowse={() => onBrowse("original")}
              busy={busyBrowse === "original"}
            />
          </div>
          <div id="vocal" className="scroll-mt-24">
            <PathField
              label="Vocal"
              value={fields.vocal_audio}
              placeholder="File _(Vocals)"
              onBrowse={() => onBrowse("vocal")}
              busy={busyBrowse === "vocal"}
            />
          </div>
          <div id="instrumental" className="scroll-mt-24">
            <PathField
              label="Instrumental"
              value={fields.instrumental_audio}
              placeholder="File _(Instrumental)"
              onBrowse={() => onBrowse("instrumental")}
              busy={busyBrowse === "instrumental"}
              hint="Chọn một file vocal hoặc instrumental, ứng dụng sẽ tìm file còn lại và bài gốc cùng tên."
            />
          </div>
        </section>

        <section id="loi" className="scroll-mt-24 space-y-2">
          <SectionTitle action={<span className="text-xs text-[var(--muted)]">{lines} câu hát</span>}>Lời</SectionTitle>
          <TextArea
            className="min-h-52 leading-6"
            value={fields.lyrics}
            onChange={(event) => setFields((current) => ({ ...current, lyrics: event.target.value }))}
            placeholder={"Dán lời vào đây.\nDòng [Verse], [Intro] và ghi chú trong ngoặc sẽ không hiện trên video."}
            disabled={hasLines}
          />
          {hasLines && (
            <p className="text-xs leading-5 text-[var(--muted)]">
              Dự án đã có timing. Lời trên video sửa ở bước Biên tập; muốn căn lại thì tạo dự án mới.
            </p>
          )}
        </section>

        <section id="hinh" className="scroll-mt-24 space-y-3">
          <SectionTitle>Hình</SectionTitle>
          <p className="text-sm leading-6 text-[var(--muted)]">
            Ảnh đúng tỉ lệ khung sẽ đầy khung; ảnh khác tỉ lệ nằm giữa và có viền tối.
          </p>
          <PathField
            label="Ảnh bìa"
            value={fields.cover_image}
            placeholder="Ảnh phía sau tên bài, nên 1920×1080"
            onBrowse={() => onBrowse("cover")}
            busy={busyBrowse === "cover"}
          />
          {fields.cover_image && (
            <ImageCards
              paths={[fields.cover_image]}
              onRemove={() =>
                setFields((current) => ({
                  ...current,
                  cover_image: "",
                  background_effects: current.background_effects.filter(
                    (effect) => effect.image_path !== current.cover_image
                  ),
                }))
              }
              outputProfile={fields.output_profile}
            />
          )}
          <div>
            <div className="mb-1.5 flex items-center justify-between gap-3">
              <span className="text-xs font-medium text-[var(--muted)]">Ảnh nền, lặp đến hết bài</span>
              <button
                type="button"
                onClick={() => onBrowse("images")}
                disabled={busyBrowse === "images"}
                className="text-sm text-[var(--gold)] disabled:opacity-60"
              >
                {busyBrowse === "images" ? "Đang mở…" : "Thêm ảnh"}
              </button>
            </div>
            {fields.background_images.length === 0 && (
              <p className="text-sm text-[var(--muted)]">Chưa có ảnh nền. Nếu bỏ trống, ảnh bìa sẽ được lặp lại.</p>
            )}
            <ImageCards
              paths={fields.background_images}
              outputProfile={fields.output_profile}
              onRemove={(path) =>
                setFields((current) => ({
                  ...current,
                  background_images: current.background_images.filter((item) => item !== path),
                  background_effects: current.background_effects.filter((effect) => effect.image_path !== path),
                }))
              }
            />
          </div>
        </section>

        <Disclosure
          title="Hiệu ứng nền"
          summary={
            <span className="flex items-center gap-1.5">
              <Badge tone="muted">{effectImages.length} ảnh</Badge>
              <Badge tone={effectCount ? "gold" : "muted"}>{effectCount} clip</Badge>
            </span>
          }
          className="bg-[var(--panel)]"
        >
          <div className="space-y-3">
            <p className="text-sm leading-6 text-[var(--muted)]">
              Phân tích ảnh bằng Gemini để thêm mưa, tuyết, khói… Cần bật đồng ý gửi ảnh trong Cài đặt.
            </p>
            <button
              type="button"
              disabled={effectImages.length === 0}
              onClick={() => setEffectsOpen(true)}
              className={btnSecondary}
            >
              Làm hiệu ứng cho ảnh
            </button>
            {effectImages.length === 0 && (
              <p className="text-xs leading-5 text-[var(--muted)]">
                Cần chọn ảnh bìa hoặc ảnh nền trước khi làm hiệu ứng.
              </p>
            )}
          </div>
        </Disclosure>

        <Disclosure
          title="Tuỳ chọn xuất"
          summary={`${profile.label} · ${profile.size}`}
          className="bg-[var(--panel)]"
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Bìa (giây)" htmlFor="cover-duration">
              <TextInput
                id="cover-duration"
                type="number"
                min="0"
                step="0.5"
                value={fields.cover_duration}
                onChange={(event) => setFields((current) => ({ ...current, cover_duration: event.target.value }))}
              />
            </Field>
            <Field label="Mỗi ảnh nền (giây)" htmlFor="image-duration">
              <TextInput
                id="image-duration"
                type="number"
                min="1"
                step="0.5"
                value={fields.image_duration}
                onChange={(event) => setFields((current) => ({ ...current, image_duration: event.target.value }))}
              />
            </Field>
            <Field label="Định dạng video" htmlFor="output-profile" className="sm:col-span-2">
              <Select
                id="output-profile"
                value={
                  PROFILE_OPTIONS.some((option) => option.value === fields.output_profile)
                    ? fields.output_profile
                    : "landscape_1080p"
                }
                onChange={(event) => setFields((current) => ({ ...current, output_profile: event.target.value }))}
              >
                {PROFILE_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label} · {option.size}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
        </Disclosure>

        <div id="thu-muc-xuat" className="scroll-mt-24">
          <PathField
            label="Thư mục xuất"
            value={fields.output_dir}
            placeholder="Nơi lưu dự án và hai video"
            onBrowse={() => onBrowse("output")}
            busy={busyBrowse === "output"}
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-[var(--line)] bg-black/20 px-3 py-2 text-xs text-[var(--muted)]">
          <span>
            Thiết bị và model mặc định: {deviceLabel} · {fields.model_name}
          </span>
          <button type="button" onClick={openSettings} className="text-[var(--gold)] hover:text-white">
            Sửa trong Cài đặt
          </button>
        </div>

        <Disclosure title="Nâng cao" className="bg-[var(--panel)]">
          <div className="space-y-3 text-xs leading-5 text-[var(--muted)]">
            {fields.project_path && (
              <p>
                File dự án hiện tại: <span className="text-[var(--paper)]">{fields.project_path}</span>. Đổi tên bài hoặc
                thư mục xuất rồi lưu sẽ ghi sang file mới và gỡ bản cũ — trừ khi tên mới trùng dự án khác (bị từ chối).
              </p>
            )}
            <p>
              Khung dọc 9:16 dành vùng an toàn trên và phải cho giao diện ứng dụng; lời nằm phía trên vùng an toàn đáy.
              1:1 và 4:5 không có vùng UI che. Preview hiệu ứng dùng ảnh gốc, bản render khớp định dạng đã chọn.
            </p>
            <p>Tên file video có hậu tố thời gian nên xuất lại không ghi đè bản trước.</p>
          </div>
        </Disclosure>

        {notice && (
          <p
            role="status"
            className="rounded-xl border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-sm leading-6 text-amber-100"
          >
            {notice}
          </p>
        )}
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
              {primaryAligns ? "Vào ghép chữ" : "Tiếp tục tách và trộn"}
            </button>
            {primaryAligns ? (
              <p className="text-xs leading-5 text-[var(--muted)]">
                Đã có vocal và instrumental nên bước tách/trộn được bỏ qua.{" "}
                <button
                  type="button"
                  disabled={aligning}
                  onClick={onRestore}
                  className="text-[var(--gold)] hover:text-white disabled:opacity-60"
                >
                  Vẫn muốn tách và trộn lại?
                </button>
              </p>
            ) : (
              <p className="text-xs leading-5 text-[var(--muted)]">
                Đã có sẵn cặp vocal/instrumental? Chọn ở mục Audio để vào thẳng bước ghép chữ, không cần tách lại.
              </p>
            )}
          </div>
        )}
      </form>

      <aside className="lg:sticky lg:top-24">
        <Panel className="space-y-3">
          <SectionTitle action={<span className="text-xs text-[var(--muted)]">{doneCount}/{requiredItems.length}</span>}>
            Checklist
          </SectionTitle>
          <ul className="space-y-1">
            {checklist.map((item) => (
              <li key={item.href}>
                <a
                  href={item.href}
                  className="group flex items-start gap-2 rounded-xl px-2 py-1.5 text-sm transition hover:bg-white/5"
                >
                  <span
                    aria-hidden
                    className={`mt-0.5 text-sm ${item.done ? "text-emerald-300" : "text-[var(--muted)]"}`}
                  >
                    {item.done ? "✓" : "○"}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className={item.done ? "text-[var(--paper)]" : "text-[var(--muted)]"}>{item.label}</span>
                    {item.detail ? (
                      <span className="block text-[11px] leading-4 text-[var(--muted)]">{item.detail}</span>
                    ) : null}
                  </span>
                  <span
                    className={`shrink-0 text-[11px] leading-5 ${
                      item.done ? "text-emerald-300" : "text-[var(--muted)]"
                    }`}
                  >
                    {item.done ? "Đủ" : item.optional ? "Tuỳ chọn" : "Thiếu"}
                  </span>
                </a>
              </li>
            ))}
          </ul>
          <p className="text-xs leading-5 text-[var(--muted)]">Bấm một mục để tới ô tương ứng.</p>
        </Panel>
      </aside>

      <Drawer
        open={effectsOpen}
        onClose={closeEffects}
        title="Hiệu ứng nền"
        description="Phân tích ảnh và dựng clip hiệu ứng cho ảnh bìa hoặc ảnh nền."
        width="max-w-3xl"
      >
        <AiEffects
          images={effectImages}
          effects={fields.background_effects}
          outputProfile={fields.output_profile}
          imageDuration={fields.image_duration}
          onEffectsChange={(updater) =>
            setFields((current) => ({ ...current, background_effects: updater(current.background_effects) }))
          }
          onAssetDuration={(seconds) =>
            setFields((current) => ({ ...current, image_duration: String(seconds) }))
          }
        />
      </Drawer>
    </div>
  );
}
