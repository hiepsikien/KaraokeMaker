"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { AiEffects } from "@/components/ai-effects";
import { AssetLibrary, type StudioAssetSummary } from "@/components/studio/asset-library";
import { PageHeader } from "@/components/ui/page-header";
import { Badge, Segmented } from "@/components/ui/primitives";
import { useDialog } from "@/components/ui/dialog";
import { useNotices } from "@/components/ui/toast";
import { readError } from "@/lib/api";
import { PROFILE_OPTIONS } from "@/lib/format";
import { sessionKeys, useSessionState } from "@/lib/session";
import type { BackgroundEffect, StudioAsset } from "@/lib/types";

type Tab = "editor" | "library";

function fileName(path: string) {
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

function effectFromAsset(asset: StudioAsset): BackgroundEffect | null {
  const plan = asset.effect_plan;
  if (!asset.source_image || !plan) return null;
  return {
    image_path: asset.source_image,
    image_hash: asset.image_hash,
    effect_prompt: `Clip ${asset.name}`,
    effect_consent_receipt: {
      timestamp: asset.created || new Date().toISOString(),
      image_hash: asset.image_hash,
      provider: "studio",
      model: "asset",
    },
    selected_object_ids: plan.selected_object_ids || [],
    effect_plan: plan,
    model: "studio",
    created: asset.created || new Date().toISOString(),
    asset_id: asset.id,
  };
}

export function StudioWorkspace() {
  const router = useRouter();
  const params = useSearchParams();
  const tabParam = params.get("tab");
  const assetParam = params.get("asset") || "";
  const { confirm } = useDialog();
  const { push } = useNotices();

  const [images, setImages] = useSessionState<string[]>(sessionKeys.studioImages, []);
  const [effects, setEffects] = useSessionState<BackgroundEffect[]>(sessionKeys.studioEffects, []);
  const [profile, setProfile] = useSessionState<string>(sessionKeys.studioProfile, "landscape_1080p");
  const [duration, setDuration] = useSessionState<number>(sessionKeys.studioDuration, 8);
  const [tab, setTab] = useState<Tab>(tabParam === "library" ? "library" : "editor");
  const [resumeAsset, setResumeAsset] = useState<{ asset: StudioAsset; token: number } | null>(null);
  const [assetsFull, setAssetsFull] = useState<StudioAsset[]>([]);
  const [assetsSummary, setAssetsSummary] = useState<StudioAssetSummary[]>([]);
  const [usage, setUsage] = useState<{ count: number; bytes: number } | null>(null);
  const [assetsLoading, setAssetsLoading] = useState(false);
  const [busyBrowse, setBusyBrowse] = useState(false);
  const [loadingAssets, setLoadingAssets] = useState(true);
  const handledAsset = useRef("");

  const loadFullAssets = useCallback(async () => {
    try {
      const response = await fetch("/api/studio/assets", { cache: "no-store" });
      if (!response.ok) return;
      const data = (await response.json()) as { assets?: StudioAsset[] };
      setAssetsFull(Array.isArray(data.assets) ? data.assets : []);
    } catch {
      /* toasts on explicit library refresh */
    }
  }, []);

  const loadLibrary = useCallback(async () => {
    setAssetsLoading(true);
    try {
      const [summaryResponse, usageResponse] = await Promise.all([
        fetch("/api/studio/assets?summary=1&usage=1", { cache: "no-store" }),
        fetch("/api/studio/assets/usage", { cache: "no-store" }),
      ]);
      if (summaryResponse.ok) {
        const data = (await summaryResponse.json()) as { assets?: StudioAssetSummary[] };
        setAssetsSummary(Array.isArray(data.assets) ? data.assets : []);
      } else {
        push({ tone: "error", message: await readError(summaryResponse) });
      }
      if (usageResponse.ok) {
        const data = (await usageResponse.json()) as { count?: number; bytes?: number };
        setUsage({ count: data.count || 0, bytes: data.bytes || 0 });
      }
    } catch {
      push({ tone: "error", message: "Không tải được thư viện clip. Kiểm tra server Python." });
    } finally {
      setAssetsLoading(false);
    }
  }, [push]);

  useEffect(() => {
    void loadFullAssets().finally(() => setLoadingAssets(false));
  }, [loadFullAssets]);

  useEffect(() => {
    if (tab === "library") void loadLibrary();
  }, [tab, loadLibrary]);

  useEffect(() => {
    const next: Tab = tabParam === "library" ? "library" : "editor";
    setTab((current) => (current === next ? current : next));
  }, [tabParam]);

  function changeTab(next: Tab) {
    setTab(next);
    if (next === "library") {
      router.replace("/studio?tab=library");
    } else {
      router.replace("/studio");
    }
  }

  const openAsset = useCallback(
    async (asset: StudioAsset | StudioAssetSummary) => {
      try {
        const response = await fetch(`/api/studio/assets/${asset.id}/edit`, { method: "POST" });
        if (!response.ok) {
          push({ tone: "error", message: await readError(response) });
          return;
        }
        const data = (await response.json()) as { asset?: StudioAsset };
        const opened = data.asset || (asset as StudioAsset);
        const effect = effectFromAsset(opened);
        if (!effect) {
          push({ tone: "error", message: "Clip này thiếu ảnh nguồn hoặc effect plan nên chưa mở chỉnh sửa được." });
          return;
        }
        const existing = effects.find((item) => item.image_path === opened.source_image);
        if (existing && existing.asset_id !== opened.id) {
          const conflict = await confirm({
            title: "Ảnh này đang có hiệu ứng khác",
            description: `Mở clip “${opened.name}” sẽ thay thế effect plan hiện tại của ${fileName(opened.source_image)}. Bản nháp đang sửa vẫn còn trong phiên nếu bạn chọn Ở lại.`,
            confirmLabel: "Mở thay thế",
            cancelLabel: "Ở lại",
            danger: true,
          });
          if (!conflict) return;
        }
        setImages((current) => (current.includes(opened.source_image) ? current : [...current, opened.source_image]));
        if (opened.profile_id && PROFILE_OPTIONS.some((option) => option.value === opened.profile_id)) {
          setProfile(opened.profile_id);
        }
        if (opened.asset_frames && opened.fps) {
          setDuration(Math.max(0.5, Math.min(30, Number((opened.asset_frames / opened.fps).toFixed(1)))));
        }
        setEffects((current) => [
          ...current.filter((item) => item.image_path !== opened.source_image),
          effect,
        ]);
        setResumeAsset({ asset: opened, token: Date.now() });
        handledAsset.current = `a:${opened.id}`;
        setTab("editor");
        router.replace(`/studio?asset=${encodeURIComponent(opened.id)}`);
      } catch {
        push({ tone: "error", message: "Không mở được clip. Kiểm tra server Python đang chạy." });
      }
    },
    [confirm, effects, push, router, setDuration, setEffects, setImages, setProfile],
  );

  useEffect(() => {
    if (!assetParam || handledAsset.current === `a:${assetParam}`) return;
    handledAsset.current = `a:${assetParam}`;
    void openAsset({ id: assetParam } as StudioAssetSummary);
  }, [assetParam, openAsset]);

  async function pickImages() {
    if (busyBrowse) return;
    setBusyBrowse(true);
    try {
      const response = await fetch("/api/browse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "images", initial_dir: "" }),
      });
      if (!response.ok) {
        push({ tone: "error", message: await readError(response) });
        return;
      }
      const data = (await response.json()) as { paths?: string[] };
      const picked = (data.paths || []).filter(Boolean);
      if (picked.length === 0) return;
      setImages((current) => [...current, ...picked.filter((path) => !current.includes(path))]);
    } catch {
      push({ tone: "error", message: "Không mở được hộp thoại chọn ảnh. Kiểm tra server Python." });
    } finally {
      setBusyBrowse(false);
    }
  }

  function removeImage(path: string) {
    setImages((current) => current.filter((item) => item !== path));
    setEffects((current) => current.filter((item) => item.image_path !== path));
    setResumeAsset((current) => (current?.asset.source_image === path ? null : current));
  }

  function changeProfile(value: string) {
    setProfile(value);
    setResumeAsset(null);
  }

  function changeDuration(value: number) {
    setDuration(value);
    setResumeAsset(null);
  }

  async function refreshAll() {
    await Promise.all([loadFullAssets(), tab === "library" ? loadLibrary() : Promise.resolve()]);
  }

  return (
    <>
      <PageHeader
        title="AI Studio"
        description="Tạo clip nền động từ ảnh tĩnh, lưu vào thư viện rồi dùng lại trong dự án Karaoke."
        actions={
          <Segmented<Tab>
            value={tab}
            onChange={changeTab}
            ariaLabel="Khu vực AI Studio"
            options={[
              { value: "editor", label: "Biên tập" },
              { value: "library", label: "Thư viện", badge: assetsSummary.length || assetsFull.length || undefined },
            ]}
          />
        }
      >
        {tab === "library" ? (
          <p className="text-xs text-[var(--muted)]">
            {assetsSummary.length} clip đã lưu · tìm kiếm không dấu, lọc theo định dạng, xem lưới hoặc danh sách.
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--muted)]">
            <Badge tone="muted">{images.length} ảnh</Badge>
            <Badge tone="gold">{effects.length} hiệu ứng</Badge>
            <span>Chọn ảnh ở cột trái, phân tích và áp dụng ở cột giữa; kết quả render nằm cùng sân khấu.</span>
          </div>
        )}
      </PageHeader>

      <div className={tab === "editor" ? "" : "hidden"}>
        <AiEffects
          layout="studio"
          images={images}
          effects={effects}
          outputProfile={profile}
          imageDuration={String(duration)}
          resumeAsset={resumeAsset}
          assets={assetsFull}
          onAssetsRefresh={() => void refreshAll()}
          onAddImages={pickImages}
          onRemoveImage={removeImage}
          busyImages={busyBrowse}
          onProfileChange={changeProfile}
          onDurationChange={changeDuration}
          onEffectsChange={(updater) => {
            setEffects((current) => updater(current));
            setResumeAsset(null);
          }}
        />
      </div>

      <div className={tab === "library" ? "" : "hidden"}>
        <AssetLibrary
          assets={assetsSummary}
          usage={usage}
          loading={assetsLoading || loadingAssets}
          onRefresh={() => {
            void loadFullAssets();
            void loadLibrary();
          }}
          onOpen={(asset) => void openAsset(asset)}
        />
      </div>
    </>
  );
}
