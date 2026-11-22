"use client";

import { useEffect, useState } from "react";

import { useSettings, type AppSettings } from "@/lib/settings";
import { Field, PathField, Select } from "@/components/ui/field";
import { Badge, btnDanger, btnPrimary, Panel } from "@/components/ui/primitives";
import { Drawer, useDialog } from "@/components/ui/dialog";
import { useNotices } from "@/components/ui/toast";

const MODELS = [
  { value: "tiny", label: "Tiny · nhanh nhất" },
  { value: "base", label: "Base" },
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
  { value: "large-v2", label: "Large v2" },
  { value: "large", label: "Large (v3)" },
];

export function SettingsDrawer() {
  const { settings, open, closeSettings, save, available, loading } = useSettings();
  const { confirm } = useDialog();
  const { push } = useNotices();
  const [draft, setDraft] = useState<AppSettings>(settings);
  const [busy, setBusy] = useState(false);
  const [browsing, setBrowsing] = useState(false);
  const [stalePaths, setStalePaths] = useState<string[]>([]);
  const [registryBusy, setRegistryBusy] = useState(false);

  useEffect(() => {
    if (open) setDraft(settings);
  }, [open, settings]);

  useEffect(() => {
    if (!open) return;
    fetch("/api/projects/registry/stale", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : { paths: [] }))
      .then((data: { paths?: string[] }) => setStalePaths(Array.isArray(data.paths) ? data.paths : []))
      .catch(() => setStalePaths([]));
  }, [open]);

  async function forgetStaleProjects() {
    if (stalePaths.length === 0) return;
    const visiblePaths = stalePaths.slice(0, 8);
    const ok = await confirm({
      title: "Quên đường dẫn dự án bị thiếu?",
      description: (
        <div className="space-y-2">
          <p>
            {stalePaths.length} đường dẫn trong danh sách dự án không còn tồn tại:
          </p>
          <ul className="max-h-32 space-y-0.5 overflow-auto rounded-lg border border-[var(--line)] bg-black/20 p-2 text-[11px]">
            {visiblePaths.map((path) => (
              <li key={path} className="truncate" title={path}>
                {path}
              </li>
            ))}
            {stalePaths.length > visiblePaths.length ? (
              <li className="text-[var(--muted)]">…và {stalePaths.length - visiblePaths.length} đường dẫn khác</li>
            ) : null}
          </ul>
          <p className="text-amber-200">
            Chỉ quên khi chắc chắn các thư mục/ổ đĩa này không còn dùng. Nếu chỉ là ổ ngoài đang tháo,
            hãy cắm lại thay vì quên.
          </p>
        </div>
      ),
      confirmLabel: "Quên đường dẫn",
      danger: true,
    });
    if (!ok) return;
    setRegistryBusy(true);
    try {
      const response = await fetch("/api/projects/registry/forget", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ missing_only: true }),
      });
      if (!response.ok) {
        push({ tone: "error", message: "Chưa quên được đường dẫn lỗi. Kiểm tra server Python." });
        return;
      }
      const data = (await response.json()) as { removed?: string[] };
      setStalePaths([]);
      window.dispatchEvent(new Event("karaoke:registry-changed"));
      push({
        tone: "success",
        message: `Đã quên ${data.removed?.length || 0} đường dẫn dự án không còn tồn tại.`,
      });
    } catch {
      push({ tone: "error", message: "Chưa quên được đường dẫn lỗi. Kiểm tra server Python." });
    } finally {
      setRegistryBusy(false);
    }
  }

  async function pickOutput() {
    setBrowsing(true);
    try {
      const response = await fetch("/api/browse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: "output", initial_dir: draft.output_dir || "" }),
      });
      if (!response.ok) return;
      const data = (await response.json()) as { paths?: string[] };
      const picked = data.paths?.[0];
      if (picked) setDraft((current) => ({ ...current, output_dir: picked }));
    } catch {
      push({ tone: "error", message: "Không mở được hộp thoại chọn thư mục." });
    } finally {
      setBrowsing(false);
    }
  }

  async function submit() {
    setBusy(true);
    const ok = await save(draft);
    setBusy(false);
    if (ok) closeSettings();
  }

  return (
    <Drawer
      open={open}
      onClose={closeSettings}
      title="Cài đặt"
      description="Áp dụng cho dự án mới. Giá trị trong dự án đang mở vẫn được ưu tiên."
      width="max-w-lg"
      footer={
        <div className="flex items-center justify-between gap-3">
          <span className="text-[11px] text-[var(--muted)]">Dự án &gt; Cài đặt &gt; Mặc định</span>
          <button type="button" className={btnPrimary} onClick={submit} disabled={busy || loading}>
            {busy ? "Đang lưu…" : "Lưu cài đặt"}
          </button>
        </div>
      }
    >
      <div className="space-y-5">
        {!available && !loading ? (
          <p className="rounded-xl border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-xs leading-5 text-amber-100">
            Server chưa có API settings. Giá trị dưới đây vẫn dùng được trong phiên này nhưng sẽ mất khi tải lại.
          </p>
        ) : null}

        <Panel className="space-y-3 bg-black/15">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Hiệu ứng AI</h3>
            <Badge tone={draft.gemini_consent ? "success" : "muted"}>
              {draft.gemini_consent ? "Đã đồng ý" : "Chưa đồng ý"}
            </Badge>
          </div>
          <label className="flex items-start gap-2 text-sm leading-5">
            <input
              type="checkbox"
              className="mt-0.5 accent-[var(--gold)]"
              checked={draft.gemini_consent}
              onChange={(event) => setDraft((current) => ({ ...current, gemini_consent: event.target.checked }))}
            />
            <span>
              Gửi ảnh lên Google Gemini để phân tích hiệu ứng
              <span className="mt-1 block text-xs leading-4 text-[var(--muted)]">
                Mỗi lần Áp dụng vẫn ghi `effect_consent_receipt` (ảnh, model, thời điểm). Tắt tuỳ chọn này sẽ chặn phân tích mới.
              </span>
            </span>
          </label>
        </Panel>

        <div className="space-y-3">
          <h3 className="text-sm font-semibold">Căn lời</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Thiết bị">
              <Select
                value={draft.device}
                onChange={(event) => setDraft((current) => ({ ...current, device: event.target.value }))}
              >
                <option value="cpu">CPU</option>
                <option value="mps">GPU Apple</option>
              </Select>
            </Field>
            <Field label="Model Whisper">
              <Select
                value={draft.model_name}
                onChange={(event) => setDraft((current) => ({ ...current, model_name: event.target.value }))}
              >
                {MODELS.map((model) => (
                  <option key={model.value} value={model.value}>
                    {model.label}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <p className="text-xs leading-5 text-[var(--muted)]">
            Có thể đổi riêng trong màn Ghép chữ nếu dự án cần model khác.
          </p>
        </div>

        <div className="space-y-3">
          <h3 className="text-sm font-semibold">Dữ liệu dự án</h3>
          <p className="text-xs leading-5 text-[var(--muted)]">
            Nếu một ổ đĩa hoặc thư mục dự án bị xoá hẳn, đường dẫn còn lại trong danh sách có thể
            khiến việc xoá/dọn clip bị chặn để bảo vệ dữ liệu.
          </p>
          {stalePaths.length > 0 ? (
            <div className="space-y-2 rounded-xl border border-amber-900/60 bg-amber-950/30 p-3">
              <p className="text-xs leading-5 text-amber-100">
                {stalePaths.length} đường dẫn dự án không còn tồn tại.
              </p>
              <button
                type="button"
                className={btnDanger}
                disabled={registryBusy}
                onClick={() => void forgetStaleProjects()}
              >
                {registryBusy ? "Đang quên…" : "Quên đường dẫn lỗi"}
              </button>
            </div>
          ) : (
            <p className="text-xs text-emerald-200">Không có đường dẫn dự án bị thiếu.</p>
          )}
        </div>

        <div className="space-y-3">
          <h3 className="text-sm font-semibold">Xuất mặc định</h3>
          <PathField
            label="Thư mục xuất"
            value={draft.output_dir}
            placeholder="Nơi lưu dự án và video"
            onBrowse={pickOutput}
            busy={browsing}
            hint="Dự án mới sẽ dùng thư mục này nếu chưa chọn nơi khác."
          />
        </div>
      </div>
    </Drawer>
  );
}
