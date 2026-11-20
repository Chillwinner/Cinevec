import { authorizationHeader, clearToken, readToken } from "./authStorage";
import type {
  AppSettings,
  Asset,
  AssetFacesResponse,
  AssetListResponse,
  Association,
  AuthPublicStatus,
  DashboardResponse,
  FaceSearchResponse,
  FrameRecord,
  Job,
  ObjectEntry,
  PersonAssetsResponse,
  PersonSummary,
  SearchResponse,
  Source,
  SystemStatus,
} from "./types";

export function authorizedMediaUrl(path: string) {
  if (!path) return path;
  const stored = readToken();
  if (!stored?.token) return path;
  const join = path.includes("?") ? "&" : "?";
  return `${path}${join}access_token=${encodeURIComponent(stored.token)}`;
}

export class ApiError extends Error {
  code?: string;
  status?: number;
  constructor(message: string, code?: string, status?: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...authorizationHeader(readToken()),
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  const response = await fetch(path, { ...init, headers });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    if (response.status === 401) {
      clearToken();
      onUnauthorized?.();
    }
    const message = data?.error?.message || `请求失败（${response.status}）`;
    throw new ApiError(message, data?.error?.code, response.status);
  }
  return data as T;
}

async function postFormData<T>(path: string, body: FormData): Promise<T> {
  const headers = { ...authorizationHeader(readToken()) };
  const response = await fetch(path, { method: "POST", headers, body });
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    if (response.status === 401) {
      clearToken();
      onUnauthorized?.();
    }
    throw new ApiError(data?.error?.message || `请求失败（${response.status}）`, data?.error?.code, response.status);
  }
  return data as T;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  status: () => request<SystemStatus>("/api/status"),
  sources: () => request<{ items: Source[] }>("/api/sources"),
  createSource: (body: Record<string, unknown>) =>
    request<Source>("/api/sources", { method: "POST", body: JSON.stringify(body) }),
  updateSource: (id: string, body: Record<string, unknown>) =>
    request<Source>(`/api/sources/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteSource: (id: string) => request<{ ok: boolean }>(`/api/sources/${id}`, { method: "DELETE" }),
  probeSource: (id: string) => request<Source>(`/api/sources/${id}/probe`, { method: "POST" }),
  browse: (id: string, prefix = "") =>
    request<{ prefix: string; items: ObjectEntry[] }>(
      `/api/sources/${id}/browse?prefix=${encodeURIComponent(prefix)}`,
    ),
  imports: (sourceId: string, items: { object_key: string; vectorize: boolean; subtitle_object_key?: string }[]) =>
    request<{ assets: Asset[]; jobs: Job[] }>("/api/imports", {
      method: "POST",
      body: JSON.stringify({ source_id: sourceId, items }),
    }),
  uploadFiles: async (
    files: File[],
    meta: {
      relative_path: string;
      vectorize: boolean;
      subtitle_relative_path?: string;
      cover_relative_path?: string;
      is_cover?: boolean;
      store_original?: boolean;
      source_url?: string;
      description?: string;
    }[],
    onProgress?: (loaded: number, total: number) => void,
    cover?: File | null,
  ) => {
    const body = new FormData();
    body.append("meta", JSON.stringify(meta));
    files.forEach((file) => {
      body.append("files", file);
      body.append("relative_paths", (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name);
    });
    if (cover) {
      body.append("cover", cover, cover.name || "cover.jpg");
    }
    const headers = { ...authorizationHeader(readToken()) };
    const data = await new Promise<{ assets: Asset[]; jobs: Job[]; prefix: string; batch_id: string; message: string; origin: string }>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/uploads");
      Object.entries(headers).forEach(([key, value]) => xhr.setRequestHeader(key, value));
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress?.(event.loaded, event.total);
      };
      xhr.onerror = () => reject(new ApiError("上传失败"));
      xhr.onload = () => {
        const parsed = xhr.responseText ? JSON.parse(xhr.responseText) : {};
        if (xhr.status < 200 || xhr.status >= 300) {
          if (xhr.status === 401) {
            clearToken();
            onUnauthorized?.();
          }
          reject(new ApiError(parsed?.error?.message || `请求失败（${xhr.status}）`, parsed?.error?.code, xhr.status));
          return;
        }
        resolve(parsed);
      };
      xhr.send(body);
    });
    return data;
  },
  lexicalSearch: (query: string, sourceId?: string) =>
    request<{ kind: string; notes: string[]; embedding_used: boolean; items: Asset[] }>("/api/search/lexical", {
      method: "POST",
      body: JSON.stringify({ query, source_id: sourceId ?? null }),
    }),
  dashboard: (stage?: string) =>
    request<DashboardResponse>(stage ? `/api/jobs/dashboard?stage=${encodeURIComponent(stage)}` : "/api/jobs/dashboard"),
  addTag: (assetId: string, tag: string) =>
    request<{ items: string[] }>(`/api/assets/${assetId}/tags`, { method: "POST", body: JSON.stringify({ tag }) }),
  removeTag: (assetId: string, tag: string) =>
    request<{ items: string[] }>(`/api/assets/${assetId}/tags/${encodeURIComponent(tag)}`, { method: "DELETE" }),
  updateAsset: (
    assetId: string,
    body: { description?: string; display_name?: string; source_url?: string; tags?: string[] },
  ) => request<Asset>(`/api/assets/${assetId}`, { method: "PATCH", body: JSON.stringify(body) }),
  uploadCover: async (assetId: string, file: File) => {
    const body = new FormData();
    body.append("cover", file, file.name || "cover.jpg");
    return postFormData<Asset>(`/api/assets/${assetId}/cover`, body);
  },
  enqueueVectorize: (assetId: string) =>
    request<{ asset: Asset; job: Job }>(`/api/assets/${assetId}/vectorize`, { method: "POST" }),
  retryJob: (id: string) => request<Job>(`/api/jobs/${id}/retry`, { method: "POST" }),
  assets: (opts?: { sourceId?: string; kind?: string; excluded?: boolean; pending?: boolean; indexed?: boolean; q?: string; limit?: number; offset?: number }) => {
    const params = new URLSearchParams();
    if (opts?.sourceId) params.set("source_id", opts.sourceId);
    if (opts?.kind) params.set("kind", opts.kind);
    if (opts?.excluded != null) params.set("excluded", String(opts.excluded));
    if (opts?.pending != null) params.set("pending", String(opts.pending));
    if (opts?.indexed != null) params.set("indexed", String(opts.indexed));
    if (opts?.q) params.set("q", opts.q);
    if (opts?.limit != null) params.set("limit", String(opts.limit));
    if (opts?.offset != null) params.set("offset", String(opts.offset));
    const query = params.toString();
    return request<AssetListResponse>(query ? `/api/assets?${query}` : "/api/assets");
  },
  objectKeys: (sourceId?: string) =>
    request<{ items: string[] }>(sourceId ? `/api/assets/object-keys?source_id=${encodeURIComponent(sourceId)}` : "/api/assets/object-keys"),
  batchAssets: (ids: string[], action: "tag" | "exclude" | "vectorize", tag?: string) =>
    request<{ ok: boolean; items: Record<string, unknown>[] }>("/api/assets/batch", {
      method: "POST",
      body: JSON.stringify({ ids, action, tag: tag ?? null }),
    }),
  rebuildPlan: () => request<{ model_mismatch_count: number; rebuildable_count: number; already_ready_count: number }>("/api/rebuild/plan"),
  rebuildQueue: () => request<{ queued_count: number; job_count?: number }>("/api/rebuild/queue", { method: "POST", body: JSON.stringify({ execute: true }) }),
  jobs: (stage?: string) =>
    request<{ items: Job[] }>(stage ? `/api/jobs?stage=${encodeURIComponent(stage)}` : "/api/jobs"),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  pauseJob: (id: string) => request<Job>(`/api/jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: string) => request<Job>(`/api/jobs/${id}/resume`, { method: "POST" }),
  cancelJob: (id: string) => request<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  deleteJob: (id: string) => request<{ ok: boolean }>(`/api/jobs/${id}`, { method: "DELETE" }),
  search: (query: string, extra?: Record<string, unknown>) =>
    request<SearchResponse>("/api/search", { method: "POST", body: JSON.stringify({ query, has_image: false, ...(extra ?? {}) }) }),
  searchImage: async (image: File, extra?: { query?: string; top_n?: number; score_threshold?: number | null }) => {
    const body = new FormData();
    body.append("image", image, image.name || "query.jpg");
    if (extra?.query) body.append("query", extra.query);
    if (extra?.top_n != null) body.append("top_n", String(extra.top_n));
    if (extra?.score_threshold != null) body.append("score_threshold", String(extra.score_threshold));
    return postFormData<SearchResponse>("/api/search/image", body);
  },
  searchFace: async (image: File, extra?: { face_index?: number | null; top_n?: number; score_threshold?: number | null }) => {
    const body = new FormData();
    body.append("image", image, image.name || "face.jpg");
    if (extra?.face_index != null) body.append("face_index", String(extra.face_index));
    if (extra?.top_n != null) body.append("top_n", String(extra.top_n));
    if (extra?.score_threshold != null) body.append("score_threshold", String(extra.score_threshold));
    return postFormData<FaceSearchResponse>("/api/search/face", body);
  },
  people: (opts?: { includeHidden?: boolean; q?: string; limit?: number; offset?: number }) => {
    const params = new URLSearchParams();
    if (opts?.includeHidden) params.set("include_hidden", "true");
    if (opts?.q) params.set("q", opts.q);
    if (opts?.limit != null) params.set("limit", String(opts.limit));
    if (opts?.offset != null) params.set("offset", String(opts.offset));
    const query = params.toString();
    return request<{ items: PersonSummary[]; total: number; limit: number; offset: number }>(query ? `/api/people?${query}` : "/api/people");
  },
  person: (personId: string) => request<PersonSummary>(`/api/people/${personId}`),
  personAssets: (personId: string, opts?: { limit?: number; offset?: number }) => {
    const params = new URLSearchParams();
    if (opts?.limit != null) params.set("limit", String(opts.limit));
    if (opts?.offset != null) params.set("offset", String(opts.offset));
    const query = params.toString();
    return request<PersonAssetsResponse>(query ? `/api/people/${personId}/assets?${query}` : `/api/people/${personId}/assets`);
  },
  updatePerson: (personId: string, body: { name?: string | null; hidden?: boolean; cover_face_id?: string }) =>
    request<PersonSummary>(`/api/people/${personId}`, { method: "PATCH", body: JSON.stringify(body) }),
  mergePeople: (targetId: string, sourceIds: string[]) =>
    request<{ person: PersonSummary; merged_faces: number }>("/api/people/merge", {
      method: "POST",
      body: JSON.stringify({ target_id: targetId, source_ids: sourceIds }),
    }),
  reassignFace: (faceId: string, body: { target_person_id?: string | null; new_person_name?: string | null }) =>
    request<{ face_id: string; person: PersonSummary }>(`/api/faces/${faceId}/reassign`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  assetFaces: (assetId: string) => request<AssetFacesResponse>(`/api/assets/${assetId}/faces`),
  backfillFaces: (force = false) =>
    request<Record<string, unknown>>("/api/faces/backfill", { method: "POST", body: JSON.stringify({ force }) }),
  reclusterFaces: (threshold?: number) =>
    request<Record<string, unknown>>("/api/faces/recluster", {
      method: "POST",
      body: JSON.stringify(threshold != null ? { threshold } : {}),
    }),
  getSettings: () => request<AppSettings>("/api/settings"),
  putSettings: (body: Record<string, unknown>) =>
    request<AppSettings>("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  frames: (assetId: string) => request<{ items: FrameRecord[] }>(`/api/assets/${assetId}/frames`),
  associations: (videoId?: string) =>
    request<{ items: Association[] }>(videoId ? `/api/subtitles/associations?video_asset_id=${videoId}` : "/api/subtitles/associations"),
  association: (id: string) => request<Association>(`/api/subtitles/associations/${id}`),
  associateSubtitle: (video_asset_id: string, subtitle_asset_id: string) =>
    request<{ association: Association; job: Job | null }>("/api/subtitles/associations", {
      method: "POST",
      body: JSON.stringify({ video_asset_id, subtitle_asset_id, enqueue: true }),
    }),
  deleteAssociation: (id: string) => request<{ ok: boolean }>(`/api/subtitles/associations/${id}`, { method: "DELETE" }),
  refresh: (sourceId: string, prefix = "") =>
    request<{ job: Job }>(`/api/refresh`, { method: "POST", body: JSON.stringify({ source_id: sourceId, prefix }) }),
  scans: (sourceId?: string) =>
    request<{ items: Record<string, unknown>[] }>(sourceId ? `/api/scans?source_id=${sourceId}` : "/api/scans"),
  excludeAsset: (id: string) => request<{ asset: Asset; job: Job; fully_deleted: boolean }>(`/api/assets/${id}/exclude`, { method: "POST" }),
  restoreAsset: (id: string) => request<{ asset: Asset }>(`/api/assets/${id}/restore`, { method: "POST" }),
  catalog: (query: string) =>
    request<{ kind: string; note: string; items: Asset[] }>("/api/catalog", { method: "POST", body: JSON.stringify({ query }) }),
  setPolicy: (sourceId: string, prefix: string, vectorize: boolean) =>
    request<{ ok: boolean }>("/api/policies", { method: "POST", body: JSON.stringify({ source_id: sourceId, prefix, vectorize }) }),
  policies: (sourceId: string) => request<{ items: { prefix: string; vectorize: number | boolean }[] }>(`/api/policies?source_id=${sourceId}`),
  authStatus: () => request<AuthPublicStatus>("/api/auth/status"),
  authMe: () => request<{ identity: { id: string; username: string; roles: string[] } | null; auth: AuthPublicStatus }>("/api/auth/me"),
  login: (username: string, password: string) =>
    request<{ token: string; token_type: string; expires_in: number; username: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  enableAuth: (body: Record<string, unknown>) =>
    request<{ ok: boolean; auth: AuthPublicStatus; token?: string; expires_in?: number; username?: string }>("/api/auth/enable", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  disableAuth: () => request<{ ok: boolean; auth: AuthPublicStatus }>("/api/auth/disable", { method: "POST" }),
  updateSigningKey: (body: Record<string, unknown>) =>
    request<{ ok: boolean; auth: AuthPublicStatus }>("/api/auth/signing-key", { method: "POST", body: JSON.stringify(body) }),
  setupAuth: (body: Record<string, unknown>) =>
    request<{ ok: boolean; auth: AuthPublicStatus }>("/api/auth/setup", { method: "POST", body: JSON.stringify(body) }),
  apiTokens: () =>
    request<{ items: { id: string; name: string; token_prefix: string; created_at: string; revoked_at: string | null }[] }>(
      "/api/settings/api-tokens",
    ),
  createApiToken: (name?: string) =>
    request<{
      id: string;
      name: string;
      token_prefix: string;
      created_at: string;
      token: string;
      message: string;
    }>("/api/settings/api-tokens", { method: "POST", body: JSON.stringify({ name: name || "plugin" }) }),
  revokeApiToken: (id: string) => request<{ ok: boolean }>(`/api/settings/api-tokens/${id}`, { method: "DELETE" }),
};



