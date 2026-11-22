"use client";

import { useEffect, useMemo, useState } from "react";

import { Badge, btnSecondary, EmptyState, Spinner } from "@/components/ui/primitives";
import { useDialog } from "@/components/ui/dialog";
import { useNotices } from "@/components/ui/toast";
import { IconDownload, IconExternal, IconGrid, IconLibrary, IconList, IconMore, IconPencil, IconRefresh, IconSearch, IconTrash } from "@/components/ui/icons";
import { readError } from "@/lib/api";
import { effectName, fileName, formatBytes, formatRelative, formatSeconds, normalizeSearch, profileMeta } from "@/lib/format";
import type { StudioAsset } from "@/lib/types";

export type StudioAssetSummary = StudioAsset & {
  duration_seconds?: number;
  effect_names?: string[];
  object_count?: number;
  manual_object_count?: number;
  used_projects?: string[];
  usage_known?: boolean;
};

const SORTS = [
  { value: "new", label: "Mới nhất" },
  { value: "old", label: "Cũ nhất" },
  { value: "name", label: "Tên A–Z" },
  { value: "duration", label: "Thời lượng" },
];

function assetDuration(asset: StudioAssetSummary) {
  if (typeof asset.duration_seconds === "number") return asset.duration_seconds;
  if (asset.asset_frames && asset.fps) return asset.asset_frames / asset.fps;
  return asset.loop_duration || 0;
}

function assetEffects(asset: StudioAssetSummary) {
  if (Array.isArray(asset.effect_names)) return asset.effect_names;
  return (asset.effect_plan?.items || []).map((item) => item.effect).filter(Boolean);
}

function AssetThumb({ asset, className = "" }: { asset: StudioAssetSummary; className?: string }) {
  const [hover, setHover] = useState(false);
  const [failed, setFailed] = useState(false);
  const meta = profileMeta(asset.profile_id);
  return (
    <div
      className={`relative overflow-hidden rounded-xl border border-[var(--line)] bg-black ${className}`}
      style={{ aspectRatio: meta.aspect }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      {hover && !failed ? (
        <video
          src={`/api/studio/assets/${asset.id}/clip`}
          autoPlay
          muted
          loop
          playsInline
          preload="none"
          className="h-full w-full object-contain"
        />
      ) : (
        <img
          src={`/api/studio/assets/${asset.id}/thumb`}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
          className="h-full w-full object-contain"
        />
      )}
      {failed && !hover ? (
        <div className="absolute inset-0 grid place-items-center text-[var(--muted)]">
          <IconLibrary className="h-6 w-6" />
        </div>
      ) : null}
      <span className="absolute right-1.5 bottom-1.5 rounded-md bg-black/70 px-1.5 py-0.5 text-[10px] text-[var(--paper)]">
        {formatSeconds(assetDuration(asset))}
      </span>
    </div>
  );
}

export function AssetLibrary({
  assets,
  usage,
  loading,
  onRefresh,
  onOpen,
}: {
  assets: StudioAssetSummary[];
  usage: { count: number; bytes: number } | null;
  loading: boolean;
  onRefresh: () => void;
  onOpen: (asset: StudioAssetSummary) => void;
}) {
  const { confirm, prompt } = useDialog();
  const { push } = useNotices();
  const [query, setQuery] = useState("");
  const [profile, setProfile] = useState("all");
  const [sort, setSort] = useState("new");
  const [view, setView] = useState<"grid" | "list">("grid");
  const [menu, setMenu] = useState("");
  const [busy, setBusy] = useState("");

  useEffect(() => {
    if (!menu) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setMenu("");
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [menu]);

  const filtered = useMemo(() => {
    const needle = normalizeSearch(query);
    const next = assets.filter((asset) => {
      if (profile !== "all" && (asset.profile_id || "landscape_1080p") !== profile) return false;
      if (!needle) return true;
      const haystack = normalizeSearch(
        [asset.name, fileName(asset.source_image || ""), ...assetEffects(asset)].join(" "),
      );
      return haystack.includes(needle);
    });
    next.sort((a, b) => {
      if (sort === "name") return a.name.localeCompare(b.name, "vi");
      if (sort === "duration") return assetDuration(b) - assetDuration(a);
      const left = Date.parse(a.created || "") || 0;
      const right = Date.parse(b.created || "") || 0;
      return sort === "old" ? left - right : right - left;
    });
    return next;
  }, [assets, profile, query, sort]);

  async function rename(asset: StudioAssetSummary) {
    setMenu("");
    const name = await prompt({
      title: "Đổi tên clip",
      description: "Tên chỉ đổi trong Thư viện, không ảnh hưởng file clip.",
      input: { label: "Tên clip", defaultValue: asset.name },
      confirmLabel: "Đổi tên",
    });
    if (!name || name === asset.name) return;
    setBusy(asset.id);
    try {
      const response = await fetch(`/api/studio/assets/${asset.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!response.ok) {
        push({ tone: "error", message: await readError(response) });
        return;
      }
      push({ tone: "success", message: `Đã đổi tên thành “${name}”.` });
      onRefresh();
    } catch {
      push({ tone: "error", message: "Không đổi tên được. Kiểm tra server Python." });
    } finally {
      setBusy("");
    }
  }

  async function remove(asset: StudioAssetSummary) {
    setMenu("");
    const used = asset.used_projects?.length ? asset.used_projects : [];
    if (used.length) {
      await confirm({
        title: "Không thể xoá clip đang dùng",
        description: `Clip này đang được tham chiếu bởi: ${Array.from(new Set(used.map((path) => fileName(path)))).join(", ")}. Hãy bỏ clip khỏi các dự án đó trước, hoặc giữ lại để lần xuất sau dùng lại.`,
        confirmLabel: "Đã hiểu",
        cancelLabel: "Đóng",
      });
      return;
    }
    const ok = await confirm({
      title: "Xoá clip?",
      description: `“${asset.name}” sẽ bị xoá khỏi thư viện. Không thể hoàn tác.`,
      confirmLabel: "Xoá clip",
      danger: true,
    });
    if (!ok) return;
    setBusy(asset.id);
    try {
      const response = await fetch(`/api/studio/assets/${asset.id}`, { method: "DELETE" });
      if (!response.ok) {
        push({ tone: "error", message: await readError(response) });
        return;
      }
      push({ tone: "success", message: `Đã xoá “${asset.name}”.` });
      onRefresh();
    } catch {
      push({ tone: "error", message: "Không xoá được clip. Kiểm tra server Python." });
    } finally {
      setBusy("");
    }
  }

  async function reveal(asset: StudioAssetSummary) {
    setMenu("");
    try {
      const response = await fetch(`/api/studio/assets/${asset.id}/reveal`, { method: "POST" });
      if (!response.ok) push({ tone: "error", message: await readError(response) });
    } catch {
      push({ tone: "error", message: "Không mở được thư mục clip." });
    }
  }

  async function cleanup() {
    setBusy("cleanup");
    try {
      const dryResponse = await fetch("/api/studio/assets/cleanup?dry_run=1", { method: "POST" });
      if (!dryResponse.ok) {
        push({ tone: "error", message: await readError(dryResponse) });
        return;
      }
      const dry = (await dryResponse.json()) as { deleted?: string[]; freed_bytes?: number };
      if (!dry.deleted?.length) {
        push({ tone: "info", message: "Không có clip cũ nào đủ điều kiện để dọn." });
        return;
      }
      const ok = await confirm({
        title: "Dọn clip không dùng?",
        description: `Sẽ xoá ${dry.deleted.length} clip cũ, giải phóng khoảng ${formatBytes(dry.freed_bytes || 0)}. Clip đang được dự án dùng sẽ được giữ lại.`,
        confirmLabel: "Dọn clip",
        danger: true,
      });
      if (!ok) return;
      const response = await fetch("/api/studio/assets/cleanup", { method: "POST" });
      if (!response.ok) {
        push({ tone: "error", message: await readError(response) });
        return;
      }
      const data = (await response.json()) as { deleted?: string[]; freed_bytes?: number };
      push({
        tone: "success",
        message: `Đã xoá ${data.deleted?.length || 0} clip, giải phóng ${formatBytes(data.freed_bytes || 0)}.`,
      });
      onRefresh();
    } catch {
      push({ tone: "error", message: "Không dọn được clip. Kiểm tra server Python." });
    } finally {
      setBusy("");
    }
  }

  function menuFor(asset: StudioAssetSummary) {
    return (
      <div className="absolute top-full right-0 z-40 mt-1 w-44 overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--panel)] py-1 text-left text-xs shadow-2xl">
        <button
          type="button"
          className="flex w-full items-center gap-2 px-3 py-2 hover:bg-white/5"
          onClick={() => {
            setMenu("");
            onOpen(asset);
          }}
        >
          <IconPencil className="h-3.5 w-3.5" /> Mở để sửa
        </button>
        <button type="button" className="flex w-full items-center gap-2 px-3 py-2 hover:bg-white/5" onClick={() => void rename(asset)}>
          <IconPencil className="h-3.5 w-3.5" /> Đổi tên
        </button>
        <a href={`/api/studio/assets/${asset.id}/clip`} download className="flex w-full items-center gap-2 px-3 py-2 hover:bg-white/5">
          <IconDownload className="h-3.5 w-3.5" /> Tải clip
        </a>
        <button type="button" className="flex w-full items-center gap-2 px-3 py-2 hover:bg-white/5" onClick={() => void reveal(asset)}>
          <IconExternal className="h-3.5 w-3.5" /> Mở thư mục
        </button>
        <div className="my-1 border-t border-[var(--line)]" />
        <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-red-200 hover:bg-red-950/40" onClick={() => void remove(asset)}>
          <IconTrash className="h-3.5 w-3.5" /> Xoá clip
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-[12rem] flex-1">
          <IconSearch className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-[var(--muted)]" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tìm theo tên, file hoặc hiệu ứng…"
            aria-label="Tìm clip"
            className="w-full rounded-xl border border-[var(--line)] bg-black/25 py-2 pr-3 pl-9 text-sm outline-none focus:border-[var(--gold)]"
          />
        </div>
        <select
          aria-label="Lọc theo định dạng"
          value={profile}
          onChange={(event) => setProfile(event.target.value)}
          className="rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2 text-sm outline-none focus:border-[var(--gold)]"
        >
          <option value="all">Mọi định dạng</option>
          <option value="landscape_1080p">16:9</option>
          <option value="portrait_1080x1920">9:16</option>
          <option value="square_1080">1:1</option>
          <option value="portrait_4x5">4:5</option>
        </select>
        <select
          aria-label="Sắp xếp"
          value={sort}
          onChange={(event) => setSort(event.target.value)}
          className="rounded-xl border border-[var(--line)] bg-black/25 px-3 py-2 text-sm outline-none focus:border-[var(--gold)]"
        >
          {SORTS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
        <div className="ml-auto flex items-center gap-2">
          {usage ? (
            <span className="text-xs text-[var(--muted)]">
              {usage.count} clip · {formatBytes(usage.bytes)}
            </span>
          ) : null}
          <button type="button" className={btnSecondary} onClick={() => void cleanup()} disabled={busy === "cleanup"}>
            {busy === "cleanup" ? "Đang dọn…" : "Dọn…"}
          </button>
          <div className="inline-flex overflow-hidden rounded-xl border border-[var(--line)]">
            <button
              type="button"
              aria-label="Xem dạng lưới"
              aria-pressed={view === "grid"}
              onClick={() => setView("grid")}
              className={`px-2.5 py-2 ${view === "grid" ? "bg-[var(--gold)] text-[#1a1206]" : "text-[var(--muted)] hover:text-white"}`}
            >
              <IconGrid className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label="Xem dạng danh sách"
              aria-pressed={view === "list"}
              onClick={() => setView("list")}
              className={`border-l border-[var(--line)] px-2.5 py-2 ${view === "list" ? "bg-[var(--gold)] text-[#1a1206]" : "text-[var(--muted)] hover:text-white"}`}
            >
              <IconList className="h-4 w-4" />
            </button>
          </div>
          <button type="button" className={btnSecondary} onClick={onRefresh} disabled={loading}>
            {loading ? <Spinner /> : <IconRefresh className="h-4 w-4" />}
            Tải lại
          </button>
        </div>
      </div>

      {loading && assets.length === 0 ? (
        <div className="flex items-center justify-center gap-2 rounded-2xl border border-dashed border-[var(--line)] py-12 text-sm text-[var(--muted)]">
          <Spinner /> Đang tải thư viện clip…
        </div>
      ) : assets.length === 0 ? (
        <EmptyState
          title="Chưa có clip nào"
          description="Render bản chuẩn trong tab Biên tập rồi bấm Lưu thành clip. Clip đã lưu sẽ xuất hiện ở đây để dùng lại trong dự án Karaoke."
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="Không tìm thấy clip phù hợp"
          description="Thử xoá từ khoá tìm kiếm hoặc chọn lại định dạng."
          action={
            <button type="button" className={btnSecondary} onClick={() => { setQuery(""); setProfile("all"); }}>
              Xoá bộ lọc
            </button>
          }
        />
      ) : view === "grid" ? (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {filtered.map((asset) => {
            const meta = profileMeta(asset.profile_id);
            const effects = assetEffects(asset);
            const used = asset.used_projects || [];
            return (
              <li key={asset.id} className="group relative rounded-2xl border border-[var(--line)] bg-[var(--panel)] p-2 transition hover:border-[var(--gold)]/60">
                <button type="button" className="block w-full text-left" onClick={() => onOpen(asset)}>
                  <AssetThumb asset={asset} />
                  <span className="mt-2 block truncate px-1 text-sm font-medium">{asset.name}</span>
                  <span className="block truncate px-1 text-[11px] text-[var(--muted)]">
                    {meta.label}
                    {effects.length ? ` · ${effects.slice(0, 2).map(effectName).join(", ")}` : ""}
                  </span>
                </button>
                <div className="absolute top-2 right-2 flex items-center gap-1">
                  {used.length ? <Badge tone="gold">Đang dùng</Badge> : null}
                  <div className="relative">
                    <button
                      type="button"
                      aria-label={`Thao tác cho ${asset.name}`}
                      onClick={() => setMenu((current) => (current === asset.id ? "" : asset.id))}
                      className="rounded-md bg-black/70 p-1.5 text-[var(--muted)] hover:text-white"
                    >
                      <IconMore className="h-3.5 w-3.5" />
                    </button>
                    {menu === asset.id ? menuFor(asset) : null}
                  </div>
                </div>
                {busy === asset.id ? (
                  <span className="absolute inset-0 grid place-items-center rounded-2xl bg-black/60">
                    <Spinner className="text-[var(--gold)]" />
                  </span>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : (
        <ul className="divide-y divide-[var(--line)] overflow-visible rounded-2xl border border-[var(--line)] bg-[var(--panel)]">
          {filtered.map((asset) => {
            const meta = profileMeta(asset.profile_id);
            const effects = assetEffects(asset);
            const used = asset.used_projects || [];
            return (
              <li key={asset.id} className="flex items-center gap-3 px-3 py-2">
                <button type="button" onClick={() => onOpen(asset)} className="shrink-0">
                  <AssetThumb asset={asset} className="w-24" />
                </button>
                <button type="button" onClick={() => onOpen(asset)} className="min-w-0 flex-1 text-left">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium">{asset.name}</span>
                    {used.length ? <Badge tone="gold">Đang dùng</Badge> : null}
                  </span>
                  <span className="mt-0.5 block truncate text-[11px] text-[var(--muted)]">
                    {meta.label} · {formatSeconds(assetDuration(asset))} · {fileName(asset.source_image || "")}
                    {effects.length ? ` · ${effects.map(effectName).join(", ")}` : ""}
                  </span>
                </button>
                <span className="hidden shrink-0 text-[11px] text-[var(--muted)] sm:block">
                  {formatRelative(asset.created)}
                </span>
                <div className="relative shrink-0">
                  <button
                    type="button"
                    aria-label={`Thao tác cho ${asset.name}`}
                    onClick={() => setMenu((current) => (current === asset.id ? "" : asset.id))}
                    className="rounded-md p-1.5 text-[var(--muted)] hover:bg-white/5 hover:text-white"
                  >
                    <IconMore className="h-4 w-4" />
                  </button>
                  {menu === asset.id ? menuFor(asset) : null}
                </div>
                {busy === asset.id ? <Spinner className="text-[var(--gold)]" /> : null}
              </li>
            );
          })}
        </ul>
      )}

      {menu ? (
        <button
          type="button"
          aria-label="Đóng menu"
          className="fixed inset-0 z-30 cursor-default"
          onClick={() => setMenu("")}
        />
      ) : null}
    </div>
  );
}
