import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import type { DramaSettings, ModalityPayload, ModalitySettings } from './types'

/**
 * 短剧设置面板：改名 + 每个模态用自己的模型和 API Key。
 *
 * **这个面板存在的意义（比功能本身重要）**：
 * 改之前所有人共用服务端 `.env` 里的 Key，等于"用户花站长的钱"。
 * 现在每个短剧可以填自己的 Key —— 用自己的额度、自己的模型。
 *
 * **三个设计决定，都写在界面上**：
 * ① 留空 = 用服务端默认（方便试玩）。服务端把 `ALLOW_SERVER_KEY_FALLBACK`
 *    设成 false 时会变成"必须填"，界面据此提示。
 * ② API Key **填了就不会再显示**：后端加密存库、接口永不回显，
 *    界面只显示掩码（`sk-abc…9f2`）让你认出"填的是哪一把"。
 * ③ 地址必须是 https 且不能指向内网 —— 否则等于让服务器替你去访问任意地址（SSRF）。
 */

type Kind = 'llm' | 'image' | 'tts' | 'video'

const KINDS: { key: Kind; title: string; hint: string; extras: ('size' | 'voice' | 'send_duration')[] }[] = [
  { key: 'llm', title: '文本模型', hint: '生成分镜 / 大纲 / 角色表 / 情绪分析', extras: [] },
  { key: 'image', title: '文生图模型', hint: '角色三视图 + 每个镜头的画面', extras: ['size'] },
  { key: 'tts', title: '配音模型', hint: '逐句台词合成（多角色 + 情绪语速）', extras: ['voice'] },
  { key: 'video', title: '图生视频模型', hint: '把静图变成会动的片段（可选）', extras: ['size', 'send_duration'] },
]

type Draft = {
  title: string
  genre: string
  logline: string
  style: string
  kinds: Record<Kind, { provider: string; base_url: string; model: string; api_key: string; size: string; voice: string; send_duration: boolean }>
}

function toDraft(settings: DramaSettings): Draft {
  const kinds = {} as Draft['kinds']
  for (const { key } of KINDS) {
    const m = settings[key] as ModalitySettings
    kinds[key] = {
      provider: m.provider,
      base_url: m.base_url,
      model: m.model,
      // api_key 永远是空的：后端不会把它回显出来，用户想换就重新填
      api_key: '',
      size: m.size,
      voice: m.voice,
      send_duration: m.send_duration ?? false,
    }
  }
  return {
    title: settings.title,
    genre: settings.genre,
    logline: settings.logline ?? '',
    style: settings.style,
    kinds,
  }
}

export default function DramaSettingsPanel({
  dramaId,
  onSaved,
  onDeleted,
}: {
  dramaId: number
  /** 保存后通知父组件（改名要刷新左侧列表） */
  onSaved: () => void
  /** 删除成功后通知父组件（清空当前选择、刷新列表） */
  onDeleted: () => void
}) {
  const [settings, setSettings] = useState<DramaSettings | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    setSettings(null)
    setDraft(null)
    setError('')
    setMessage('')
    api
      .getDramaSettings(dramaId)
      .then((next) => {
        setSettings(next)
        setDraft(toDraft(next))
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : '读取设置失败'))
  }, [dramaId])

  const changedKinds = useMemo(() => {
    if (!settings || !draft) return [] as Kind[]
    return KINDS.map((k) => k.key).filter((key) => {
      const before = settings[key] as ModalitySettings
      const now = draft.kinds[key]
      return (
        now.provider !== before.provider ||
        now.base_url !== before.base_url ||
        now.model !== before.model ||
        now.size !== before.size ||
        now.voice !== before.voice ||
        now.send_duration !== (before.send_duration ?? false) ||
        now.api_key !== ''
      )
    })
  }, [settings, draft])

  function patchKind(key: Kind, field: 'provider' | 'base_url' | 'model' | 'api_key' | 'size' | 'voice', value: string) {
    setDraft((prev) => (prev ? { ...prev, kinds: { ...prev.kinds, [key]: { ...prev.kinds[key], [field]: value } } } : prev))
  }

  async function save() {
    if (!draft) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const payload: Parameters<typeof api.updateDramaSettings>[1] = {
        title: draft.title,
        genre: draft.genre,
        logline: draft.logline,
        style: draft.style,
      }
      // 只提交**改动过的**模态：一次全传会把没动的配置也覆盖一遍，容易出现意外
      for (const key of changedKinds) {
        const now = draft.kinds[key]
        const body: ModalityPayload = {
          provider: now.provider,
          base_url: now.base_url,
          model: now.model,
        }
        if (KINDS.find((k) => k.key === key)?.extras.includes('size')) body.size = now.size
        if (KINDS.find((k) => k.key === key)?.extras.includes('voice')) body.voice = now.voice
        if (KINDS.find((k) => k.key === key)?.extras.includes('send_duration')) body.send_duration = now.send_duration
        // 只有真的填了才发 api_key；空字符串表示"清除已保存的 Key"
        if (now.api_key.trim() !== '') body.api_key = now.api_key.trim()
        payload[key] = body
      }
      const next = await api.updateDramaSettings(dramaId, payload)
      setSettings(next)
      setDraft(toDraft(next))
      setMessage(next.allow_server_key_fallback ? '已保存' : '已保存（本服务要求每个短剧填自己的 Key）')
      onSaved()
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  async function clearKey(key: Kind) {
    if (!settings) return
    setBusy(true)
    setError('')
    try {
      const next = await api.updateDramaSettings(dramaId, { [key]: { api_key: '' } })
      setSettings(next)
      setDraft(toDraft(next))
      setMessage('已清除这个短剧的 Key，将回退到服务端默认')
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  /**
   * 删除整部短剧。
   *
   * **这个动作不可恢复**：后端会级联删掉剧集、分镜、逐句台词、角色，
   * 以及磁盘上所有已生成的图片 / 音频 / 动态片段 / 成片。
   * 所以它被放在"设置 → 危险操作"里，而不是列表标题旁边 ——
   * 列表里紧挨标题的删除按钮点错一次就没了。
   */
  async function removeDrama() {
    const ok = window.confirm(
      `确定删除《${settings?.title ?? ''}》？\n\n` +
        '这会同时删除：全部剧集与分镜、角色卡与三视图、' +
        '以及所有已生成的画面 / 配音 / 动态片段 / 成片。\n\n' +
        '删除后无法恢复。',
    )
    if (!ok) return
    setDeleting(true)
    setError('')
    try {
      await api.deleteDrama(dramaId)
      onDeleted()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  if (error && !settings) return <p className="image-error">{error}</p>
  if (!settings || !draft) return <p className="muted small">读取设置中…</p>

  return (
    <div className="settings-panel">
      <h3>短剧设置</h3>

      <div className="settings-group">
        <h4>短剧信息</h4>
        <div className="settings-grid">
          <label>
            名称
            <input value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          </label>
          <label>
            题材
            <input value={draft.genre} onChange={(e) => setDraft({ ...draft, genre: e.target.value })} />
          </label>
          <label className="wide">
            一句话梗概
            <input value={draft.logline} onChange={(e) => setDraft({ ...draft, logline: e.target.value })} />
          </label>
          <label className="wide">
            视觉风格（会拼进画面提示词）
            <input value={draft.style} onChange={(e) => setDraft({ ...draft, style: e.target.value })} />
          </label>
        </div>
      </div>

      <p className="muted small">
        每个模型都可以填<b>你自己的 Key</b>（用自己的额度）。<b>留空则使用服务端默认</b>
        {!settings.allow_server_key_fallback && ' —— 但本服务已关闭默认 Key，必须填才能生成'}。
        Key 会加密保存，保存后不会再显示明文。
      </p>

      {KINDS.map(({ key, title, hint, extras }) => {
        const current = settings[key] as ModalitySettings
        const now = draft.kinds[key]
        return (
          <div className="settings-group" key={key}>
            <h4>
              {title}
              <span className={`badge ${current.source === 'drama' ? 'ready' : 'draft'}`}>
                {current.source === 'drama' ? '用你自己的配置' : '用服务端默认'}
              </span>
            </h4>
            <p className="muted small">{hint}</p>

            <div className="settings-grid">
              <label>
                provider
                <select value={now.provider} onChange={(e) => patchKind(key, 'provider', e.target.value)}>
                  {/* 空值 = 跟着服务端走；其余是具体模式 */}
                  <option value="">（跟随服务端）</option>
                  <option value="mock">mock（本地假实现，不花钱）</option>
                  <option value="openai_compat">openai_compat（真模型）</option>
                  {key === 'video' && <option value="none">none（不生成视频）</option>}
                </select>
              </label>

              <label>
                模型名
                <input
                  value={now.model}
                  onChange={(e) => patchKind(key, 'model', e.target.value)}
                  placeholder="如 deepseek-chat / cogview-3-flash"
                />
              </label>

              <label className="wide">
                API 地址（必须 https）
                <input
                  value={now.base_url}
                  onChange={(e) => patchKind(key, 'base_url', e.target.value)}
                  placeholder="如 https://api.deepseek.com/v1"
                />
              </label>

              <label className="wide">
                API Key
                <input
                  type="password"
                  value={now.api_key}
                  onChange={(e) => patchKind(key, 'api_key', e.target.value)}
                  placeholder={
                    current.has_key
                      ? `已保存（${current.api_key_hint}）—— 不填表示不修改`
                      : '留空则使用服务端默认'
                  }
                  autoComplete="new-password"
                />
              </label>

              {extras.includes('size') && (
                <label>
                  尺寸
                  <input
                    value={now.size}
                    onChange={(e) => patchKind(key, 'size', e.target.value)}
                    placeholder="如 1024x1024 / 1080x1920"
                  />
                </label>
              )}
              {extras.includes('voice') && (
                <label>
                  默认音色
                  <input
                    value={now.voice}
                    onChange={(e) => patchKind(key, 'voice', e.target.value)}
                    placeholder="如 tongtong（角色可单独指定）"
                  />
                </label>
              )}
              {extras.includes('send_duration') && (
                <label className="inline">
                  <input
                    type="checkbox"
                    checked={now.send_duration}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        kinds: { ...draft.kinds, [key]: { ...draft.kinds[key], send_duration: e.target.checked } },
                      })
                    }
                  />
                  发送 duration 参数（只有 cogvideox-3 支持）
                </label>
              )}
            </div>

            {current.has_key && (
              <button className="link" disabled={busy} onClick={() => void clearKey(key)}>
                清除这个短剧的 Key
              </button>
            )}
          </div>
        )
      })}

      {error && <p className="image-error">{error}</p>}
      <div className="panel-actions">
        <button onClick={() => void save()} disabled={busy}>
          {busy ? '保存中…' : '保存设置'}
        </button>
        {message && <span className="muted">{message}</span>}
        {changedKinds.length > 0 && (
          <span className="muted small">将更新：{changedKinds.join(' / ')}</span>
        )}
      </div>

      {/* 危险操作放最后、并要求先展开"短剧设置"才能看到 —— 降低误删的可能 */}
      <div className="danger-zone">
        <h4>危险操作</h4>
        <p className="muted small">
          删除这部短剧会<b>级联删除</b>全部剧集、分镜、角色卡与三视图，
          以及磁盘上已生成的画面 / 配音 / 动态片段 / 成片。
          <b>删除后无法恢复。</b>
        </p>
        <button className="danger" onClick={() => void removeDrama()} disabled={deleting || busy}>
          {deleting ? '删除中…' : `删除《${settings.title}》`}
        </button>
      </div>
    </div>
  )
}
