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
  /** 合成好的成片（通过 GET /episodes/{id}/video 播放） */
  video_path: string | null
  /** 合成失败的原因 */
  video_error: string | null
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
  /** 有值表示画面已生成（前端据此决定显示图片还是"未生成"） */
  image_path: string | null
  /** 画面生成失败的原因 */
  image_error: string | null
  /** 有值表示配音已生成（没有台词的镜头会一直是 null） */
  audio_path: string | null
  /** 配音生成失败的原因 */
  audio_error: string | null
  /** 有值表示这个镜头有**会动**的动态片段（图生视频）；为 null 时成片用静态图 */
  clip_path: string | null
  /** 动态片段生成失败的原因 */
  clip_error: string | null
}

export interface EpisodeDetail extends Episode {
  shots: Shot[]
}

/**
 * 角色卡：一张卡同时服务两件事。
 * - 配音关心：gender / age_group / voice / pitch_shift / speed
 * - 画面关心：gender / age / appearance / occupation / look_prompt
 */
export interface Character {
  id: number
  drama_id: number
  name: string
  gender: string
  age_group: string
  voice: string
  pitch_shift: number
  speed: number
  personality: string
  notes: string
  // ── 角色卡（画角色/画镜头用）──
  /** 具体年龄；为空时用 age_group 兜底 */
  age: number | null
  /** 外貌：发型/脸型/体型/常穿的衣服 —— 决定"同一个角色在不同镜头里长得像" */
  appearance: string
  occupation: string
  background: string
  /** 锁定的外貌提示词：留空 = 自动从上面几个字段生成；填了 = 以它为准 */
  look_prompt: string
  /** 有值表示三视图已生成，通过 GET /characters/{id}/turnaround 读取 */
  turnaround_path: string | null
  turnaround_error: string | null
}

export interface VoiceOption {
  voice: string
  label: string
  /** 实测基频：男声约 85~155Hz，女声约 165~265Hz */
  pitch_hz: number | null
}

export interface GenerationTask {
  id: number
  /** 集级任务才有；剧级任务（角色三视图）为 null */
  episode_id: number | null
  /** 剧级任务（角色三视图）才有 */
  drama_id: number | null
  kind: 'shots' | 'images' | 'clips' | 'audio' | 'video' | 'cast' | 'turnaround'
  status: 'pending' | 'running' | 'succeeded' | 'failed'
  provider: string
  shot_count: number
  total: number
  done: number
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
