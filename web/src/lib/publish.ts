export type PublishChannel = {
  provider: string;
  connected: boolean;
  channel_id?: string;
  name?: string;
  avatar?: string;
  scopes?: string[];
  expires_at?: string;
  message?: string;
};

export type PublishPlaylist = {
  id: string;
  title: string;
  item_count?: number;
};

export type PublishDraft = {
  id: string;
  project_path: string;
  project_title: string;
  platform: "youtube" | "tiktok" | string;
  video_role: "vocal" | "instrumental" | string;
  title: string;
  description: string;
  made_for_kids: boolean;
  privacy: "public" | "unlisted" | "private" | string;
  playlist_id: string;
  playlist_name: string;
  created?: string;
  updated?: string;
  revision: number;
  last_published_at?: string;
  last_video_id?: string;
  last_url?: string;
};

export type PublishJob = {
  id: string;
  draft_id?: string;
  project_path?: string;
  project_title?: string;
  platform?: string;
  video_role?: string;
  status: string;
  step?: string;
  progress?: number;
  attempts?: number;
  last_error?: string;
  created?: string;
  updated?: string;
  platform_video_id?: string;
  last_url?: string;
  url?: string;
};

export type PublishVideo = {
  id?: string;
  project_path: string;
  project_title: string;
  role: "vocal" | "instrumental" | string;
  path: string;
  updated?: string;
};

export class PublishApiError extends Error {
  status: number;
  payload: unknown;

  constructor(status: number, message: string, payload: unknown = null) {
    super(message);
    this.name = "PublishApiError";
    this.status = status;
    this.payload = payload;
  }
}

export async function publishRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!response.ok) {
    const text = await response.text();
    let payload: unknown = null;
    try {
      payload = text ? JSON.parse(text) : null;
    } catch {
      payload = null;
    }
    const fallback = await readErrorFromText(response.status, text);
    throw new PublishApiError(response.status, fallback, payload);
  }
  if (response.status === 204) return null as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : null) as T;
}

async function readErrorFromText(status: number, text: string) {
  try {
    const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown };
    const detail = parsed.detail ?? parsed.message;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (detail && typeof detail === "object") {
      const nested = detail as { message?: unknown; detail?: unknown };
      if (typeof nested.message === "string" && nested.message.trim()) return nested.message;
      if (typeof nested.detail === "string" && nested.detail.trim()) return nested.detail;
      return JSON.stringify(detail);
    }
  } catch {
    /* handled below */
  }
  if (text.trim() && text.length < 300 && !text.includes("<html")) return text.trim();
  return `Yêu cầu không thành công (${status}).`;
}

export function normalizeChannels(value: unknown): PublishChannel[] {
  if (Array.isArray(value)) return value as PublishChannel[];
  if (value && typeof value === "object" && Array.isArray((value as { channels?: unknown }).channels)) {
    return (value as { channels: PublishChannel[] }).channels;
  }
  return [];
}

export function normalizeDrafts(value: unknown): PublishDraft[] {
  if (Array.isArray(value)) return value as PublishDraft[];
  if (value && typeof value === "object" && Array.isArray((value as { drafts?: unknown }).drafts)) {
    return (value as { drafts: PublishDraft[] }).drafts;
  }
  return [];
}

export function normalizeJobs(value: unknown): PublishJob[] {
  if (Array.isArray(value)) return value as PublishJob[];
  if (value && typeof value === "object" && Array.isArray((value as { jobs?: unknown }).jobs)) {
    return (value as { jobs: PublishJob[] }).jobs;
  }
  return [];
}

export function normalizePlaylists(value: unknown): PublishPlaylist[] {
  const raw = Array.isArray(value)
    ? value
    : value && typeof value === "object" && Array.isArray((value as { playlists?: unknown }).playlists)
      ? (value as { playlists: unknown[] }).playlists
      : [];
  const out: PublishPlaylist[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const item = entry as {
      id?: string;
      playlist_id?: string;
      title?: string;
      name?: string;
      item_count?: number;
      snippet?: { title?: string };
      contentDetails?: { itemCount?: number };
    };
    const id = item.id || item.playlist_id;
    const title = item.title || item.snippet?.title || item.name;
    if (!id || !title) continue;
    out.push({ id, title, item_count: item.item_count ?? item.contentDetails?.itemCount });
  }
  return out;
}

export function normalizeVideos(value: unknown): PublishVideo[] {
  if (Array.isArray(value)) return value as PublishVideo[];
  if (value && typeof value === "object" && Array.isArray((value as { videos?: unknown }).videos)) {
    return (value as { videos: PublishVideo[] }).videos;
  }
  return [];
}

export function draftFromResponse(value: unknown): PublishDraft | null {
  if (!value || typeof value !== "object") return null;
  const body = value as { draft?: PublishDraft; project?: unknown };
  if (body.draft && typeof body.draft === "object") return body.draft;
  if ("id" in value && "title" in value) return value as PublishDraft;
  return null;
}

export const JOB_STATUS: Record<string, { label: string; tone: "muted" | "gold" | "success" | "danger" | "info" }> = {
  draft: { label: "Nháp", tone: "muted" },
  queued: { label: "Chờ đăng", tone: "info" },
  initializing: { label: "Chuẩn bị", tone: "gold" },
  uploading: { label: "Đang tải", tone: "gold" },
  finalizing: { label: "Hoàn tất", tone: "gold" },
  published: { label: "Đã đăng", tone: "success" },
  failed: { label: "Lỗi", tone: "danger" },
  cancelled: { label: "Đã huỷ", tone: "muted" },
  cancel_requested: { label: "Đang huỷ", tone: "danger" },
};

export function jobStatus(value: string | undefined) {
  return JOB_STATUS[value || ""] || { label: value || "Không rõ", tone: "muted" as const };
}

export const PRIVACY_OPTIONS = [
  { value: "public", label: "Công khai" },
  { value: "unlisted", label: "Không công khai" },
  { value: "private", label: "Riêng tư" },
] as const;

export function codePointLength(value: string) {
  return Array.from(value).length;
}
