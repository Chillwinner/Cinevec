export type Source = {
  id: string;
  name: string;
  endpoint: string;
  bucket: string;
  region: string;
  role: "input" | "storage" | "both";
  is_platform_storage: boolean;
  access_key_configured: boolean;
  secret_configured: boolean;
  last_probe_status: string;
  last_probe_message: string;
  created_at: string;
  updated_at: string;
};

export type Asset = {
  id: string;
  source_id: string;
  object_key: string;
  kind: string;
  vectorize: boolean;
  status: string;
  lifecycle?: string;
  lifecycle_label?: string;
  index_state?: string;
  version?: number;
  ready_version?: number;
  excluded?: boolean;
  source_deleted?: boolean;
  vector_purge_status?: string;
  vector_purge_message?: string | null;
  artifact_purge_status?: string;
  artifact_purge_message?: string | null;
  fully_deleted?: boolean;
  s3_original_untouched?: boolean;
  can_restore?: boolean;
  origin?: string;
  store_original?: boolean;
  display_name?: string;
  description?: string;
  cover_key?: string | null;
  external_ref?: Record<string, unknown> | null;
  source_url?: string | null;
  tags?: string[];
  cover_locator?: string | null;
  video_cover_locator?: string | null;
  cover_index?: number | null;
  cover_status?: "ready" | "pending" | "none";
  created_at: string;
  updated_at: string;
};

export type AssetListResponse = {
  items: Asset[];
  total: number;
  limit: number;
  offset: number;
};

export type Job = {
  id: string;
  job_type: string;
  status: "waiting" | "running" | "paused" | "blocked" | "completed" | "failed";
  source_id: string | null;
  asset_id: string | null;
  stage: string;
  step: string;
  progress: number;
  queue_position: number | null;
  error_code: string | null;
  error_message: string | null;
  checkpoint: Record<string, unknown>;
  payload: Record<string, unknown>;
  created_at: string | null;
  updated_at: string | null;
};

export type ObjectEntry = {
  key: string;
  is_dir: boolean;
  size: number | null;
  last_modified: string | null;
};

export type DepStatus = {
  status: string;
  message: string;
  secret_configured?: boolean;
};

export type ResourceBlock = {
  available: boolean;
  status: string;
  message: string;
  rss_bytes?: number | null;
  cpu_percent?: number | null;
  memory_budget_bytes?: number | null;
  within_budget?: boolean | null;
  total_bytes?: number | null;
  used_bytes?: number | null;
  logical_cpus?: number | null;
  loadavg?: number[] | null;
  devices?: { name?: string | null; utilization_percent?: number | null; memory_used_mib?: number | null; memory_total_mib?: number | null }[];
};

export type SystemStatus = {
  sqlite: DepStatus;
  minio_s3: DepStatus;
  file_store?: DepStatus;
  ffmpeg?: DepStatus;
  milvus: DepStatus & { uri_configured?: boolean; token_configured?: boolean; inference_ready?: boolean };
  embedding_model: DepStatus & { inference_ready?: boolean; path_configured?: boolean };
  subtitle_encoder?: DepStatus & { inference_ready?: boolean; path_configured?: boolean };
  auth: DepStatus;
  sample_interval_seconds: number;
  model?: ModelSettingsView;
  resources?: {
    pica_process: ResourceBlock;
    host: { cpu: ResourceBlock; memory: ResourceBlock };
    gpu: ResourceBlock;
    external_model: ResourceBlock;
  };
};

export type SampleSettings = {
  mode: "interval_seconds" | "uniform_total" | "frames_per_minute";
  interval_seconds: number;
  uniform_total: number;
  frames_per_minute: number;
  black_max_mean: number;
  white_min_mean: number;
  blur_max_laplacian_var: number;
  similar_max_mad: number;
  preview_max_edge: number;
  preview_quality: number;
  model_input_size: number;
};

export type SearchSettings = {
  top_n: number;
  max_previews_per_video: number;
  counting_mode: "asset" | "video";
  fusion: "visual_first" | "rrf";
  subtitle_agg: "max" | "sum";
  rrf_k: number;
  score_threshold: number | null;
  oversample_factor: number;
  max_scan: number;
  metric: string;
};

export type ModelCatalogEntry = {
  id: string;
  family: string;
  label: string;
  native_dim: number;
  preprocess_version: string;
  contract_status: string;
  task_prefix_applied_by: string;
  active: boolean;
  verified: boolean;
  http_paths_unverified: boolean;
  notes: string;
  save_is_not_health: boolean;
};

export type ModelSettingsView = {
  catalog: ModelCatalogEntry[];
  active_family: string;
  active_space: {
    profile: string;
    family: string;
    model_id: string;
    model_version: string;
    dim: number;
    native_dim?: number | null;
    preprocess_version: string;
    metric: string;
  } | null;
  collections?: { frame: string; subtitle: string } | null;
  encoder_backend: string;
  http_base_url_configured: boolean;
  http_base_url: string;
  contract_status: string | null;
  task_prefix_applied_by: string | null;
  save_is_not_health: boolean;
  auto_activated: boolean;
  note: string;
};

export type AppSettings = {
  sample: SampleSettings;
  subtitle_merge?: { max_gap_seconds: number; max_group_duration_seconds: number; max_group_chars: number };
  refresh?: RefreshSettings;
  lifecycle?: { preview_cleanup_on_exclude: boolean };
  search?: SearchSettings;
  upload?: { max_upload_files: number };
  search_notes?: Record<string, unknown>;
  provisional_note: string;
  undecided_note?: string;
  ffmpeg: DepStatus & { path?: string | null };
  file_store: DepStatus & { kind?: string };
  model?: ModelSettingsView;
};

export type SearchHit = {
  asset_id: string;
  kind: string;
  object_key: string;
  lifecycle: string;
  lifecycle_label: string;
  visibility: string;
  score: number;
  metric: string;
  score_meaning: string;
  hit_source: string;
  previews: { frame_index: number; time_seconds: number | null; preview_key?: string | null; locator: string; score: number | null; near_subtitle?: boolean }[];
  subtitle_excerpt?: string | null;
  time_seconds?: number | null;
  hit_method?: string | null;
  source_available?: boolean | null;
  source_availability?: string | null;
  index_version?: number | null;
  model_id?: string | null;
  model_version?: string | null;
  dim?: number | null;
  preprocess_version?: string | null;
  collection?: string | null;
  origin?: string;
  store_original?: boolean;
  display_name?: string;
  description?: string;
  cover_key?: string | null;
  video_cover_locator?: string | null;
  external_ref?: Record<string, unknown> | null;
  source_url?: string | null;
  tags?: string[];
  subtitle_hits?: { unit_index: number; time_seconds: number | null; score: number; excerpt?: string | null }[];
};

export type LexicalHit = Asset & {
  matched_on?: string[];
  filename?: string;
  tags?: string[];
};

export type JobCard = {
  id: string;
  job_type: string;
  status: Job["status"];
  stage: string;
  step: string;
  progress: number;
  queue_position: number | null;
  asset_id: string | null;
  object_key?: string | null;
  error_message?: string | null;
  elapsed_seconds?: number | null;
  timings?: Record<string, {
    seconds?: number;
    started_at?: string;
    ended_at?: string;
    bytes?: number;
    width?: number;
    height?: number;
    source_width?: number;
    source_height?: number;
    format?: string;
    strategy?: string;
    request_bytes?: number;
    ready_seconds?: number;
    http_seconds?: number;
    queue_seconds?: number;
    preprocess_seconds?: number;
    inference_seconds?: number;
    remote_split?: boolean;
    processed_width?: number;
    processed_height?: number;
    processed_source?: string;
    received_width?: number;
    received_height?: number;
    device?: string;
  }>;
  can_cancel?: boolean;
  can_retry?: boolean;
  created_at?: string | null;
};

export type DashboardStage = {
  id: string;
  label: string;
  counts: Record<string, number>;
  total: number;
  jobs?: JobCard[];
};

export type DashboardResponse = {
  stages: DashboardStage[];
  selected?: { id: string; label?: string; jobs: Record<string, unknown>[] } | null;
  resources?: {
    pica_process: ResourceBlock;
    host: { cpu: ResourceBlock; memory: ResourceBlock };
    gpu: ResourceBlock;
    external_model?: ResourceBlock;
  };
  notes: string[];
};

export type SearchResponse = {
  items: SearchHit[];
  metric: string;
  score_meaning: string;
  reranker: boolean;
  settings: SearchSettings;
  notes: string[];
  connections: Record<string, DepStatus & { inference_ready?: boolean; token_configured?: boolean; uri_configured?: boolean }>;
};

export type FrameRecord = {
  asset_id: string;
  job_id: string;
  index: number;
  time_seconds: number;
  kept: boolean;
  drop_reason: string | null;
  preview_key: string | null;
  model_input_key: string | null;
  extra: Record<string, unknown>;
};

export type ApiError = {
  error: { code: string; message: string };
};

export type Association = {
  id: string;
  video_asset_id: string;
  subtitle_asset_id: string;
  created_at: string;
  video_key?: string | null;
  subtitle_key?: string | null;
  cue_count?: number;
  group_count?: number;
  cues?: { cue_index: number; start_seconds: number; end_seconds: number; text: string }[];
  groups?: { group_index: number; start_seconds: number; end_seconds: number; text: string; cue_indexes: number[] }[];
  merge_note?: string;
};

export type RefreshSettings = {
  enabled: boolean;
  interval_minutes: number;
  last_enqueued_at: string | null;
  last_completed_at: string | null;
  last_result: string | null;
  next_due_at: string | null;
};

export type AuthPublicStatus = {
  enabled: boolean;
  setup_required: boolean;
  user_configured: boolean;
  username: string | null;
  secret_configured: boolean;
  preset_available: boolean;
  preset_pending_verification: boolean;
  signing_key_source: string;
  token_version: number;
  expire_seconds: number;
  message: string;
  status?: string;
};
