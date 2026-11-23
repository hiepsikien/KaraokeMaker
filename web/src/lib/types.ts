import type { TimedLine } from "@/lib/cue";

export type Phase = "home" | "setup" | "restore" | "align" | "edit" | "export";

export type RestoreSettings = {
  instrumental_stem: string;
  high_hz: number;
  low_hz: number;
  high_volume: number;
  low_volume: number;
};

export function sungCount(lyrics: string) {
  return lyrics
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !(line.startsWith("[") && line.endsWith("]"))).length;
}

export type Outputs = {
  directory: string;
  vocal: string;
  instrumental: string;
  timing?: string;
  project?: string;
};

export type DirectorPoint = {
  x: number;
  y: number;
};

export type DirectorBox = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

export type DirectorAnchors = {
  pivot?: DirectorPoint | null;
  opening?: DirectorPoint | null;
};

export type DirectorObject = {
  id: string;
  label: string;
  /** Unique UI label (`flag_1`, `flag_2`); falls back to `label` when absent. */
  display_label?: string;
  /** Optional group shared by objects that move together (e.g. `tree_1`). */
  group_id?: string;
  box: DirectorBox;
  anchors: DirectorAnchors;
  affordances: string[];
  confidence: number;
  source?: string;
};

export type DirectorRegion = {
  id: string;
  polygon: DirectorPoint[];
};

export type DirectorProtect = {
  label: string;
  box: DirectorBox;
};

export type DirectorFitTransform = {
  mode: string;
  source_width: number;
  source_height: number;
  frame_width: number;
  frame_height: number;
  scale: number;
  offset_x: number;
  offset_y: number;
};

export type EffectPlanItem = {
  effect: string;
  target_ids: string[];
  params: Record<string, unknown>;
};

export type EffectPlan = {
  version: number;
  image_hash: string;
  profile_id: string;
  fit_transform: DirectorFitTransform;
  regions?: DirectorRegion[];
  protect?: DirectorProtect[];
  /** Objects are part of the compiled plan; keep their boxes editable. */
  objects?: DirectorObject[];
  /** Optional global filter applied when the plan is rendered. */
  selected_object_ids?: string[];
  items: EffectPlanItem[];
};

export type EffectConsentReceipt = {
  timestamp: string;
  image_hash: string;
  provider: string;
  model: string;
};

export type BackgroundEffect = {
  image_path: string;
  image_hash: string;
  effect_prompt: string;
  effect_consent_receipt: EffectConsentReceipt;
  selected_object_ids: string[];
  effect_plan: EffectPlan;
  model: string;
  created: string;
  asset_id: string | null;
};

export type DirectorIntentEffect = {
  type: string;
  intensity?: number;
  audio?: boolean;
  [key: string]: unknown;
};

export type DirectorIntent = {
  effects: DirectorIntentEffect[];
};

export type DirectorUsage = {
  prompt?: number;
  output?: number;
  thoughts?: number;
  total?: number;
};

export type DirectorAnalysis = {
  image: string;
  image_hash: string;
  width: number;
  height: number;
  prompt?: string;
  model: string;
  scene?: string;
  objects: DirectorObject[];
  regions?: DirectorRegion[];
  protect?: DirectorProtect[];
  intent?: DirectorIntent | null;
  effect_plan: EffectPlan;
  usage?: DirectorUsage | null;
  cached?: boolean;
  intent_fallback?: boolean;
};

export type StudioAsset = {
  id: string;
  name: string;
  source_image: string;
  image_hash: string;
  effect_plan?: EffectPlan;
  plan_hash?: string;
  asset_version?: number;
  profile_id?: string;
  profile_fingerprint?: string;
  width?: number;
  height?: number;
  fps?: number;
  fit_mode?: string;
  strip_rect?: number[];
  renderer_version?: number;
  loop_duration?: number;
  asset_frames?: number;
  clip_file?: string;
  clip_size?: number;
  clip_sha256?: string;
  created?: string;
};

export type BackgroundRender = {
  schema_version?: number;
  builder_version?: number;
  hash: string;
  profile_id?: string;
  profile_fingerprint?: string;
  source_output_dir?: string;
  width?: number;
  height?: number;
  fps?: number;
  total_frames?: number;
  cover_frames?: number;
  song_frames?: number;
  outro_frames?: number;
  image_frames?: number;
  overlay_hash?: string;
  overlay_frames?: number;
  overlay_degraded?: boolean;
  degraded?: boolean;
  created?: string;
  segments?: unknown[];
};

export type OverlayKind = "snow" | "leaf" | "bokeh" | "lightleak";

export type OverlayEffect = {
  id: string;
  kind: OverlayKind | string;
  enabled: boolean;
  params: Record<string, number | string>;
  mask: "none" | "avoid_lyrics";
  seed: number;
};

export type WaveformPlan = {
  enabled: boolean;
  source: string;
  style: string;
  anchor: string;
  /** Center of the strip, 0–1 of the frame. */
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
  accent: string;
  opacity: number;
  background: boolean;
  mode: string;
};

export type CoverStyle = {
  layout: string;
  label: string;
  show_label: boolean;
  title_size: number | null;
  artist_size: number | null;
  line_size: number | null;
  uppercase: boolean;
  text_color: string;
  line_color: string;
  accent_color: string;
  shadow: boolean;
  stroke: number;
  bg_blur: number;
  bg_darken: number;
  veil: number;
  vignette: number;
  scrim: string;
  scrim_opacity: number;
};

export type InterludeStyle = {
  enabled: boolean;
  min_gap: number;
  mode: string;
  text: string;
  show_next_line: boolean;
  fade: number;
};

export type OutroStyle = {
  enabled: boolean;
  duration: number;
  image: string;
  title: string;
  subtext: string;
  handle: string;
  show_title: boolean;
  show_artist: boolean;
  fade: number;
};

export type Presentation = {
  cover: CoverStyle;
  interlude: InterludeStyle;
  outro: OutroStyle;
};

export type Typography = {
  lyric_size: number | null;
  lyric_color: string;
  sung_color: string;
  bar_color: string;
  bar_opacity: number;
  shadow: boolean;
  stroke: number;
  row_gap: number | null;
};

export const DEFAULT_COVER: CoverStyle = {
  layout: "center",
  label: "",
  show_label: false,
  title_size: null,
  artist_size: null,
  line_size: null,
  uppercase: false,
  text_color: "#fff4dc",
  line_color: "#e8e0d2",
  accent_color: "#e7b15a",
  shadow: true,
  stroke: 0,
  bg_blur: 0,
  bg_darken: 58,
  veil: 70,
  vignette: 0,
  scrim: "none",
  scrim_opacity: 0,
};

export const DEFAULT_INTERLUDE: InterludeStyle = {
  enabled: false,
  min_gap: 8,
  mode: "strip",
  text: "Nhạc dạo",
  show_next_line: false,
  fade: 0.4,
};

export const DEFAULT_OUTRO: OutroStyle = {
  enabled: false,
  duration: 4,
  image: "",
  title: "",
  subtext: "Cảm ơn đã xem",
  handle: "",
  show_title: true,
  show_artist: true,
  fade: 0.6,
};

export const DEFAULT_WAVEFORM: WaveformPlan = {
  enabled: false,
  source: "instrumental",
  style: "bars",
  anchor: "center",
  x: 0.5,
  y: 0.70,
  width: 0.32,
  height: 0.06,
  color: "#ffc448",
  accent: "#ff7a59",
  opacity: 0.9,
  background: true,
  mode: "progress",
};

export const DEFAULT_TYPOGRAPHY: Typography = {
  lyric_size: null,
  lyric_color: "#fff8ec",
  sung_color: "#ffc448",
  bar_color: "#080604",
  bar_opacity: 196,
  shadow: true,
  stroke: 0,
  row_gap: null,
};

export const OVERLAY_DEFAULTS: Record<OverlayKind, Record<string, number | string>> = {
  snow: { intensity: 55, density: 100, speed: 100, size: 100 },
  leaf: { intensity: 55, density: 100, speed: 100, size: 100, palette: "autumn" },
  bokeh: {
    intensity: 45,
    density: 100,
    size: 100,
    speed: 100,
    opacity: 35,
    blend: "screen",
    palette: "auto",
  },
  lightleak: {
    intensity: 35,
    speed: 100,
    angle: 35,
    opacity: 30,
    blend: "screen",
    palette: "warm",
  },
};

export function defaultPresentation(): Presentation {
  return {
    cover: { ...DEFAULT_COVER },
    interlude: { ...DEFAULT_INTERLUDE },
    outro: { ...DEFAULT_OUTRO },
  };
}

export function defaultWaveform(): WaveformPlan {
  return { ...DEFAULT_WAVEFORM };
}

export function defaultTypography(): Typography {
  return { ...DEFAULT_TYPOGRAPHY };
}

/** Deep-merge partial project JSON with the known defaults. */
export function mergePresentation(value: Partial<Presentation> | null | undefined): Presentation {
  const base = defaultPresentation();
  if (!value || typeof value !== "object") return base;
  return {
    cover: { ...base.cover, ...(value.cover || {}) },
    interlude: { ...base.interlude, ...(value.interlude || {}) },
    outro: { ...base.outro, ...(value.outro || {}) },
  };
}

export function mergeWaveform(value: Partial<WaveformPlan> | null | undefined): WaveformPlan {
  if (!value || typeof value !== "object") return defaultWaveform();
  return { ...DEFAULT_WAVEFORM, ...value };
}

export function mergeTypography(value: Partial<Typography> | null | undefined): Typography {
  if (!value || typeof value !== "object") return defaultTypography();
  return { ...DEFAULT_TYPOGRAPHY, ...value };
}

export function overlayEffect(kind: OverlayKind, existing?: OverlayEffect): OverlayEffect {
  if (existing) {
    return {
      ...existing,
      params: { ...OVERLAY_DEFAULTS[kind], ...existing.params },
    };
  }
  return {
    id: `ovl_${kind}_${Date.now().toString(36)}`,
    kind,
    enabled: true,
    params: { ...OVERLAY_DEFAULTS[kind] },
    mask: "avoid_lyrics",
    seed: Math.floor(Math.random() * 1_000_000),
  };
}

export type Project = {
  version: number;
  status: "draft" | "aligned" | "exported";
  title: string;
  artist: string;
  cover_line: string;
  lyrics: string;
  original_audio: string;
  vocal_audio: string;
  instrumental_audio: string;
  cover_image: string;
  background_images: string[];
  background_effects: BackgroundEffect[];
  overlay_effects: OverlayEffect[];
  waveform: WaveformPlan;
  presentation: Presentation;
  typography: Typography;
  background_render?: BackgroundRender | null;
  output_profile: string;
  output_dir: string;
  cover_duration: number;
  image_duration: number;
  language: string;
  model_name: string;
  device: string;
  no_speech_threshold?: number | null;
  whisper_retry?: boolean;
  lines: TimedLine[];
  outputs: Outputs | null;
  restore?: RestoreSettings | null;
  path: string;
};

export type Job = {
  id: string;
  status: "running" | "editing" | "rendering" | "done" | "error" | "needs_split" | "separating" | "ready";
  created?: number;
  started?: number;
  log: string[];
  error: string;
  outputs: Outputs | null;
  lines: TimedLine[] | null;
  duration: number;
  connectionLost?: boolean;
  export_gaps?: string[];
  render_percent?: number;
  project_path?: string;
  project?: Project | null;
  restore?: RestoreSettings | null;
  background?: {
    state: "missing" | "stale" | "ready" | "building" | "error";
    percent?: number;
    reason?: string;
    hash?: string;
    path?: string;
    total_frames?: number;
    cover_frames?: number;
    song_frames?: number;
    image_frames?: number;
    outro_frames?: number;
    overlay_hash?: string;
    profile_id?: string;
  } | null;
};

export type ProjectFields = {
  title: string;
  artist: string;
  cover_line: string;
  original_audio: string;
  vocal_audio: string;
  instrumental_audio: string;
  lyrics: string;
  background_images: string[];
  background_effects: BackgroundEffect[];
  overlay_effects: OverlayEffect[];
  waveform: WaveformPlan;
  presentation: Presentation;
  typography: Typography;
  output_profile: string;
  cover_image: string;
  output_dir: string;
  cover_duration: string;
  image_duration: string;
  model_name: string;
  device: string;
  no_speech_threshold: string;
  whisper_retry: boolean;
  project_path: string;
};

export function emptyFields(outputDir = ""): ProjectFields {
  return {
    title: "",
    artist: "",
    cover_line: "",
    original_audio: "",
    vocal_audio: "",
    instrumental_audio: "",
    lyrics: "",
    background_images: [],
    background_effects: [],
    overlay_effects: [],
    waveform: defaultWaveform(),
    presentation: defaultPresentation(),
    typography: defaultTypography(),
    output_profile: "landscape_1080p",
    cover_image: "",
    output_dir: outputDir,
    cover_duration: "5",
    image_duration: "8",
    model_name: "small",
    device: "cpu",
    no_speech_threshold: "",
    whisper_retry: true,
    project_path: "",
  };
}

export function fieldsFromProject(project: Project): ProjectFields {
  return {
    title: project.title || "",
    artist: project.artist || "",
    cover_line: project.cover_line || "",
    original_audio: project.original_audio || "",
    vocal_audio: project.vocal_audio || "",
    instrumental_audio: project.instrumental_audio || "",
    lyrics: project.lyrics || "",
    background_images: project.background_images || [],
    background_effects: Array.isArray(project.background_effects) ? project.background_effects : [],
    overlay_effects: Array.isArray(project.overlay_effects) ? project.overlay_effects : [],
    waveform: mergeWaveform(project.waveform),
    presentation: mergePresentation(project.presentation),
    typography: mergeTypography(project.typography),
    output_profile: project.output_profile || "landscape_1080p",
    cover_image: project.cover_image || "",
    output_dir: project.output_dir || "",
    cover_duration: String(project.cover_duration ?? 5),
    image_duration: String(project.image_duration ?? 8),
    model_name: project.model_name || "small",
    device: project.device || "cpu",
    no_speech_threshold:
      project.no_speech_threshold == null ? "" : String(project.no_speech_threshold),
    whisper_retry: project.whisper_retry !== false,
    project_path: project.path || "",
  };
}

export function fieldsPayload(fields: ProjectFields) {
  return {
    title: fields.title,
    artist: fields.artist,
    cover_line: fields.cover_line,
    original_audio: fields.original_audio,
    vocal_audio: fields.vocal_audio,
    instrumental_audio: fields.instrumental_audio,
    lyrics: fields.lyrics,
    background_images: fields.background_images,
    background_effects: fields.background_effects,
    overlay_effects: fields.overlay_effects || [],
    waveform: mergeWaveform(fields.waveform),
    presentation: mergePresentation(fields.presentation),
    typography: mergeTypography(fields.typography),
    output_profile: fields.output_profile,
    cover_image: fields.cover_image,
    output_dir: fields.output_dir,
    cover_duration: Number(fields.cover_duration),
    image_duration: Number(fields.image_duration),
    language: "vi",
    model_name: fields.model_name,
    device: fields.device,
    no_speech_threshold: fields.no_speech_threshold.trim() === "" ? null : Number(fields.no_speech_threshold),
    whisper_retry: fields.whisper_retry,
    project_path: fields.project_path,
  };
}
