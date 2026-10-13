// 与后端接口一一对应的类型定义。
// 后端由 Pydantic Schema 保证契约；前端用 TS 类型保证调用方不出错。

export interface User {
  id: number
  username: string
  email: string | null
  created_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  user: User
}

export interface Drama {
  id: number
  title: string
  genre: string
  logline: string | null
  style: string
  status: string
  created_at: string
  updated_at: string
}

export interface Episode {
  id: number
  drama_id: number
  number: number
  title: string
  synopsis: string
  status: string
  created_at: string
  updated_at: string
}

export interface Shot {
  id: number
  number: number
  shot_type: string
  camera: string
  description: string
  dialogue: string
  duration_sec: number
  image_prompt: string
  audio_prompt: string
}

export interface EpisodeDetail extends Episode {
  shots: Shot[]
}

export interface GenerationTask {
  id: number
  episode_id: number
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  provider: string
  shot_count: number
  error: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

/** AI 起草大纲的返回：只给建议，不写库 —— 用户确认后再 PATCH 保存 */
export interface SynopsisRead {
  synopsis: string
  used_context_episodes: number[]
}
