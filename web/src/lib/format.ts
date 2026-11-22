export function fileName(path: string) {
  const parts = path.split(/[/\\]/);
  return parts[parts.length - 1] || path;
}

export function formatBytes(bytes: number | null | undefined) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

export function formatSeconds(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "";
  return `${value.toFixed(1)}s`;
}

export function formatRelative(value: string | number | null | undefined) {
  if (value == null || value === "") return "";
  const time = typeof value === "number" ? value * 1000 : Date.parse(value);
  if (!Number.isFinite(time)) return "";
  const diff = Date.now() - time;
  if (diff < 60_000) return "vừa xong";
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)} phút trước`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)} giờ trước`;
  if (diff < 7 * 86_400_000) return `${Math.round(diff / 86_400_000)} ngày trước`;
  return new Date(time).toLocaleDateString("vi-VN");
}

/** Accent-insensitive Vietnamese search: "hai chien" matches "hải chiến". */
export function normalizeSearch(value: string) {
  return value
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export const PROFILE_OPTIONS = [
  { value: "landscape_1080p", label: "Ngang 16:9", size: "1920×1080", aspect: "16 / 9" },
  { value: "portrait_1080x1920", label: "Dọc 9:16", size: "1080×1920", aspect: "9 / 16" },
  { value: "square_1080", label: "Vuông 1:1", size: "1080×1080", aspect: "1 / 1" },
  { value: "portrait_4x5", label: "Dọc 4:5", size: "1080×1350", aspect: "4 / 5" },
] as const;

export function profileMeta(id: string | null | undefined) {
  return PROFILE_OPTIONS.find((option) => option.value === id) || PROFILE_OPTIONS[0];
}

export function profileAspect(id: string | null | undefined) {
  return profileMeta(id).aspect;
}

export function effectName(effect: string) {
  const names: Record<string, string> = {
    rain: "Mưa",
    snow: "Tuyết",
    water: "Nước",
    steam: "Khói",
    wind: "Gió",
    birds: "Chim",
    fire: "Lửa",
    embers: "Tàn lửa",
    sunrays: "Tia nắng",
    fog: "Sương mù",
    light: "Nhịp sáng",
  };
  return names[effect] || effect;
}
