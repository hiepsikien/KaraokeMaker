"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";

import { ChannelCards } from "@/components/publish/channel-cards";
import { DraftForm } from "@/components/publish/draft-form";
import { PublishQueue } from "@/components/publish/publish-queue";
import { useDialog } from "@/components/ui/dialog";
import { PageHeader } from "@/components/ui/page-header";
import { Badge, EmptyState, Panel, SectionTitle, btnGhost, btnPrimary, btnSecondary } from "@/components/ui/primitives";
import { useNotices } from "@/components/ui/toast";
import { formatRelative } from "@/lib/format";
import {
  PublishApiError,
  codePointLength,
  draftFromResponse,
  normalizeChannels,
  normalizeDrafts,
  normalizeJobs,
  normalizePlaylists,
  normalizeVideos,
  publishRequest,
  type PublishChannel,
  type PublishDraft,
  type PublishJob,
  type PublishPlaylist,
  type PublishVideo,
} from "@/lib/publish";

function sortDrafts(drafts: PublishDraft[]) {
  return [...drafts].sort((a, b) => {
    return (Date.parse(b.updated || b.created || "") || 0) - (Date.parse(a.updated || a.created || "") || 0);
  });
}

export function PublishWorkspace() {
  const params = useSearchParams();
  const projectParam = params.get("project") || "";
  const roleParam = params.get("role") || "";
  const { push } = useNotices();
  const { confirm } = useDialog();

  const [channels, setChannels] = useState<PublishChannel[]>([]);
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelsError, setChannelsError] = useState("");
  const [channelBusy, setChannelBusy] = useState("");

  const [drafts, setDrafts] = useState<PublishDraft[]>([]);
  const [draftsLoading, setDraftsLoading] = useState(true);
  const [draftsError, setDraftsError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [draft, setDraft] = useState<PublishDraft | null>(null);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");
  const [conflict, setConflict] = useState<PublishDraft | null>(null);
  const [draftBusy, setDraftBusy] = useState("");

  const [playlists, setPlaylists] = useState<PublishPlaylist[]>([]);
  const [playlistsLoading, setPlaylistsLoading] = useState(false);
  const [playlistsError, setPlaylistsError] = useState("");

  const [videos, setVideos] = useState<PublishVideo[]>([]);
  const [videosLoading, setVideosLoading] = useState(true);

  const [jobs, setJobs] = useState<PublishJob[]>([]);
  const [jobsError, setJobsError] = useState("");
  const [jobBusy, setJobBusy] = useState("");

  const draftRef = useRef<PublishDraft | null>(null);
  draftRef.current = draft;
  const saveTimer = useRef<number | null>(null);
  const bootstrapped = useRef(false);

  const youtubeConnected = Boolean(
    channels.find((channel) => channel.provider === "youtube" && channel.connected),
  );

  async function loadChannels() {
    setChannelsLoading(true);
    setChannelsError("");
    try {
      const data = await publishRequest<unknown>("/api/publish/channels");
      setChannels(normalizeChannels(data));
    } catch (error) {
      setChannelsError(
        error instanceof Error
          ? error.message
          : "Không tải được kênh. Kiểm tra server Python và cấu hình OAuth.",
      );
    } finally {
      setChannelsLoading(false);
    }
  }

  async function loadDrafts() {
    setDraftsLoading(true);
    setDraftsError("");
    try {
      const query = projectParam ? `?project=${encodeURIComponent(projectParam)}` : "";
      const data = await publishRequest<unknown>(`/api/publish/drafts${query}`);
      const list = sortDrafts(normalizeDrafts(data));
      setDrafts(list);
      setSelectedId((current) => {
        if (current && list.some((item) => item.id === current)) return current;
        const preferred =
          list.find(
            (item) =>
              item.project_path === projectParam &&
              (!roleParam || item.video_role === roleParam),
          ) || list.find((item) => item.project_path === projectParam) || list[0];
        return preferred?.id || "";
      });
    } catch (error) {
      setDraftsError(error instanceof Error ? error.message : "Không tải được danh sách nháp.");
    } finally {
      setDraftsLoading(false);
    }
  }

  async function loadJobs() {
    try {
      const query = projectParam ? `?project=${encodeURIComponent(projectParam)}` : "";
      const data = await publishRequest<unknown>(`/api/publish/jobs${query}`);
      setJobs(normalizeJobs(data));
      setJobsError("");
    } catch (error) {
      setJobsError(error instanceof Error ? error.message : "Không tải được hàng đợi.");
    }
  }

  async function loadPlaylists() {
    setPlaylistsLoading(true);
    setPlaylistsError("");
    try {
      const data = await publishRequest<unknown>("/api/publish/youtube/playlists");
      setPlaylists(normalizePlaylists(data));
    } catch (error) {
      setPlaylistsError(
        error instanceof PublishApiError && error.status === 401
          ? "Chưa kết nối YouTube hoặc token đã hết hạn."
          : error instanceof Error
            ? error.message
            : "Không tải được playlist.",
      );
    } finally {
      setPlaylistsLoading(false);
    }
  }

  async function loadVideos() {
    setVideosLoading(true);
    try {
      const data = await publishRequest<unknown>("/api/publish/videos");
      setVideos(normalizeVideos(data));
    } catch {
      setVideos([]);
    } finally {
      setVideosLoading(false);
    }
  }

  useEffect(() => {
    void loadChannels();
    void loadDrafts();
    void loadVideos();
    void loadJobs();
    const timer = window.setInterval(() => {
      void loadJobs();
    }, 4000);
    return () => {
      window.clearInterval(timer);
      if (saveTimer.current) window.clearTimeout(saveTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!youtubeConnected) {
      setPlaylists([]);
      return;
    }
    void loadPlaylists();
  }, [youtubeConnected]);

  useEffect(() => {
    function onFocus() {
      void loadChannels();
    }
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  useEffect(() => {
    if (bootstrapped.current) return;
    if (draftsLoading) return;
    bootstrapped.current = true;
    if (params.get("connected") === "1") {
      push({ tone: "success", message: "Đã kết nối YouTube." });
    }
  }, [draftsLoading, params, push]);

  useEffect(() => {
    const next = drafts.find((item) => item.id === selectedId) || null;
    setDraft(next);
    setConflict(null);
    setSaveError("");
    setSaveState("idle");
  }, [selectedId, drafts]);

  function selectDraft(next: PublishDraft) {
    if (next.id === selectedId) return;
    const current = draftRef.current;
    if (current && current.id !== next.id) {
      void flushSave(current);
    }
    setSelectedId(next.id);
    setDraft(next);
    setConflict(null);
    setSaveError("");
    setSaveState("idle");
  }

  function updateDraft(patch: Partial<PublishDraft>) {
    const current = draftRef.current;
    if (!current) return;
    const next = { ...current, ...patch };
    draftRef.current = next;
    setDraft(next);
    setSaveState("saving");
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void flushSave(next);
    }, 600);
  }

  async function flushSave(target?: PublishDraft): Promise<boolean> {
    const current = target || draftRef.current;
    if (!current) return false;
    if (saveTimer.current) {
      window.clearTimeout(saveTimer.current);
      saveTimer.current = null;
    }
    const targetId = current.id;
    try {
      const data = await publishRequest<unknown>(`/api/publish/drafts/${targetId}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: current.title,
          description: current.description,
          made_for_kids: current.made_for_kids,
          privacy: current.privacy || "public",
          playlist_id: current.playlist_id || "",
          playlist_name: current.playlist_name || "",
          project_path: current.project_path,
          project_title: current.project_title,
          video_role: current.video_role,
          revision: current.revision,
        }),
      });
      const saved = draftFromResponse(data);
      if (saved && draftRef.current?.id === targetId) {
        const merged = {
          ...saved,
          title: draftRef.current.title,
          description: draftRef.current.description,
          made_for_kids: draftRef.current.made_for_kids,
          privacy: draftRef.current.privacy,
          playlist_id: draftRef.current.playlist_id,
          playlist_name: draftRef.current.playlist_name,
          project_path: draftRef.current.project_path,
          project_title: draftRef.current.project_title,
          video_role: draftRef.current.video_role,
        };
        draftRef.current = merged;
        setDraft(merged);
        setDrafts((list) => sortDrafts(list.map((item) => (item.id === merged.id ? { ...item, ...merged } : item))));
      }
      setSaveState("saved");
      setSaveError("");
      return true;
    } catch (error) {
      if (error instanceof PublishApiError && error.status === 409) {
        const payload = error.payload as { detail?: { draft?: unknown }; draft?: unknown } | null;
        const latest = draftFromResponse(payload?.detail?.draft ?? payload?.draft ?? payload);
        setConflict(latest);
        setSaveState("error");
        setSaveError("Dữ liệu đã đổi ở tab khác. Tải bản mới nhất rồi sửa tiếp.");
        return false;
      }
      setSaveState("error");
      setSaveError(error instanceof Error ? error.message : "Chưa lưu được nháp.");
      return false;
    }
  }

  async function saveNow() {
    setDraftBusy("save");
    const ok = await flushSave();
    setDraftBusy("");
    if (ok) push({ tone: "success", message: "Đã lưu nháp." });
  }

  async function createDraft() {
    setDraftBusy("create");
    try {
      const preferredRole = roleParam || "vocal";
      const sameProject = videos.filter((item) => item.project_path === projectParam);
      const video = projectParam
        ? sameProject.find((item) => item.role === preferredRole) || sameProject[0]
        : videos.find((item) => item.role === preferredRole) || videos[0];
      const projectPath = projectParam || video?.project_path || "";
      if (!projectPath) {
        push({ tone: "error", message: "Chưa có video đã xuất để tạo nháp. Hãy xuất video trước." });
        return;
      }
      const baseName = (projectPath.split(/[/\\]/).pop() || "Dự án karaoke").replace(/\.karaoke\.json$/i, "");
      const projectTitle = video?.project_title || baseName;
      const data = await publishRequest<unknown>("/api/publish/drafts", {
        method: "POST",
        body: JSON.stringify({
          project_path: projectPath,
          project_title: projectTitle,
          platform: "youtube",
          video_role: video?.role || preferredRole,
          title: projectTitle,
          description: "",
          made_for_kids: false,
          privacy: "public",
          playlist_id: "",
          playlist_name: "",
        }),
      });
      const created = draftFromResponse(data);
      if (!created) {
        push({ tone: "error", message: "Server không trả về nháp vừa tạo." });
        return;
      }
      setDrafts((list) => sortDrafts([created, ...list]));
      setSelectedId(created.id);
      setDraft(created);
      setSaveState("saved");
    } catch (error) {
      push({
        tone: "error",
        message: error instanceof Error ? error.message : "Không tạo được nháp.",
      });
    } finally {
      setDraftBusy("");
    }
  }

  async function publish() {
    const current = draftRef.current;
    if (!current) return;
    if (!youtubeConnected) {
      push({ tone: "error", message: "Chưa kết nối YouTube. Hãy kết nối kênh trước khi đăng." });
      return;
    }
    if (!current.project_path) {
      push({ tone: "error", message: "Chọn video đã xuất trước khi đăng." });
      return;
    }
    if (current.title.trim() === "" || /[<>]/.test(current.title)) {
      push({ tone: "error", message: "Tiêu đề không được trống hoặc chứa < >." });
      return;
    }
    if (codePointLength(current.title) > 100 || codePointLength(current.description) > 5000) {
      push({ tone: "error", message: "Tiêu đề hoặc nội dung vượt giới hạn ký tự." });
      return;
    }
    setDraftBusy("publish");
    try {
      const saved = await flushSave(current);
      if (!saved) return;
      const data = await publishRequest<unknown>(`/api/publish/drafts/${current.id}/publish`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      const payload = data as { job?: PublishJob } | PublishJob | null;
      const job: PublishJob | null =
        payload && typeof payload === "object" && "job" in payload && payload.job
          ? payload.job
          : (payload as PublishJob | null);
      if (job && job.id) {
        setJobs((list) => [job, ...list.filter((item) => item.id !== job.id)]);
      }
      await loadJobs();
      push({ tone: "success", message: "Đã đưa video vào hàng đợi đăng." });
    } catch (error) {
      push({
        tone: "error",
        message: error instanceof Error ? error.message : "Không gửi được yêu cầu đăng.",
      });
    } finally {
      setDraftBusy("");
    }
  }

  async function duplicate() {
    const current = draftRef.current;
    if (!current) return;
    setDraftBusy("duplicate");
    try {
      const data = await publishRequest<unknown>(`/api/publish/drafts/${current.id}/duplicate`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      const created = draftFromResponse(data);
      if (!created) {
        push({ tone: "error", message: "Server không trả về bản nhân bản." });
        return;
      }
      setDrafts((list) => sortDrafts([created, ...list]));
      setSelectedId(created.id);
      setDraft(created);
    } catch (error) {
      push({ tone: "error", message: error instanceof Error ? error.message : "Không nhân bản được nháp." });
    } finally {
      setDraftBusy("");
    }
  }

  async function removeDraft() {
    const current = draftRef.current;
    if (!current) return;
    const ok = await confirm({
      title: "Xoá nháp?",
      description: "Nội dung nháp sẽ bị xoá. Video đã đăng trên YouTube không bị ảnh hưởng.",
      confirmLabel: "Xoá nháp",
      cancelLabel: "Giữ lại",
      danger: true,
    });
    if (!ok) return;
    setDraftBusy("delete");
    try {
      await publishRequest(`/api/publish/drafts/${current.id}`, { method: "DELETE" });
      setDrafts((list) => list.filter((item) => item.id !== current.id));
      setSelectedId("");
      setDraft(null);
      push({ tone: "success", message: "Đã xoá nháp." });
    } catch (error) {
      push({ tone: "error", message: error instanceof Error ? error.message : "Không xoá được nháp." });
    } finally {
      setDraftBusy("");
    }
  }

  async function connect(provider: string) {
    setChannelBusy(`${provider}:connect`);
    setChannelsError("");
    // Open synchronously so the browser does not treat it as a blocked popup
    // after the async OAuth call.
    const popup = window.open("about:blank", "_blank");
    try {
      const data = await publishRequest<{ url?: string; authorize_url?: string }>(
        `/api/publish/channels/${provider}/connect`,
        { method: "POST", body: JSON.stringify({}) },
      );
      const url = data?.url || data?.authorize_url;
      if (!url) {
        popup?.close();
        setChannelsError("Server không trả về URL OAuth. Kiểm tra GOOGLE_OAUTH_CLIENT_ID/SECRET.");
        return;
      }
      if (popup && !popup.closed) {
        popup.opener = null;
        popup.location.href = url;
      } else {
        window.location.assign(url);
      }
    } catch (error) {
      popup?.close();
      setChannelsError(error instanceof Error ? error.message : "Không bắt đầu được OAuth.");
    } finally {
      setChannelBusy("");
    }
  }

  async function disconnect(provider: string) {
    setChannelBusy(`${provider}:disconnect`);
    setChannelsError("");
    try {
      await publishRequest(`/api/publish/channels/${provider}`, { method: "DELETE" });
      push({ tone: "success", message: "Đã ngắt kết nối YouTube." });
      await loadChannels();
      setPlaylists([]);
    } catch (error) {
      setChannelsError(error instanceof Error ? error.message : "Không ngắt được kết nối.");
    } finally {
      setChannelBusy("");
    }
  }

  async function retryJob(id: string) {
    setJobBusy(`retry:${id}`);
    try {
      await publishRequest(`/api/publish/jobs/${id}/retry`, { method: "POST", body: JSON.stringify({}) });
      await loadJobs();
    } catch (error) {
      push({ tone: "error", message: error instanceof Error ? error.message : "Không thử lại được." });
    } finally {
      setJobBusy("");
    }
  }

  async function cancelJob(id: string) {
    setJobBusy(`cancel:${id}`);
    try {
      await publishRequest(`/api/publish/jobs/${id}/cancel`, { method: "POST", body: JSON.stringify({}) });
      await loadJobs();
    } catch (error) {
      push({ tone: "error", message: error instanceof Error ? error.message : "Không huỷ được job." });
    } finally {
      setJobBusy("");
    }
  }

  return (
    <>
      <PageHeader
        title="Đăng bài"
        description="Kết nối YouTube, soạn nội dung một lần rồi đăng lại video mới nhất sau khi xuất. TikTok chưa hỗ trợ trong phiên bản này."
        actions={
          <>
            <button type="button" className={btnSecondary} onClick={() => void loadVideos()}>
              Làm mới video
            </button>
            <button type="button" className={btnGhost} onClick={() => void loadDrafts()}>
              Làm mới nháp
            </button>
          </>
        }
      />

      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(330px,0.85fr)]">
        <div className="space-y-5">
          <ChannelCards
            channels={channels}
            loading={channelsLoading}
            error={channelsError}
            busy={channelBusy}
            onConnect={(provider) => void connect(provider)}
            onDisconnect={(provider) => void disconnect(provider)}
            onRetry={() => void loadChannels()}
          />

          <DraftForm
            draft={draft}
            playlists={playlists}
            playlistsLoading={playlistsLoading}
            playlistsError={playlistsError}
            videos={videos}
            videosLoading={videosLoading}
            saveState={saveState}
            saveError={saveError}
            conflict={conflict}
            busy={draftBusy}
            onCreate={() => void createDraft()}
            onReloadConflict={(next) => {
              setConflict(null);
              setSaveError("");
              setSaveState("idle");
              setDraft(next);
              setDrafts((list) => sortDrafts(list.map((item) => (item.id === next.id ? next : item))));
            }}
            onChange={updateDraft}
            onSaveNow={() => void saveNow()}
            onPublish={() => void publish()}
            onDuplicate={() => void duplicate()}
            onDelete={() => void removeDraft()}
          />
        </div>

        <div className="space-y-5">
          <Panel className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <SectionTitle>Nháp</SectionTitle>
              <div className="flex items-center gap-2">
                <Badge tone="muted">{drafts.length}</Badge>
                <button
                  type="button"
                  className={btnPrimary}
                  onClick={() => void createDraft()}
                  disabled={draftBusy !== ""}
                >
                  {draftBusy === "create" ? "Đang tạo…" : "Nháp mới"}
                </button>
              </div>
            </div>
            {draftsError ? (
              <p className="rounded-xl border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs leading-5 text-amber-100">
                {draftsError}
              </p>
            ) : null}
            {draftsLoading ? (
              <p className="text-xs text-[var(--muted)]">Đang tải nháp…</p>
            ) : drafts.length === 0 ? (
              <EmptyState
                title="Chưa có nháp"
                description="Xuất video rồi tạo nháp. Nháp giữ tiêu đề, mô tả, playlist và chế độ đăng để bấm Đăng lại sau."
                action={
                  <button type="button" className={btnSecondary} onClick={() => void createDraft()}>
                    Tạo nháp mới
                  </button>
                }
              />
            ) : (
              <ul className="max-h-[32rem] space-y-2 overflow-auto pr-1">
                {drafts.map((item) => {
                  const active = item.id === selectedId;
                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        onClick={() => selectDraft(item)}
                        className={`w-full rounded-xl border px-3 py-2 text-left transition ${
                          active
                            ? "border-[var(--gold)] bg-black/25"
                            : "border-[var(--line)] bg-black/15 hover:border-[var(--gold)]/60"
                        }`}
                      >
                        <span className="flex flex-wrap items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium">{item.title || "(chưa có tiêu đề)"}</span>
                          <span className="flex shrink-0 gap-1">
                            {item.last_published_at ? <Badge tone="success">đã đăng</Badge> : null}
                            <Badge tone="muted">
                              {item.privacy === "public"
                                ? "công khai"
                                : item.privacy === "unlisted"
                                  ? "không công khai"
                                  : "riêng tư"}
                            </Badge>
                          </span>
                        </span>
                        <span className="mt-1 block truncate text-[11px] text-[var(--muted)]">
                          {item.project_title || item.project_path}
                          {item.video_role ? ` · ${item.video_role === "vocal" ? "có giọng" : "không lời"}` : ""}
                          {item.updated ? ` · ${formatRelative(item.updated)}` : ""}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </Panel>

          {jobsError ? (
            <p className="rounded-xl border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs leading-5 text-amber-100">
              {jobsError}
            </p>
          ) : null}
          <PublishQueue
            jobs={jobs}
            busy={jobBusy}
            onRetry={(id) => void retryJob(id)}
            onCancel={(id) => void cancelJob(id)}
            onRefresh={() => void loadJobs()}
          />
        </div>
      </div>
    </>
  );
}
