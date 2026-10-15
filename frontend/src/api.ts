// API 客户端：所有请求都走这里。
// 好处：token 管理、错误解析、统一异常只写一遍，组件里不用重复。

import type {
  Drama,
  Episode,
  EpisodeDetail,
  GenerationTask,
  SynopsisRead,
  TokenResponse,
  User,
} from './types'

const TOKEN_KEY = 'ai_drama_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null): void {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

/** 后端统一错误结构 {"error": {"code","message"}} 的映射 */
export class ApiError extends Error {
  status: number
  code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  let resp: Response
  try {
    resp = await fetch(path, { ...options, headers })
  } catch (cause) {
    // fetch 只在"请求根本没发出去 / 一个字节都没收到"时才抛异常：
    // 后端没启动、端口不对、被代理或防火墙拦截、断网……
    //
    // 关键认知：**这不是后端返回的错误**，所以后端日志里不会有任何记录。
    // 浏览器原生只会说一句 "Failed to fetch"，非常容易让人误以为是后端崩了 ——
    // 所以这里翻译成人话，并把原始错误打到控制台供深入排查。
    console.error(`[网络错误] ${options.method ?? 'GET'} ${path}`, cause)
    throw new ApiError(
      0,
      'network_error',
      `无法连接后端服务（${options.method ?? 'GET'} ${path}）：请确认后端已启动、端口正确，然后重试`,
    )
  }

  const text = await resp.text()
  const body: unknown = text ? JSON.parse(text) : null

  if (!resp.ok) {
    const err = (body as { error?: { code?: string; message?: string } } | null)?.error
    throw new ApiError(resp.status, err?.code ?? 'unknown', err?.message ?? `请求失败（HTTP ${resp.status}）`)
  }
  return body as T
}

export const api = {
  register: (username: string, password: string) =>
    request<User>('/api/v1/auth/register', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  // 登录比较特殊：后端按 OAuth2 规范收表单（application/x-www-form-urlencoded）
  login: async (username: string, password: string): Promise<TokenResponse> => {
    const resp = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ username, password }),
    })
    const body = (await resp.json()) as TokenResponse | { error?: { code?: string; message?: string } }
    if (!resp.ok) {
      const err = (body as { error?: { code?: string; message?: string } }).error
      throw new ApiError(resp.status, err?.code ?? 'unknown', err?.message ?? '登录失败')
    }
    return body as TokenResponse
  },

  me: () => request<User>('/api/v1/auth/me'),

  listDramas: () => request<Drama[]>('/api/v1/dramas'),
  createDrama: (title: string, genre: string, logline: string, style: string) =>
    request<Drama>('/api/v1/dramas', {
      method: 'POST',
      body: JSON.stringify({ title, genre, logline, style }),
    }),
  deleteDrama: (id: number) =>
    request<null>(`/api/v1/dramas/${id}`, { method: 'DELETE' }),

  listEpisodes: (dramaId: number) =>
    request<Episode[]>(`/api/v1/dramas/${dramaId}/episodes`),
  createEpisode: (dramaId: number, title: string, synopsis: string) =>
    request<Episode>(`/api/v1/dramas/${dramaId}/episodes`, {
      method: 'POST',
      body: JSON.stringify({ title, synopsis }),
    }),

  /** 修改本集标题/大纲（用户自己写的内容走这里保存） */
  updateEpisode: (episodeId: number, payload: { title?: string; synopsis?: string }) =>
    request<EpisodeDetail>(`/api/v1/episodes/${episodeId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),

  /**
   * 让 AI 起草大纲。只返回建议、不写库，用户确认后调 updateEpisode 保存。
   * after_number 留空 = 接在现有所有剧集之后（新建一集时用）。
   */
  generateSynopsis: (dramaId: number, payload: { title: string; after_number?: number }) =>
    request<SynopsisRead>(`/api/v1/dramas/${dramaId}/synopsis/generate`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getEpisode: (id: number) => request<EpisodeDetail>(`/api/v1/episodes/${id}`),
  generate: (episodeId: number, shotCount: number, extraHint: string) =>
    request<GenerationTask>(`/api/v1/episodes/${episodeId}/generate`, {
      method: 'POST',
      body: JSON.stringify({ shot_count: shotCount, extra_hint: extraHint }),
    }),
  getTask: (id: number) => request<GenerationTask>(`/api/v1/tasks/${id}`),

  /** 为这一集的镜头批量生成画面（文生图）。返回任务，前端轮询进度。 */
  generateImages: (episodeId: number, onlyMissing = true) =>
    request<GenerationTask>(`/api/v1/episodes/${episodeId}/images`, {
      method: 'POST',
      body: JSON.stringify({ only_missing: onlyMissing }),
    }),

  /** 镜头画面的地址（需要带 Authorization 头，所以要用 AuthMedia 组件取） */
  shotImagePath: (shotId: number) => `/api/v1/shots/${shotId}/image`,

  /** 为这一集的台词批量生成配音（TTS）。返回任务，前端轮询进度。 */
  generateAudio: (episodeId: number, onlyMissing = true) =>
    request<GenerationTask>(`/api/v1/episodes/${episodeId}/audio`, {
      method: 'POST',
      body: JSON.stringify({ only_missing: onlyMissing }),
    }),

  /** 镜头配音的地址（同样需要带 Authorization 头） */
  shotAudioPath: (shotId: number) => `/api/v1/shots/${shotId}/audio`,

  /**
   * 为每个镜头的画面生成动态片段（图生视频）。返回任务。
   * 不做这一步的话，成片就是"每张图定格 + 配音"的幻灯片 —— 画面完全静止。
   */
  generateClips: (episodeId: number, onlyMissing = true) =>
    request<GenerationTask>(`/api/v1/episodes/${episodeId}/clips`, {
      method: 'POST',
      body: JSON.stringify({ only_missing: onlyMissing }),
    }),

  /** 镜头动态片段的地址（预览用，同样需要带 Authorization 头） */
  shotClipPath: (shotId: number) => `/api/v1/shots/${shotId}/clip`,

  /** 把这一集的画面 + 配音合成成竖屏 MP4（本地 ffmpeg）。返回任务。 */
  generateVideo: (episodeId: number) =>
    request<GenerationTask>(`/api/v1/episodes/${episodeId}/video`, { method: 'POST' }),

  /** 这一集成片的地址（需要带 Authorization 头） */
  episodeVideoPath: (episodeId: number) => `/api/v1/episodes/${episodeId}/video`,
}
