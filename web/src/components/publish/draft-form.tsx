"use client";

import { useMemo, useState } from "react";

import { Field, Select, TextArea, TextInput } from "@/components/ui/field";
import {
  Badge,
  EmptyState,
  Panel,
  SectionTitle,
  btnDanger,
  btnPrimary,
  btnSecondary,
} from "@/components/ui/primitives";
import {
  PRIVACY_OPTIONS,
  codePointLength,
  type PublishDraft,
  type PublishPlaylist,
  type PublishVideo,
} from "@/lib/publish";

function videoKey(video: PublishVideo) {
  return `${video.project_path}|${video.role}`;
}

export function DraftForm({
  draft,
  playlists,
  playlistsLoading,
  playlistsError,
  videos,
  videosLoading,
  saveState,
  saveError,
  conflict,
  busy,
  onReloadConflict,
  onChange,
  onSaveNow,
  onPublish,
  onDuplicate,
  onDelete,
  onCreate,
}: {
  draft: PublishDraft | null;
  playlists: PublishPlaylist[];
  playlistsLoading: boolean;
  playlistsError: string;
  videos: PublishVideo[];
  videosLoading: boolean;
  saveState: "idle" | "saving" | "saved" | "error";
  saveError: string;
  conflict: PublishDraft | null;
  busy: string;
  onReloadConflict: (draft: PublishDraft) => void;
  onChange: (patch: Partial<PublishDraft>) => void;
  onSaveNow: () => void;
  onPublish: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onCreate?: () => void;
}) {
  const [playlistSearch, setPlaylistSearch] = useState("");

  const titleLength = draft ? codePointLength(draft.title) : 0;
  const bodyLength = draft ? codePointLength(draft.description) : 0;
  const titleInvalid = Boolean(draft && (draft.title.trim() === "" || /[<>]/.test(draft.title)));
  const bodyInvalid = bodyLength > 5000;
  const titleTooLong = titleLength > 100;

  const filteredPlaylists = useMemo(() => {
    const query = playlistSearch.trim().toLowerCase();
    const base = query
      ? playlists.filter((playlist) => playlist.title.toLowerCase().includes(query))
      : playlists;
    if (draft?.playlist_id && !base.some((playlist) => playlist.id === draft.playlist_id)) {
      const selected = playlists.find((playlist) => playlist.id === draft.playlist_id);
      if (selected) return [selected, ...base];
    }
    return base;
  }, [draft?.playlist_id, playlists, playlistSearch]);

  if (!draft) {
    return (
      <Panel className="space-y-3">
        <SectionTitle>Soạn nội dung</SectionTitle>
        <EmptyState
          title="Chưa có nháp để sửa"
          description="Chọn một nháp bên dưới, hoặc chọn video đã xuất để tạo nháp mới. Nội dung được lưu lại để bấm “Đăng lại” sau khi xuất video mới."
        />
        {onCreate ? (
          <button type="button" className={btnPrimary} onClick={onCreate} disabled={busy !== ""}>
            {busy === "create" ? "Đang tạo…" : "Tạo nháp mới"}
          </button>
        ) : null}
      </Panel>
    );
  }

  const selectedVideo =
    videos.find((video) => video.project_path === draft.project_path && video.role === draft.video_role) || null;

  return (
    <Panel className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionTitle>Soạn nội dung (YouTube)</SectionTitle>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={saveState === "error" ? "danger" : saveState === "saving" ? "gold" : "success"}>
            {saveState === "saving" ? "Đang lưu…" : saveState === "error" ? "Lỗi lưu" : "Đã lưu"}
          </Badge>
          {draft.last_published_at ? <Badge tone="info">Video mới nhất</Badge> : <Badge tone="muted">Nháp</Badge>}
        </div>
      </div>

      {conflict ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs leading-5 text-amber-100">
          <span>Nháp đã đổi ở tab khác (revision {conflict.revision}). Tải bản mới nhất để tránh ghi đè.</span>
          <button type="button" className={btnSecondary} onClick={() => onReloadConflict(conflict)}>
            Tải bản mới
          </button>
        </div>
      ) : null}
      {saveError ? (
        <p className="rounded-xl border border-red-900/60 bg-red-950/30 px-3 py-2 text-xs leading-5 text-red-100">
          {saveError}
        </p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <Field
          label="Video"
          hint={
            selectedVideo
              ? `Dùng file ${selectedVideo.role === "vocal" ? "có giọng" : "không lời"} mới nhất khi đăng.`
              : "Chọn một video đã xuất. Nếu danh sách trống, hãy xuất video trước."
          }
        >
          <Select
            value={selectedVideo ? videoKey(selectedVideo) : ""}
            disabled={videosLoading || videos.length === 0 || busy !== ""}
            onChange={(event) => {
              const next = videos.find((video) => videoKey(video) === event.target.value);
              if (!next) return;
              onChange({
                project_path: next.project_path,
                project_title: next.project_title,
                video_role: next.role,
              });
            }}
          >
            {!selectedVideo ? (
              <option value="">{videosLoading ? "Đang tải video…" : "Chọn video đã xuất"}</option>
            ) : null}
            {videos.map((video) => (
              <option key={videoKey(video)} value={videoKey(video)}>
                {video.project_title} · {video.role === "vocal" ? "có giọng" : "không lời"}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Kênh phát (playlist)" hint={playlistsError || "Có thể để trống nếu không thêm vào playlist."}>
          <Select
            value={draft.playlist_id || ""}
            disabled={playlistsLoading}
            onChange={(event) => {
              const playlist = playlists.find((item) => item.id === event.target.value);
              onChange({
                playlist_id: event.target.value,
                playlist_name: playlist?.title || "",
              });
            }}
          >
            <option value="">Không thêm vào playlist</option>
            {filteredPlaylists.map((playlist) => (
              <option key={playlist.id} value={playlist.id}>
                {playlist.title}
                {typeof playlist.item_count === "number" ? ` · ${playlist.item_count} video` : ""}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {playlists.length > 8 ? (
        <TextInput
          value={playlistSearch}
          onChange={(event) => setPlaylistSearch(event.target.value)}
          placeholder="Tìm playlist…"
          className="max-w-sm"
        />
      ) : null}

      <Field
        label={`Tiêu đề · ${titleLength}/100`}
        hint={titleInvalid ? "Tiêu đề không được trống hoặc chứa < >." : "Emoji/icon: nhập hoặc dán trực tiếp."}
      >
        <TextInput
          value={draft.title}
          onChange={(event) => onChange({ title: event.target.value })}
          placeholder="🎤 Tên bài hát"
          className={titleInvalid || titleTooLong ? "border-red-900" : ""}
        />
      </Field>

      <Field
        label={`Nội dung · ${bodyLength}/5000`}
        hint="Giữ nguyên Unicode/emoji. Không có bảng chọn emoji; chỉ nhập hoặc dán."
      >
        <TextArea
          value={draft.description}
          onChange={(event) => onChange({ description: event.target.value })}
          placeholder={"🎶 Lời bài hát…\n#karaoke #cover"}
          className={`min-h-40 ${bodyInvalid ? "border-red-900" : ""}`}
        />
      </Field>

      <div className="grid gap-4 sm:grid-cols-2">
        <fieldset className="space-y-2">
          <legend className="mb-1 text-xs font-medium text-[var(--muted)]">Trẻ em có xem được?</legend>
          <div className="flex flex-wrap gap-4 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name={`kids-${draft.id}`}
                checked={draft.made_for_kids}
                onChange={() => onChange({ made_for_kids: true })}
                className="accent-[var(--gold)]"
              />
              Có
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name={`kids-${draft.id}`}
                checked={!draft.made_for_kids}
                onChange={() => onChange({ made_for_kids: false })}
                className="accent-[var(--gold)]"
              />
              Không
            </label>
          </div>
          <p className="text-[11px] leading-4 text-[var(--muted)]">
            Nếu kênh đã đặt “dành cho trẻ em” ở cấp kênh, YouTube có thể không cho đổi.
          </p>
        </fieldset>

        <fieldset className="space-y-2">
          <legend className="mb-1 text-xs font-medium text-[var(--muted)]">Chế độ đăng</legend>
          <div className="flex flex-wrap gap-3 text-sm">
            {PRIVACY_OPTIONS.map((option) => (
              <label key={option.value} className="flex items-center gap-2">
                <input
                  type="radio"
                  name={`privacy-${draft.id}`}
                  checked={(draft.privacy || "public") === option.value}
                  onChange={() => onChange({ privacy: option.value })}
                  className="accent-[var(--gold)]"
                />
                {option.label}
              </label>
            ))}
          </div>
          <p className="text-[11px] leading-4 text-[var(--muted)]">Mặc định công khai; luôn gửi tường minh.</p>
        </fieldset>
      </div>

      {draft.last_url || draft.last_video_id ? (
        <p className="text-xs leading-5 text-[var(--muted)]">
          Lần đăng trước:{" "}
          {draft.last_url ? (
            <a href={draft.last_url} target="_blank" rel="noreferrer" className="text-[var(--gold)] hover:text-white">
              {draft.last_url}
            </a>
          ) : (
            draft.last_video_id
          )}
          {draft.last_published_at ? ` · ${draft.last_published_at}` : ""}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--line)] pt-3">
        <div className="flex flex-wrap gap-2">
          <button type="button" className={btnSecondary} onClick={onSaveNow} disabled={busy !== ""}>
            {busy === "save" ? "Đang lưu…" : "Lưu nháp"}
          </button>
          <button
            type="button"
            className={btnPrimary}
            onClick={onPublish}
            disabled={busy !== "" || titleInvalid || titleTooLong || bodyInvalid}
          >
            {busy === "publish" ? "Đang gửi…" : draft.last_published_at ? "Đăng lại" : "Đăng ngay"}
          </button>
          <button type="button" className={btnSecondary} onClick={onDuplicate} disabled={busy !== ""}>
            Nhân bản
          </button>
        </div>
        <button type="button" className={btnDanger} onClick={onDelete} disabled={busy !== ""}>
          Xoá nháp
        </button>
      </div>
      <p className="text-[11px] leading-5 text-[var(--muted)]">
        Video dùng bản mới nhất theo vai trò đã chọn. Tự lưu sau khi ngừng gõ; nút Đăng sẽ chụp lại metadata tại thời
        điểm bấm.
      </p>
    </Panel>
  );
}
