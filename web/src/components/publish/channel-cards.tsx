"use client";

import { useDialog } from "@/components/ui/dialog";
import { Badge, Panel, SectionTitle, btnDanger, btnPrimary, btnSecondary } from "@/components/ui/primitives";
import type { PublishChannel } from "@/lib/publish";

function youtubeChannel(channels: PublishChannel[]) {
  return channels.find((channel) => channel.provider === "youtube") || null;
}

export function ChannelCards({
  channels,
  loading,
  error,
  busy,
  onConnect,
  onDisconnect,
  onRetry,
}: {
  channels: PublishChannel[];
  loading: boolean;
  error: string;
  busy: string;
  onConnect: (provider: string) => void;
  onDisconnect: (provider: string) => void;
  onRetry: () => void;
}) {
  const { confirm } = useDialog();
  const channel = youtubeChannel(channels);
  const connected = Boolean(channel?.connected);
  const missingScope =
    connected && Array.isArray(channel?.scopes) && !channel.scopes.includes("youtube.force-ssl");

  async function disconnect() {
    const ok = await confirm({
      title: "Ngắt kết nối YouTube?",
      description:
        "Token trên máy sẽ bị xoá. Video đã đăng vẫn còn trên YouTube; muốn đăng tiếp phải kết nối lại.",
      confirmLabel: "Ngắt kết nối",
      cancelLabel: "Giữ kết nối",
      danger: true,
    });
    if (ok) onDisconnect("youtube");
  }

  return (
    <Panel className="space-y-3">
      <SectionTitle action={loading ? <Badge tone="muted">Đang kiểm tra…</Badge> : null}>Kênh</SectionTitle>
      <div
        className={`rounded-2xl border p-3 transition ${
          connected ? "border-emerald-800/70 bg-emerald-950/20" : "border-[var(--line)] bg-black/15"
        }`}
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <span
              aria-hidden
              className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${
                connected ? "bg-emerald-400" : "bg-[var(--muted)]"
              }`}
            />
            <div className="min-w-0">
              <p className="flex flex-wrap items-center gap-2 text-sm font-semibold">
                YouTube
                <Badge tone={connected ? "success" : "muted"}>{connected ? "Đã kết nối" : "Chưa kết nối"}</Badge>
              </p>
              <p className="mt-1 truncate text-xs leading-5 text-[var(--muted)]">
                {connected
                  ? channel?.name || channel?.channel_id || "Kênh đã kết nối"
                  : "Kết nối một kênh để đăng video và chọn playlist."}
              </p>
              {connected && channel?.expires_at ? (
                <p className="text-[11px] leading-4 text-[var(--muted)]">Token hết hạn: {channel.expires_at}</p>
              ) : null}
              {missingScope ? (
                <p className="mt-1 text-[11px] leading-4 text-amber-200">
                  Token thiếu quyền youtube.force-ssl (cần cho playlist). Hãy ngắt kết nối rồi kết nối lại.
                </p>
              ) : null}
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            {connected ? (
              <button
                type="button"
                onClick={() => void disconnect()}
                disabled={busy !== ""}
                className={btnDanger}
              >
                {busy === "youtube:disconnect" ? "Đang ngắt…" : "Ngắt kết nối"}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => onConnect("youtube")}
                disabled={busy !== ""}
                className={btnPrimary}
              >
                {busy === "youtube:connect" ? "Đang mở…" : "Kết nối YouTube"}
              </button>
            )}
          </div>
        </div>
      </div>
      {error ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs leading-5 text-amber-100">
          <span>{error}</span>
          <button type="button" onClick={onRetry} className={btnSecondary}>
            Thử lại
          </button>
        </div>
      ) : null}
      <p className="text-[11px] leading-5 text-[var(--muted)]">
        TikTok chưa hỗ trợ trong phiên bản này. Token được lưu ngoài file dự án và không gửi xuống trình duyệt.
        Video cover có thể bị Content ID claim; bạn chịu trách nhiệm về quyền sử dụng bài hát.
      </p>
    </Panel>
  );
}
