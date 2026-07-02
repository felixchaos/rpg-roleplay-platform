/**
 * Typed API surface. Endpoint paths mirror the FastAPI backend exactly.
 * Note: tavern routes live at /api/tavern/* (NO /v1 prefix); chat/opening/me/* use /api/v1.
 */
import { http, baseUrl } from "./http";
import { streamPost, SseHandlers, SseController } from "./sse";

const V1 = "/api/v1";

export type User = {
  id: number;
  username: string;
  display_name?: string;
  role?: string;
};

export type TavernChat = {
  id: number;
  title: string | null;
  character_name: string;
  avatar_path: string;
  last_snippet: string;
  updated_at: string;
  archived_at: string | null;
};

export type ModelInfo = { id: string; name?: string; [k: string]: unknown };
export type ProviderInfo = {
  api_id: string;
  display_name?: string;
  has_credential?: boolean;
  enabled?: boolean;
  models?: ModelInfo[];
  [k: string]: unknown;
};

export type NpcCardRef = { name?: string; matched?: boolean; [k: string]: unknown };

export const auth = {
  me: () => http.get<{ ok: boolean; user: User }>(`${V1}/auth/me`),
  login: (username: string, password: string) =>
    http.post<{ ok: boolean; user: User }>(`${V1}/auth/login`, { username, password }),
  /** Trigger a password-reset email (always returns ok, anti-enumeration). Reset completes via web link. */
  forgotPassword: (email: string) => http.post<{ ok: boolean }>(`${V1}/auth/forgot-password`, { email }),
  /** Passwordless OTP: request a 6-digit code by email, then verify it to log in. */
  loginCodeRequest: (email: string) =>
    http.post<{ ok: boolean; pending_verify?: boolean; message?: string }>(`${V1}/auth/login-code/request`, { email }),
  loginCodeVerify: (email: string, code: string) =>
    http.post<{ ok: boolean; user?: User }>(`${V1}/auth/login-code/verify`, { email, code }),
  register: (body: Record<string, unknown>) =>
    http.post<{ ok: boolean; user?: User; pending_verify?: boolean; auto_verified?: boolean }>(
      `${V1}/auth/register`,
      body,
    ),
  logout: () => http.post(`${V1}/auth/logout`, {}),
  /** Login/register form schema; notes.turnstile_sitekey present when Turnstile is enforced. */
  schema: () =>
    http.get<{ login?: any[]; register?: any[]; notes?: { turnstile_sitekey?: string; invite_only?: boolean; min_password_length?: number } }>(
      `${V1}/auth/schema`,
    ),
  /** Exchange a desktop magic-link token for a session (used by QR login). */
  magicConsume: (magic_token: string, email: string) =>
    http.post<{ ok: boolean; username?: string; needs_profile?: boolean }>(`/api/auth/magic-consume`, {
      magic_token,
      email,
    }),
};

export const tavern = {
  list: () => http.get<{ ok: boolean; chats: TavernChat[] }>(`/api/tavern/chats`),
  listArchived: () =>
    http.get<{ ok: boolean; chats: TavernChat[] }>(`/api/tavern/chats`, { archived: 1 }),
  create: (body: { character_card_id?: number; persona_card_id?: number; title?: string }) =>
    http.post<{ ok: boolean; save: { id: number } }>(`/api/tavern/chats`, body),
  importCharacter: (form: FormData) =>
    http.postForm<{ ok: boolean; save_id: number; character_name: string }>(
      `/api/tavern/import-character`,
      form,
    ),
  /** Import a SillyTavern chat-history JSONL as a new tavern save. */
  importJsonl: (form: FormData) =>
    http.postForm<{ ok: boolean; save_id?: number; chat_id?: number; title?: string }>(
      `/api/tavern/chats/import-jsonl`,
      form,
    ),
  activate: (id: number) => http.post(`/api/tavern/chats/${id}/activate`, {}),
  archive: (id: number, archived: boolean) =>
    http.patch(`/api/tavern/chats/${id}/archive`, { archived }),
  rename: (id: number, title: string) => http.post(`/api/tavern/chats/${id}/rename`, { title }),
  setSystemPrompt: (id: number, system_prompt: string) =>
    http.post(`/api/tavern/chats/${id}/system-prompt`, { system_prompt }),
  bindCard: (id: number, role: "character" | "persona", card_id: number | null) =>
    http.post(`/api/tavern/chats/${id}/bind-card`, { role, card_id }),
  setImmersive: (id: number, enabled: boolean) =>
    http.post(`/api/tavern/chats/${id}/immersive`, { enabled }),
  aiReply: (id: number) => http.post<{ ok: boolean; text: string }>(`/api/tavern/chats/${id}/ai-reply`, {}),
  autotitle: (id: number) => http.post<{ ok: boolean; title?: string }>(`/api/tavern/chats/${id}/autotitle`, {}),
  remove: (id: number) => http.del(`/api/tavern/chats/${id}`, {}),
};

export const game = {
  /** Full current in-memory game state (player/world/history/tavern/...). */
  state: () => http.get<any>(`${V1}/state`),
  stop: () => http.post(`${V1}/stop`, {}),
  chat: (body: Record<string, unknown>, handlers: SseHandlers): Promise<SseController> =>
    streamPost(`${V1}/chat`, body, handlers),
  opening: (body: Record<string, unknown>, handlers: SseHandlers): Promise<SseController> =>
    streamPost(`${V1}/opening`, body, handlers),
  /** Edit a past turn's text in place. message_index is 0-based into state.history. */
  editMessage: (save_id: number, message_index: number, content: string) =>
    http.post<{ ok: boolean; state?: any }>(`${V1}/message/edit`, { save_id, message_index, content }),
  /** Token-budget breakdown for the current context window. */
  contextBreakdown: () =>
    http.get<{ ok: boolean; total_tokens?: number; ctx_limit?: number; breakdown?: any[] }>(
      `${V1}/chat/context-breakdown`,
    ),
  /** Answer (or dismiss) a pending GM question. choice is the chosen option text. */
  clearQuestion: (id: string | number | undefined, choice?: string, index?: number) =>
    http.post<{ ok: boolean; cleared?: boolean; state?: any }>(`${V1}/questions/clear`, { id, choice, index }),
};

export type MemoryBucket = "pinned" | "notes" | "facts" | "resources" | "abilities";

export const memory = {
  /** notes/facts/pinned/resources/abilities are player-editable; returns refreshed state. */
  setMode: (mode: string) => http.post<{ ok: boolean; state?: any }>(`${V1}/memory/mode`, { mode }),
  add: (bucket: MemoryBucket, text: string) =>
    http.post<{ ok: boolean; state?: any }>(`${V1}/memory/add`, { bucket, text }),
  update: (bucket: MemoryBucket, index: number, text: string) =>
    http.post<{ ok: boolean; state?: any }>(`${V1}/memory/update`, { bucket, index, text }),
  remove: (bucket: MemoryBucket, index: number) =>
    http.post<{ ok: boolean; state?: any }>(`${V1}/memory/remove`, { bucket, index }),
};

export const settings = {
  models: () => http.get<{ ok: boolean; models: { apis: ProviderInfo[] }; selected?: any }>(`${V1}/models`),
  selectModel: (api_id: string, model_id: string, save_id?: number) =>
    http.post(`${V1}/models/select`, { api_id, model_id, ...(save_id != null ? { save_id } : {}) }),
  credentials: () => http.get<{ ok: boolean; items: ProviderInfo[] }>(`${V1}/me/credentials`),
  setCredential: (body: { api_id: string; api_key: string; base_url_override?: string; enabled?: boolean }) =>
    http.post(`${V1}/me/credentials`, body),
  deleteCredential: (api_id: string) => http.post(`${V1}/me/credentials/delete`, { api_id }),
  testCredential: (api_id: string, model?: string) =>
    http.get<{ ok: boolean; latency_ms?: number; error?: string }>(`${V1}/me/credentials/test`, {
      api_id,
      ...(model ? { model } : {}),
    }),
  usage: (days = 30) => http.get<any>(`${V1}/me/usage`, { days }),
  /** Pull remote model list from the provider using the current credentials. */
  syncRemote: (api_id: string) =>
    http.post<{ ok: boolean; models?: any[]; error?: string }>(`/api/models/remote/sync`, { api_id }),
  /** Show {added, removed, kept} comparing remote list vs local stored list. */
  modelsDiff: (api_id: string) =>
    http.get<{ ok: boolean; added?: string[]; removed?: string[]; kept?: string[]; remote_only?: string[]; local_only?: string[]; matching?: string[] }>(`/api/models/diff`, { api_id }),
  /** Add (or upsert) a single model record. */
  upsertModel: (body: { api_id: string; real_name: string; display_name?: string; enabled?: boolean }) =>
    http.post<{ ok: boolean }>(`/api/models/model`, body),
  /** Delete a single model record. */
  deleteModel: (body: { api_id: string; model_id?: string; real_name?: string }) =>
    http.post<{ ok: boolean }>(`/api/models/model/delete`, body),
  /** Toggle a user's own visibility flag for a single (synced) model. */
  setModelVisibility: (body: { api_id: string; model: string; visible: boolean }) =>
    http.post<{ ok: boolean }>(`/api/me/models/visibility`, body),
  /** Refresh per-model connectivity status for all providers. */
  refreshHealthAll: () => http.post<{ ok: boolean }>(`/api/models/health/refresh-all`, {}),
  /** Per-provider usage stats. */
  usageByProvider: (days = 30) => http.get<{ ok: boolean; by_api?: Record<string, any> }>(`${V1}/me/usage`, { days, group: "api" }),
};

export const cards = {
  personas: () => http.get<{ ok: boolean; items: any[] }>(`${V1}/me/personas`),
  getPersona: (id: number) => http.get<{ ok: boolean; persona: any }>(`${V1}/me/personas/${id}`),
  upsertPersona: (body: Record<string, unknown>) => http.post<{ ok: boolean }>(`${V1}/me/personas`, body),
  removePersona: (id: number) => http.post(`${V1}/me/personas/${id}/delete`, {}),
  characterCards: (q?: string) =>
    http.get<{ ok: boolean; items: any[] }>(`${V1}/me/character-cards`, q ? { q } : undefined),
  getCharacter: (id: number) => http.get<{ ok: boolean; card: any }>(`${V1}/me/character-cards/${id}`),
  upsertCharacter: (body: Record<string, unknown>) =>
    http.post<{ ok: boolean; card?: any }>(`${V1}/me/character-cards`, body),
  deleteCharacter: (id: number) => http.post(`${V1}/me/character-cards/${id}/delete`, {}),
  uploadAvatar: (id: number, form: FormData) =>
    http.postForm<{ ok: boolean; url?: string }>(`${V1}/me/character-cards/${id}/avatar`, form),
  publicCards: (q?: string, limit = 30, offset = 0) =>
    http.get<{ ok: boolean; items: any[] }>(`${V1}/cards/public`, { q, limit, offset }),
  clonePublicCard: (cardId: number) => http.post<{ ok: boolean; card?: any }>(`${V1}/cards/public/${cardId}/clone`, {}),
};

export const gmStyle = {
  schema: () => http.get<{ ok: boolean; knobs: string[]; defaults: Record<string, number> }>(`${V1}/gm-style/schema`),
  get: () => http.get<{ ok: boolean; gm_style: Record<string, number> }>(`${V1}/me/gm-style`),
  set: (gm_style: Record<string, number>) => http.post<{ ok: boolean }>(`${V1}/me/gm-style`, { gm_style }),
};

export const saveSettings = {
  get: (saveId: number) => http.get<{ ok: boolean; settings?: any }>(`${V1}/saves/${saveId}/settings`),
  patch: (saveId: number, updates: Record<string, unknown>, is_create = false) =>
    http.patch<{ ok: boolean; settings?: any }>(`${V1}/saves/${saveId}/settings`, { updates, is_create }),
};

export type ScriptSummary = {
  id: number;
  title: string;
  author?: string;
  chapter_count?: number;
  word_count?: number;
  cover_url?: string;
  [k: string]: unknown;
};

export type SaveSummary = {
  id: number;
  title: string;
  turn?: number;
  player_name?: string;
  world_time?: string;
  history_count?: number;
  updated_at?: string;
  [k: string]: unknown;
};

export type ChapterSummary = {
  id: number;
  chapter_index: number;
  title?: string;
  volume_title?: string;
  word_count?: number;
  preview?: string;
};

export type WorldbookEntry = {
  id?: number;
  name?: string;
  keys?: string[];
  content?: string;
  comment?: string;
  [k: string]: unknown;
};

export const scripts = {
  // Backend returns {ok, items, page}; /api/scripts ignores scope (owned ∪ subscribed).
  list: (_scope?: "mine" | "subscribed" | "public", _q?: string) =>
    http.get<{ ok: boolean; items: ScriptSummary[] }>(`${V1}/scripts`),
  get: (id: number) => http.get<{ ok: boolean; script: any }>(`${V1}/scripts/${id}`),
  birthpoints: (id: number) => http.get<{ ok: boolean; birthpoints: any[] }>(`${V1}/scripts/${id}/birthpoints`),
  chapters: (id: number, q?: string, limit = 200) =>
    http.get<{ ok: boolean; items?: ChapterSummary[]; chapters?: ChapterSummary[] }>(`${V1}/scripts/${id}/chapters`, { q, limit }),
  chapterDetail: (id: number, index: number) =>
    http.get<{ ok: boolean; chapter: ChapterSummary & { content?: string } }>(`${V1}/scripts/${id}/chapters/${index}`),
  worldbook: (id: number) =>
    http.get<{ ok: boolean; items?: WorldbookEntry[]; entries?: WorldbookEntry[] }>(`${V1}/scripts/${id}/worldbook`, { fetch_all: true }),
  /** Owned script → delete; subscribed → unsubscribe. */
  remove: (id: number, subscribed = false) =>
    subscribed
      ? http.post<{ ok: boolean }>(`${V1}/scripts/${id}/unsubscribe`, {})
      : http.post<{ ok: boolean }>(`${V1}/scripts/${id}/delete`, {}),
  updateChapter: (id: number, index: number, body: { title?: string; content?: string; volume_title?: string }) =>
    http.post<{ ok: boolean }>(`${V1}/scripts/${id}/chapters/${index}`, body),
  worldbookCreate: (id: number, body: WorldbookEntry) =>
    http.post<{ ok: boolean; entry?: WorldbookEntry }>(`${V1}/scripts/${id}/worldbook`, body),
  worldbookUpdate: (id: number, entryId: number, body: WorldbookEntry) =>
    http.put<{ ok: boolean }>(`${V1}/scripts/${id}/worldbook/${entryId}`, body),
  worldbookDelete: (id: number, entryId: number) =>
    http.del<{ ok: boolean }>(`${V1}/scripts/${id}/worldbook/${entryId}`, {}),
  /** AI 复核人名/语义: dedupe NPC cards, lock 主角, drop non-name cards. */
  auditCards: (id: number, api_id: string, model: string) =>
    http.post<{ ok: boolean; code?: string; error?: string; merged?: number; removed?: number; protagonist?: string }>(
      `${V1}/scripts/${id}/audit-cards`,
      { api_id, model },
      120000,
    ),
  /** Settings audit (设定核对): extraction quality flags + canon entities + worldlines. */
  audit: (id: number) =>
    http.get<{
      ok: boolean;
      script: { id: number; title: string; review_status: string; reviewed_at: string | null };
      entities: any[];
      worldlines: any[];
      nodes: any[];
      timeline: any[];
      review_flags: { needs_review?: boolean; author_notes?: any[]; weird_titles?: any[]; gaps?: any[]; cleaning?: Record<string, unknown> };
    }>(`${V1}/scripts/${id}/graph`),
  patchCanon: (id: number, body: Record<string, unknown>) =>
    http.patch<{ ok: boolean; error?: string }>(`${V1}/scripts/${id}/canon`, body),
  markReviewed: (id: number, reviewed: boolean) =>
    http.post<{ ok: boolean }>(`${V1}/scripts/${id}/mark-reviewed`, { reviewed }),
  /** All canon entities for this script — characters/locations/factions/items/concepts. */
  canonList: (id: number, limit = 200) =>
    http.get<{ ok: boolean; items?: any[]; page?: { next_cursor?: string | null } }>(
      `${V1}/scripts/${id}/canon-entities`,
      { limit },
    ),
  /** Timeline anchors for this script (chapter-min/max + story-time label). */
  timeline: (id: number) =>
    http.get<{ ok: boolean; items?: any[]; phases?: any[]; current_chapter?: number }>(
      `${V1}/scripts/${id}/timeline`,
    ),
};

export const saves = {
  list: () => http.get<{ ok: boolean; items: SaveSummary[] }>(`${V1}/saves`),
  get: (id: number) => http.get<{ ok: boolean; save: any }>(`${V1}/saves/${id}`),
  /** Create a new game save. Reuses /api/new (no /v1) — script/persona priority resolved server-side. */
  newGame: (body: Record<string, unknown>) => http.post<{ ok: boolean; state: any }>(`/api/new`, body, 60000),
  /** Switch the runtime to this save before chatting (so /api/v1/state reflects it). */
  activate: (id: number) => http.post<{ ok: boolean; active_save_id?: number }>(`${V1}/saves/${id}/activate`, {}),
  rename: (id: number, title: string) => http.post(`${V1}/saves/${id}/rename`, { title }),
  remove: (id: number) => http.post(`${V1}/saves/${id}/delete`, {}),
  activateBranch: (node_id: number) => http.post(`${V1}/branches/activate`, { node_id }),
  /** Expected anchors + actual phase footprints for this save's timeline panel. */
  timeline: (id: number) =>
    http.get<{ ok: boolean; script_anchors?: any[]; save_phases?: any[]; current_phase_index?: number; current_chapter?: number }>(
      `${V1}/saves/${id}/timeline`,
    ),
  /** Worldline-convergence anchor state: drift summary, per-phase pressure, pending/occurred. */
  anchors: (id: number) =>
    http.get<{ ok: boolean; summary?: any; by_phase?: any[]; recent_pending?: any[]; recent_occurred?: any[] }>(
      `${V1}/saves/${id}/anchors`,
    ),
  /** Player-driven advance: mark a non-fatal, in-window pending anchor as reached. */
  satisfyAnchor: (id: number, anchorKey: string) =>
    http.post<{ ok: boolean; error?: string }>(`${V1}/saves/${id}/anchors/${encodeURIComponent(anchorKey)}/satisfy`, {}),
  /** Human-readable TXT export (game / tavern 通用, 皆 game_saves 行). */
  exportTxtPath: (id: number) => `/api/saves/${id}/export/txt`,
};

export const tavernExport = {
  /** Absolute URL for a chat's JSONL export (downloaded with cookie auth by the caller). */
  jsonlPath: (chatId: number) => `/api/tavern/chats/${chatId}/export-jsonl`,
  /** Human-readable TXT export — same save row, readable as a novel. */
  txtPath: (chatId: number) => `/api/saves/${chatId}/export/txt`,
};

export type BranchNode = {
  id: number;
  commit_id: number;
  node_id: number;
  parent_id?: number | null;
  turn?: number;
  player_input?: string;
  gm_output?: string;
  summary?: string;
  content_preview?: string;
  ref_names?: string[];
  is_active?: boolean;
  created_at?: string;
  [k: string]: unknown;
};

export const branches = {
  /** Full commit tree for a save, newest-first, with the active node flagged. */
  list: (saveId: number) =>
    http.get<{ ok: boolean; nodes?: BranchNode[]; commits?: BranchNode[]; active_commit_id?: number }>(
      `${V1}/branches/${saveId}`,
    ),
  /** Fork a new branch from a historical commit. */
  continueFrom: (node_id: number) =>
    http.post<{ ok: boolean; save_id?: number; new_branch_node_id?: number }>(`${V1}/branches/continue`, { node_id }),
  /** Jump the active head to a commit within the same save. */
  activate: (node_id: number) =>
    http.post<{ ok: boolean; save_id?: number }>(`${V1}/branches/activate`, { node_id }),
  /** Soft-rollback: delete a turn and everything after it. */
  rollback: (save_id: number, message_index: number) =>
    http.post(`${V1}/branches/rollback`, { save_id, message_index }),
};

export type ProfileStats = {
  total_rounds?: number;
  branch_nodes?: number;
  branches?: number;
  max_branch_depth?: number;
  saves_count?: number;
  login_streak?: number;
  longest_login_streak?: number;
  imported?: { scripts?: number; words?: number; chapters?: number };
  [k: string]: unknown;
};

export type Achievement = {
  id: string;
  title?: string;
  name?: string;
  description?: string;
  unlocked?: boolean;
  unlocked_at?: string | null;
  hidden?: boolean;
  icon?: string;
  [k: string]: unknown;
};

export const profile = {
  get: () => http.get<{ ok: boolean; user: User; profile?: any; stats?: any; usage_30d?: any; preferences?: any }>(`${V1}/me/profile`),
  patch: (body: Record<string, unknown>) => http.patch(`${V1}/me/profile`, body),
  stats: () => http.get<{ ok: boolean } & ProfileStats>(`${V1}/me/stats`),
  achievements: () => http.get<{ ok: boolean; items: Achievement[] }>(`${V1}/me/achievements`),
  achievementsCatalog: () => http.get<{ ok: boolean; items: Achievement[] }>(`/api/achievements`),
  usageTimeline: (days = 30, group_by: "day" | "model" = "day") =>
    http.get<{ ok: boolean; series?: any[]; daily_breakdown?: any[]; total_tokens?: number; total_cost_usd?: number }>(
      `${V1}/me/usage/timeline`,
      { days, group_by },
    ),
};

export type GenImage = {
  id: number;
  status: "pending" | "running" | "done" | "failed" | "cancelled" | string;
  url?: string;
  error?: string;
  kind?: string;
  prompt?: string;
  created_at?: string;
};

export const images = {
  /** Enqueue an async image job; returns immediately with {image_id, status}. */
  generate: (body: { prompt: string; kind?: string; save_id?: number | string; size?: string; attach?: any }) =>
    http.post<{ ok?: boolean; image_id?: number; status?: string; code?: string }>(`/api/images/generate`, body, 60000),
  /** Poll one image's status/url. */
  get: (id: number) => http.get<GenImage>(`/api/images/${id}`),
  /** All images attached to a save, newest-first. */
  list: (saveId: number | string) => http.get<GenImage[] | { ok: boolean; items: GenImage[] }>(`/api/images/list`, { save_id: saveId }),
  cancel: (id: number) => http.post(`/api/images/${id}/cancel`, {}),
};

export const world = {
  /** Allowlisted scalar world keys: time·weather·phase·location·atmosphere·season·region·calendar. */
  set: (key: string, value: string) => http.post<{ ok: boolean; state?: any }>(`${V1}/world/set`, { key, value }),
};

export const relationships = {
  set: (character: string, status: string) =>
    http.post<{ ok: boolean; state?: any }>(`${V1}/relationships/set`, { character, status }),
  remove: (character: string) =>
    http.post<{ ok: boolean; state?: any }>(`${V1}/relationships/delete`, { character }),
};

export const worldline = {
  list: () => http.get<{ ok: boolean; variables?: any }>(`${V1}/worldline/variables`),
  set: (key: string, value: string) => http.post<{ ok: boolean; state?: any }>(`${V1}/worldline/variable`, { key, value }),
  remove: (key: string) => http.post<{ ok: boolean; state?: any }>(`${V1}/worldline/variable/remove`, { key }),
};

export const compliance = {
  splashStatus: () =>
    http.get<{ ok: boolean; current_version: string; acked: boolean; acked_at: string | null }>(`${V1}/me/splash/status`),
  splashAck: (splash_version: string) => http.post<{ ok: boolean }>(`${V1}/me/splash/ack`, { splash_version }),
  /** free_text + a 64-char SHA256 consent_token (hash of the consent statement). */
  submitFeedback: (body: { free_text: string; consent_token: string; app_version: string; contact_email?: string; excerpts?: any[] }) =>
    http.post<{ ok?: boolean; error_key?: string; message?: string }>(`/api/feedback`, { excerpts: [], ...body }),
  policyNotices: () => http.get<{ ok: boolean; notices?: any[] }>(`${V1}/policy/notices`),
};

export const prefs = {
  /** Read current preferences (folded into the profile payload). */
  get: () => http.get<{ ok: boolean; preferences?: Record<string, any> }>(`${V1}/me/profile`),
  /** Merge-patch preference keys (e.g. ui_language, serif, autosave, extractor.enabled). */
  set: (preferences: Record<string, unknown>, replace = false) =>
    http.post<{ ok: boolean; preferences?: Record<string, any> }>(`${V1}/me/preference`, { preferences, replace }),
};

export const account = {
  saveProfile: (body: { display_name?: string; username?: string }) => http.post<{ ok: boolean }>(`${V1}/profile`, body),
  deactivate: () => http.post<{ ok: boolean }>(`${V1}/account/deactivate`, {}),
  requestDelete: () => http.post<{ ok: boolean }>(`/api/account/request-delete`, {}),
  cancelDelete: () => http.post<{ ok: boolean }>(`/api/account/cancel-delete`, {}),
  deleteStatus: () => http.get<{ ok: boolean; pending?: boolean; purge_at?: string | null }>(`/api/account/delete-status`),
  exportUrl: async () => (await baseUrl()) + `${V1}/me/account/export`,
};

export type LibraryAsset = {
  id: string | number;
  kind?: string;
  url?: string;
  source?: string;
  size?: number;
  created_at?: string;
  [k: string]: unknown;
};

export const library = {
  list: (kind?: string) => http.get<{ ok: boolean; items?: LibraryAsset[] }>(`/api/library`, kind ? { kind } : undefined),
  get: (id: string | number) => http.get<{ ok: boolean; asset?: LibraryAsset }>(`/api/library/asset/${encodeURIComponent(String(id))}`),
  downloadUrl: async (id: string | number) => (await baseUrl()) + `/api/library/asset/${encodeURIComponent(String(id))}/download`,
  remove: (id: string | number) => http.post<{ ok: boolean }>(`/api/library/asset/${encodeURIComponent(String(id))}/delete`, { confirm: true }),
};

export const apparatus = {
  tools: () => http.get<{ ok: boolean; tools?: any[] }>(`${V1}/tools`),
  skills: () => http.get<{ ok: boolean; skills?: any[] }>(`${V1}/skills`),
  plugins: () => http.get<{ ok: boolean; plugins?: any[] }>(`${V1}/plugins`),
  mcpRuntime: () => http.get<{ ok: boolean; servers?: any[] }>(`${V1}/mcp/runtime`),
  mcpTools: () => http.get<{ ok: boolean; tools?: any[] }>(`${V1}/mcp/tools`),
  /** Add or update an MCP server. command for stdio servers, url for remote.
   *  Extra fields are passed through (server schema is extra='allow'). */
  upsertMcp: (body: {
    id?: string;
    name: string;
    transport?: "stdio" | "http";
    command?: string;
    url?: string;
    args?: string[];
    env?: Record<string, string>;
    headers?: Record<string, string>;
    cwd?: string;
    enabled?: boolean;
    [k: string]: any;
  }) => http.post<{ ok: boolean; mcp?: any }>(`${V1}/mcp/server`, body),
  setMcpEnabled: (id: string, enabled: boolean) =>
    http.post<{ ok: boolean }>(`${V1}/mcp/server/enabled`, { id, enabled }),
  deleteMcp: (id: string) => http.post<{ ok: boolean }>(`${V1}/mcp/server/delete`, { id }),
  startMcp: (id: string) => http.post<{ ok: boolean }>(`${V1}/mcp/server/start`, { id }),
  stopMcp: (id: string) => http.post<{ ok: boolean }>(`${V1}/mcp/server/stop`, { id }),
  /** Run handshake + tools/list against the server and return reachability. */
  validateMcp: (id: string) => http.post<{ ok: boolean; result?: any; error?: string }>(`${V1}/mcp/server/validate`, { id }),
  runSkill: (skill_id: string, body?: Record<string, unknown>) =>
    http.post<{ ok: boolean; result?: any }>(`${V1}/skills/${encodeURIComponent(skill_id)}/run`, body ?? {}),
  importSkill: (form: FormData) => http.postForm<{ ok: boolean }>(`${V1}/skills/import`, form),
};

/**
 * Persona Skills — markdown-based character distillation. Different from executable skills:
 * each entry is a roleplay character produced by feeding a .md profile (or a public GitHub
 * repo) to an LLM that turns it into a regular character card.
 */
export const personaSkills = {
  list: () => http.get<{ ok: boolean; items?: any[] }>(`/api/me/persona-skills`),
  /** Body: { source: "github" | "upload", repo_url?, files?: [{name, content}], generate_image?, use_llm? } */
  import: (body: Record<string, unknown>) =>
    http.post<{ ok: boolean; card?: any; image_status?: string; error?: string }>(
      `/api/me/persona-skills/import`,
      body,
      120000,
    ),
  remove: (skill_id: string | number) =>
    http.post<{ ok: boolean }>(`/api/me/persona-skills/${encodeURIComponent(String(skill_id))}/delete`, {}),
};

export const permissions = {
  /** Set the LLM state-write permission mode: readonly / default / auto / full_access. */
  setMode: (mode: string) => http.post<{ ok: boolean; state?: any }>(`${V1}/permissions`, { mode }),
};

export type ActiveTask = {
  job_id?: string | number;
  kind?: string;
  status?: string;
  cancel_requested?: boolean;
  progress?: number;
  title?: string;
  label?: string;
  [k: string]: unknown;
};

export const tasks = {
  /** Current user's in-flight + recently-finished background jobs (import/rebuild/imagegen). */
  active: () => http.get<{ ok?: boolean; tasks?: ActiveTask[]; items?: ActiveTask[] }>(`${V1}/me/tasks/active`),
};

// ── The editor's AI familiar (console_assistant) ─────────────────────────────
// A separate agent from the GM: it has tools that read & write the script's
// knowledge base directly (chapters, worldbook, canon, timeline). Streams
// `token` / `error` / `done` events; SSE base path lives outside `/api/v1`.
export type AssistantConversation = {
  id: string;
  title?: string;
  updated_at?: string;
  message_count?: number;
};

export type AssistantMessage = {
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
};

export const consoleAssistant = {
  list: () => http.get<{ ok: boolean; conversations?: AssistantConversation[]; items?: AssistantConversation[] }>(`/api/console_assistant/conversations`),
  messages: (conversationId: string) =>
    http.get<{ ok: boolean; messages?: AssistantMessage[]; items?: AssistantMessage[] }>(
      `/api/console_assistant/conversations/${encodeURIComponent(conversationId)}/messages`,
    ),
  newConversation: (page_context?: Record<string, unknown>) =>
    http.post<{ ok: boolean; conversation_id?: string; id?: string }>(`/api/console_assistant/new_conversation`, page_context ?? {}),
  deleteConversation: (conversation_id: string) =>
    http.post<{ ok: boolean }>(`/api/console_assistant/delete_conversation`, { conversation_id }),
  /** Stream the familiar's response. SSE: token{text} · error{message} · done{}. */
  chat: (body: { message: string; conversation_id?: string; page_context?: Record<string, unknown> }, handlers: SseHandlers) =>
    streamPost(`/api/console_assistant/chat`, body, handlers),
};

export const rules = {
  scene: () => http.get<{ ok: boolean; scene?: any }>(`${V1}/rules/scene`),
  move: (to: string) => http.post<{ ok: boolean; state?: any }>(`${V1}/rules/move`, { to }),
  action: (body: Record<string, unknown>) => http.post<{ ok: boolean; state?: any }>(`${V1}/rules/action`, body),
  encounterStart: (encounter_id: string, seed?: number) =>
    http.post<{ ok: boolean; state?: any }>(`${V1}/rules/encounter/start`, { encounter_id, seed }),
  encounterNext: () => http.post<{ ok: boolean; state?: any }>(`${V1}/rules/encounter/next`, {}),
  encounterEnemy: (attacker_id: string, target_id = "player", seed?: number) =>
    http.post<{ ok: boolean; state?: any }>(`${V1}/rules/encounter/enemy`, { attacker_id, target_id, seed }),
};

export const api = { auth, tavern, game, settings, cards, scripts, saves, branches, profile, memory, gmStyle, saveSettings, images, world, relationships, worldline, compliance, prefs, account, library, apparatus, permissions, rules };
