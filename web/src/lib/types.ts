import type { TimedLine } from "@/lib/cue";

export type Phase = "home" | "setup" | "align" | "edit" | "export";

export type Outputs = {
  directory: string;
  vocal: string;
  instrumental: string;
  timing?: string;
  project?: string;
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
  output_dir: string;
  cover_duration: number;
  image_duration: number;
  language: string;
  model_name: string;
  device: string;
  lines: TimedLine[];
  outputs: Outputs | null;
  path: string;
};

export type Job = {
  id: string;
  status: "running" | "editing" | "rendering" | "done" | "error";
  log: string[];
  error: string;
  outputs: Outputs | null;
  lines: TimedLine[] | null;
  duration: number;
  export_gaps?: string[];
  project_path?: string;
  project?: Project | null;
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
  cover_image: string;
  output_dir: string;
  cover_duration: string;
  image_duration: string;
  model_name: string;
  device: string;
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
    cover_image: "",
    output_dir: outputDir,
    cover_duration: "5",
    image_duration: "8",
    model_name: "small",
    device: "cpu",
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
    cover_image: project.cover_image || "",
    output_dir: project.output_dir || "",
    cover_duration: String(project.cover_duration ?? 5),
    image_duration: String(project.image_duration ?? 8),
    model_name: project.model_name || "small",
    device: project.device || "cpu",
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
    cover_image: fields.cover_image,
    output_dir: fields.output_dir,
    cover_duration: Number(fields.cover_duration),
    image_duration: Number(fields.image_duration),
    language: "vi",
    model_name: fields.model_name,
    device: fields.device,
    project_path: fields.project_path,
  };
}
