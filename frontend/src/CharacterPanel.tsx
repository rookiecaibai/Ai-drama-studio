import { useEffect, useState } from 'react'
import { api } from './api'
import AuthMedia from './AuthMedia'
import type { Character, VoiceOption } from './types'

/**
 * 角色卡面板。
 *
 * 它解决的是一个很实际的问题：**AI 猜的角色信息经常是错的**（名字看不出性别、
 * 年龄只能靠剧情推、外貌压根没有），而角色信息决定了后面两件事的全部质量：
 *   - 配音：性别/年龄 → 音色、音高、语速
 *   - 画面：年龄/外貌/职业 → 每个镜头的提示词（这是"角色长得一样"的唯一依据）
 * 所以角色卡必须是**可编辑的数据**，而不是写死的常量。
 *
 * 还有一个关键信息要在界面上讲清楚：三视图**不会**被喂给模型当参考图
 * （当前厂商的文生图都是纯文本输入），它的作用是让你先"看见"角色、
 * 并且在画镜头之前就把外貌描述定下来。所以面板上会明确写这句话 ——
 * 不写清楚，用户会以为画面不一致是代码的 bug。
 */

const GENDERS = [
  { value: 'male', label: '男' },
  { value: 'female', label: '女' },
  { value: 'unknown', label: '未知' },
]

const AGE_GROUPS = [
  { value: 'child', label: '儿童' },
  { value: 'teen', label: '少年' },
  { value: 'young', label: '青年' },
  { value: 'middle', label: '中年' },
  { value: 'elderly', label: '老年' },
]

/** 本地草稿：用户在输入框里改、点保存才写库 */
type Draft = {
  gender: string
  age: string
  appearance: string
  occupation: string
  background: string
  look_prompt: string
  voice: string
  speed: string
}

function toDraft(character: Character): Draft {
  return {
    gender: character.gender,
    age: character.age === null ? '' : String(character.age),
    appearance: character.appearance,
    occupation: character.occupation,
    background: character.background,
    look_prompt: character.look_prompt,
    voice: character.voice,
    speed: String(character.speed),
  }
}

/** 新增角色表单的初始值 */
const EMPTY_ADD = {
  name: '',
  gender: 'unknown',
  age: '',
  occupation: '',
  appearance: '',
  background: '',
}

export default function CharacterPanel({
  dramaId,
  characters,
  onChanged,
  version,
}: {
  dramaId: number
  characters: Character[]
  /** 保存/重新生成后通知父组件刷新（父组件负责重新拉取角色表） */
  onChanged: () => void
  /** 变化时强制重新拉取三视图（重新生成后 URL 没变但内容变了） */
  version: number
}) {
  const [voices, setVoices] = useState<VoiceOption[]>([])
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [savingId, setSavingId] = useState<number | null>(null)
  const [error, setError] = useState('')
  // 手动新增角色（后续集数出新角色时用）
  const [showAdd, setShowAdd] = useState(false)
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState('')
  const [addDraft, setAddDraft] = useState(EMPTY_ADD)

  useEffect(() => {
    void api
      .listVoices()
      .then(setVoices)
      .catch(() => setVoices([]))
  }, [])

  // 角色表刷新后，草稿跟着重置（未保存的修改会丢 —— 这是刻意的：显示的一定是库里的真实值）
  useEffect(() => {
    setDrafts(Object.fromEntries(characters.map((c) => [c.id, toDraft(c)])))
  }, [characters])

  function patchDraft(id: number, key: keyof Draft, value: string) {
    setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], [key]: value } }))
  }

  async function save(character: Character) {
    const draft = drafts[character.id]
    if (!draft) return
    setSavingId(character.id)
    setError('')
    try {
      await api.updateCharacter(character.id, {
        gender: draft.gender,
        age: draft.age.trim() === '' ? null : Number(draft.age),
        appearance: draft.appearance,
        occupation: draft.occupation,
        background: draft.background,
        look_prompt: draft.look_prompt,
        voice: draft.voice,
        speed: Number(draft.speed),
      })
      onChanged()
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSavingId(null)
    }
  }

  /**
   * 手动新增角色。
   *
   * 为什么需要：角色表原来是纯 AI 分析出来的（扫台词判断谁在说话），
   * 所以"只出现在画面里、没有台词"的人它发现不了；而且新角色第一次出现时
   * AI 还没分析过，用户只能干等。加完之后会立刻按性别/年龄配好音色，
   * 所以马上就能生成三视图、也能配音。
   */
  async function submitAdd() {
    const name = addDraft.name.trim()
    if (!name) return
    setAdding(true)
    setAddError('')
    try {
      await api.createCharacter(dramaId, {
        name,
        gender: addDraft.gender,
        age: addDraft.age.trim() === '' ? null : Number(addDraft.age),
        occupation: addDraft.occupation,
        appearance: addDraft.appearance,
        background: addDraft.background,
      })
      setAddDraft(EMPTY_ADD)
      setShowAdd(false)
      onChanged()
    } catch (err) {
      // 重名、空名这类错误后端会给出能看懂的话，直接展示
      setAddError(err instanceof Error ? err.message : '添加失败')
    } finally {
      setAdding(false)
    }
  }

  const isNarrator = (name: string) => ['旁白', '画外音', '解说'].includes(name)
  const empty = characters.length === 0

  return (
    <div className="character-panel">
      <div className="character-panel-head">
        {/* 标题由外层 tab / summary 承担，这里只放操作与提示 */}
        <span className="muted small">先定角色，再画镜头：外貌描述会拼进每张画面</span>
        <div className="panel-actions">
          <button className="secondary" onClick={() => setShowAdd((v) => !v)}>
            {showAdd ? '取消添加' : '＋ 添加角色'}
          </button>
          {!empty && (
            <button
              className="secondary"
              onClick={() =>
                void api
                  .generateTurnarounds(dramaId, true)
                  .then(onChanged)
                  .catch((err: unknown) =>
                    setError(err instanceof Error ? err.message : '提交失败'),
                  )
              }
              title="为所有有形象的角色画「正面/侧面/背面」三视图；旁白会被自动跳过；已有三视图的不会重画"
            >
              生成三视图
            </button>
          )}
        </div>
      </div>

      {showAdd && (
        <section className="character-add">
          <p className="muted small">
            手动加一个角色 —— 适合<b>后续集数出现的新角色</b>，以及 AI 分析不到的人
            （比如只站在背景里、没有台词的保安）。名字要和台词里「角色名：」的写法一致。
          </p>
          <div className="character-add-grid">
            <label>
              角色名（必填）
              <input
                value={addDraft.name}
                onChange={(e) => setAddDraft({ ...addDraft, name: e.target.value })}
                placeholder="例如：王警官"
              />
            </label>
            <label>
              性别
              <select
                value={addDraft.gender}
                onChange={(e) => setAddDraft({ ...addDraft, gender: e.target.value })}
              >
                {GENDERS.map((g) => (
                  <option key={g.value} value={g.value}>
                    {g.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              年龄
              <input
                type="number"
                min={0}
                max={200}
                value={addDraft.age}
                onChange={(e) => setAddDraft({ ...addDraft, age: e.target.value })}
                placeholder="留空则按性别定音色"
              />
            </label>
            <label>
              职业
              <input
                value={addDraft.occupation}
                onChange={(e) => setAddDraft({ ...addDraft, occupation: e.target.value })}
                placeholder="例如：刑警"
              />
            </label>
          </div>
          <label>
            外貌（影响每个镜头的画面，越具体越稳定）
            <textarea
              rows={2}
              value={addDraft.appearance}
              onChange={(e) => setAddDraft({ ...addDraft, appearance: e.target.value })}
              placeholder="例如：短发、微胖、深蓝夹克、右手有疤"
            />
          </label>
          <label>
            背景故事（可选，参与剧情理解）
            <textarea
              rows={2}
              value={addDraft.background}
              onChange={(e) => setAddDraft({ ...addDraft, background: e.target.value })}
              placeholder="例如：干了二十年刑警，女儿也在这一行"
            />
          </label>
          <div className="panel-actions">
            <button onClick={() => void submitAdd()} disabled={adding || !addDraft.name.trim()}>
              {adding ? '添加中…' : '添加'}
            </button>
            <button className="link" onClick={() => setShowAdd(false)}>
              取消
            </button>
            {addError && <span className="image-error">{addError}</span>}
          </div>
        </section>
      )}

      {error && <p className="image-error">{error}</p>}

      {empty ? (
        <p className="muted">
          还没有角色表。可以点上面的「＋ 添加角色」手动加，
          也可以点「生成配音」或「分析角色」让 AI 从剧本里整理出人物。
        </p>
      ) : (
        <>
          <p className="muted small">
            三视图用来<b>先确认角色长什么样</b>，再画每个镜头。
            如果图片厂商支持参考图（比如万相），系统会把裁好的<b>单人正面</b>当参考图传进去；
            不支持的厂商只能靠下面这段<b>外貌描述</b>——写得越具体，各个镜头里就越稳定。
          </p>

          <div className="character-cards">
        {characters.map((character) => {
          const draft = drafts[character.id] ?? toDraft(character)
          const narrator = isNarrator(character.name)
          return (
            <div className="character-card" key={character.id}>
              <div className="character-card-head">
                <strong>{character.name}</strong>
                {narrator && <span className="clip-badge static">旁白 · 无形象</span>}
              </div>

              <div className="character-card-body">
                <div className="character-sheet">
                  {character.turnaround_path ? (
                    <AuthMedia
                      kind="image"
                      path={api.characterTurnaroundPath(character.id)}
                      alt={`${character.name} 的三视图`}
                      className="turnaround"
                      cacheKey={version}
                    />
                  ) : character.turnaround_error ? (
                    <span className="image-error" title={character.turnaround_error}>
                      三视图失败
                    </span>
                  ) : (
                    <span className="muted small">{narrator ? '旁白不需要三视图' : '还没有三视图'}</span>
                  )}
                </div>

                <div className="character-fields">
                  <label>
                    性别
                    <select
                      value={draft.gender}
                      onChange={(e) => patchDraft(character.id, 'gender', e.target.value)}
                    >
                      {GENDERS.map((g) => (
                        <option key={g.value} value={g.value}>
                          {g.label}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label>
                    年龄
                    <input
                      type="number"
                      min={0}
                      max={200}
                      placeholder={AGE_GROUPS.find((a) => a.value === character.age_group)?.label}
                      value={draft.age}
                      onChange={(e) => patchDraft(character.id, 'age', e.target.value)}
                    />
                  </label>

                  <label>
                    职业
                    <input
                      value={draft.occupation}
                      onChange={(e) => patchDraft(character.id, 'occupation', e.target.value)}
                      placeholder="如：便利店夜班店员"
                    />
                  </label>

                  <label>
                    音色
                    <select
                      value={draft.voice}
                      onChange={(e) => patchDraft(character.id, 'voice', e.target.value)}
                    >
                      {draft.voice && !voices.some((v) => v.voice === draft.voice) && (
                        <option value={draft.voice}>{draft.voice}</option>
                      )}
                      {voices.map((v) => (
                        <option key={v.voice} value={v.voice}>
                          {v.label}
                          {v.pitch_hz ? ` · ${Math.round(v.pitch_hz)}Hz` : ''}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label>
                    语速
                    <input
                      type="number"
                      step={0.02}
                      min={0.5}
                      max={2}
                      value={draft.speed}
                      onChange={(e) => patchDraft(character.id, 'speed', e.target.value)}
                    />
                  </label>

                  <label className="wide">
                    外貌（画镜头时每次都会带上）
                    <textarea
                      rows={2}
                      value={draft.appearance}
                      onChange={(e) => patchDraft(character.id, 'appearance', e.target.value)}
                      placeholder="发型、脸型、体型、常穿的衣服。越具体越稳定"
                    />
                  </label>

                  <label className="wide">
                    背景故事（只给人看，不进画面提示词）
                    <textarea
                      rows={2}
                      value={draft.background}
                      onChange={(e) => patchDraft(character.id, 'background', e.target.value)}
                    />
                  </label>

                  <label className="wide">
                    锁定外貌提示词（留空 = 按上面自动生成）
                    <textarea
                      rows={2}
                      value={draft.look_prompt}
                      onChange={(e) => patchDraft(character.id, 'look_prompt', e.target.value)}
                      placeholder="手写一段更精准的锚定描述；填了就以它为准"
                    />
                  </label>
                </div>
              </div>

              <div className="character-card-foot">
                <button onClick={() => void save(character)} disabled={savingId === character.id}>
                  {savingId === character.id ? '保存中…' : '保存'}
                </button>
                {character.turnaround_path && (
                  <button
                    className="secondary"
                    onClick={() =>
                      void api
                        .generateTurnarounds(dramaId, false)
                        .then(onChanged)
                        .catch((err: unknown) =>
                          setError(err instanceof Error ? err.message : '提交失败'),
                        )
                    }
                    title="改完外貌后重画三视图（会把所有角色的三视图都重画一遍）"
                  >
                    重画三视图
                  </button>
                )}
              </div>
            </div>
          )
        })}
          </div>
        </>
      )}
    </div>
  )
}
