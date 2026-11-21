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
};

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
  log: string[];
  error: string;
  outputs: Outputs | null;
  lines: TimedLine[] | null;
  duration: number;
  export_gaps?: string[];
  project_path?: string;
  project?: Project | null;
  restore?: RestoreSettings | null;
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
