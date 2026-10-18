import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import AuthMedia from './AuthMedia'
import CharacterPanel from './CharacterPanel'
import DramaSettingsPanel from './DramaSettingsPanel'
import type {
  Character,
  Drama,
  Episode,
  EpisodeDetail,
  GenerationTask,
  Shot,
  User,
} from './types'

const TASK_STATUS_TEXT: Record<GenerationTask['status'], string> = {
  pending: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '失败',
}

const TASK_KIND_TEXT: Record<GenerationTask['kind'], string> = {
  shots: '生成分镜',
  images: '生成画面',
  clips: '生成动态镜头',
  audio: '生成配音',
  video: '合成视频',
  cast: '分析角色',
  turnaround: '生成角色三视图',
}

export default function DramaWorkspace({
  user,
  onLogout,
}: {
  user: User
  onLogout: () => void
}) {
  const [dramas, setDramas] = useState<Drama[]>([])
  const [selectedDramaId, setSelectedDramaId] = useState<number | null>(null)
  const [episodes, setEpisodes] = useState<Episode[]>([])
  const [selectedEpisode, setSelectedEpisode] = useState<EpisodeDetail | null>(null)
  const [task, setTask] = useState<GenerationTask | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const [newDrama, setNewDrama] = useState({ title: '', genre: '都市', logline: '', style: '' })

  // 「新增一集」面板：点按钮才展开，不再常驻一个输入框
  const [showNewEpisode, setShowNewEpisode] = useState(false)
  const [newEp, setNewEp] = useState({ title: '', synopsis: '' })
  const [newEpHint, setNewEpHint] = useState('')

  // 详情区的编辑草稿：用户改完点保存才写库，避免误改
  const [draft, setDraft] = useState({ title: '', synopsis: '' })
  const [draftHint, setDraftHint] = useState('')
  const [savingDraft, setSavingDraft] = useState(false)
  const [aiBusy, setAiBusy] = useState<'new' | 'draft' | null>(null)

  const [shotCount, setShotCount] = useState(6)
  const [hint, setHint] = useState('')
  // 画面/配音/成片重新生成后，URL 没变但内容变了 —— 用这个版本号强制媒体组件重新拉取
  const [mediaVersion, setMediaVersion] = useState(0)

  // 这部剧的角色表（配音的选角依据 + 画面的外貌锚点）
  const [characters, setCharacters] = useState<Character[]>([])
  // 正在编辑哪个镜头（null = 都没在编辑）+ 该镜头的草稿
  const [editingShotId, setEditingShotId] = useState<number | null>(null)
  const [shotDraft, setShotDraft] = useState<{
    shot_type: string
    camera: string
    description: string
    dialogue: string
    duration_sec: string
    image_prompt: string
  } | null>(null)

  const loadedTaskId = useRef<number | null>(null)

  const showError = (e: unknown) => setError(e instanceof Error ? e.message : '出错了')

  const loadDramas = useCallback(async () => {
    try {
      setDramas(await api.listDramas())
    } catch (e) {
      showError(e)
    }
  }, [])

  const loadEpisodes = useCallback(async (dramaId: number) => {
    try {
      setEpisodes(await api.listEpisodes(dramaId))
      setSelectedEpisode(null)
      setTask(null)
    } catch (e) {
      showError(e)
    }
  }, [])

  const loadEpisode = useCallback(async (episodeId: number) => {
    try {
      const detail = await api.getEpisode(episodeId)
      setSelectedEpisode(detail)
      // 进入某一集时，把它现有的标题/大纲填进编辑区
      setDraft({ title: detail.title, synopsis: detail.synopsis })
      setDraftHint('')
    } catch (e) {
      showError(e)
    }
  }, [])

  const loadCharacters = useCallback(async (dramaId: number) => {
    try {
      setCharacters(await api.listCharacters(dramaId))
    } catch (e) {
      showError(e)
    }
  }, [])

  useEffect(() => {
    void loadDramas()
  }, [loadDramas])

  useEffect(() => {
    if (selectedDramaId !== null) {
      void loadEpisodes(selectedDramaId)
      void loadCharacters(selectedDramaId)
    } else {
      setCharacters([])
    }
  }, [selectedDramaId, loadEpisodes, loadCharacters])

  // 任务轮询：每 0.8 秒查一次，到达终态后刷新分镜
  useEffect(() => {
    if (!task) return
    if (task.status === 'succeeded' || task.status === 'failed') {
      if (task.status === 'succeeded' && loadedTaskId.current !== task.id) {
        loadedTaskId.current = task.id
        // 画面/配音/成片任务成功后，媒体内容变了，要强制重新拉取
        if (task.kind !== 'shots') setMediaVersion((v) => v + 1)
        if (task.kind === 'turnaround') {
          // 剧级任务：刷新角色表（三视图是挂在角色上的），不涉及某一集
          if (selectedDramaId !== null) void loadCharacters(selectedDramaId)
        } else if (task.episode_id !== null) {
          void loadEpisode(task.episode_id)
        }
      }
      return
    }
    const timer = setInterval(() => {
      api
        .getTask(task.id)
        .then(setTask)
        .catch(showError)
    }, 800)
    return () => clearInterval(timer)
  }, [task, loadEpisode])

  async function createDrama(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const drama = await api.createDrama(newDrama.title, newDrama.genre, newDrama.logline, newDrama.style)
      setDramas([drama, ...dramas])
      setSelectedDramaId(drama.id)
      setNewDrama({ title: '', genre: '都市', logline: '', style: '' })
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  function openNewEpisode() {
    setNewEp({ title: '', synopsis: '' })
    setNewEpHint('')
    setShowNewEpisode(true)
  }

  /** AI 起草大纲（新建一集）：依据「本集标题 + 前面所有剧集」，没有前情就只看短剧信息 */
  async function aiDraftForNew() {
    if (selectedDramaId === null) return
    setAiBusy('new')
    setError('')
    try {
      const res = await api.generateSynopsis(selectedDramaId, { title: newEp.title })
      setNewEp((prev) => ({ ...prev, synopsis: res.synopsis }))
      setNewEpHint(
        res.used_context_episodes.length > 0
          ? `AI 参考了第 ${res.used_context_episodes.join('、')} 集的内容起草`
          : '这是第一集，AI 只依据短剧名称等信息起草',
      )
    } catch (err) {
      showError(err)
    } finally {
      setAiBusy(null)
    }
  }

  /** AI 重新起草当前这一集的大纲（前情 = 编号比它小的剧集） */
  async function aiDraftForCurrent() {
    if (selectedDramaId === null || !selectedEpisode) return
    setAiBusy('draft')
    setError('')
    try {
      const res = await api.generateSynopsis(selectedDramaId, {
        title: draft.title,
        after_number: selectedEpisode.number,
      })
      setDraft((prev) => ({ ...prev, synopsis: res.synopsis }))
      setDraftHint(
        res.used_context_episodes.length > 0
          ? `AI 参考了第 ${res.used_context_episodes.join('、')} 集 —— 满意就点「保存」`
          : '这是第一集，AI 只依据短剧名称等信息起草 —— 满意就点「保存」',
      )
    } catch (err) {
      showError(err)
    } finally {
      setAiBusy(null)
    }
  }

  async function createEpisode() {
    if (selectedDramaId === null) return
    setBusy(true)
    setError('')
    try {
      const created = await api.createEpisode(selectedDramaId, newEp.title, newEp.synopsis)
      setShowNewEpisode(false)
      setNewEp({ title: '', synopsis: '' })
      setEpisodes(await api.listEpisodes(selectedDramaId))
      setTask(null)
      // 建完立刻选中：它的编辑区 + 镜头生成区就在下方
      await loadEpisode(created.id)
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  async function saveDraft() {
    if (!selectedEpisode) return
    setSavingDraft(true)
    setError('')
    try {
      const updated = await api.updateEpisode(selectedEpisode.id, draft)
      setSelectedEpisode(updated)
      setEpisodes((prev) =>
        prev.map((e) => (e.id === updated.id ? { ...e, title: updated.title, synopsis: updated.synopsis } : e)),
      )
      setDraftHint('已保存')
    } catch (err) {
      showError(err)
    } finally {
      setSavingDraft(false)
    }
  }

  async function startGeneration() {
    if (!selectedEpisode) return
    setBusy(true)
    setError('')
    loadedTaskId.current = null
    try {
      setTask(await api.generate(selectedEpisode.id, shotCount, hint))
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  /** 文生图：为这一集的镜头批量生成画面（跳过已生成的，省钱） */
  async function startImageGeneration() {
    if (!selectedEpisode) return
    if (selectedEpisode.shots.length === 0) {
      showError(new Error('这一集还没有分镜，请先点「AI 生成分镜」'))
      return
    }
    setBusy(true)
    setError('')
    loadedTaskId.current = null
    try {
      setTask(await api.generateImages(selectedEpisode.id, true))
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  /** TTS：为这一集有台词的镜头批量生成配音 */
  async function startAudioGeneration() {
    if (!selectedEpisode) return
    if (selectedEpisode.shots.length === 0) {
      showError(new Error('这一集还没有分镜，请先点「AI 生成分镜」'))
      return
    }
    setBusy(true)
    setError('')
    loadedTaskId.current = null
    try {
      setTask(await api.generateAudio(selectedEpisode.id, true))
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  /** 合成成片：本地 ffmpeg 把「画面 + 配音」按时长拼成竖屏 MP4 */
  async function startVideoComposition() {
    if (!selectedEpisode) return
    setBusy(true)
    setError('')
    loadedTaskId.current = null
    try {
      setTask(await api.generateVideo(selectedEpisode.id))
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  /**
   * 图生视频：把每个镜头的静图变成真的会动的短片。
   *
   * 不做这一步的话，成片就是"每张图定格 + 配音"的幻灯片 —— 画面完全静止。
   * 这一步可能很慢（真实视频模型几十秒到几分钟一条），所以进度条要盯着看。
   */
  async function startClipGeneration() {
    if (!selectedEpisode) return
    if (selectedEpisode.shots.length === 0) {
      showError(new Error('这一集还没有分镜，请先点「AI 生成分镜」'))
      return
    }
    setBusy(true)
    setError('')
    loadedTaskId.current = null
    try {
      setTask(await api.generateClips(selectedEpisode.id, true))
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
    }
  }

  /**
   * 手动编辑镜头。
   *
   * 为什么要给这个能力：AI 生成的分镜只是初稿，用户最常改的就是
   * 画面描述、台词、时长。不能编辑就只能整集重生成（还花钱）。
   *
   * 一个必须让用户知道的后果：**改台词会作废这个镜头的配音**
   * （后端会删掉逐句台词行和音频文件）—— 因为台词变了，旧配音就是错的。
   * 界面上会提示，避免用户以为音频"凭空消失"。
   */
  function beginEditShot(shot: Shot) {
    setEditingShotId(shot.id)
    setShotDraft({
      shot_type: shot.shot_type,
      camera: shot.camera,
      description: shot.description,
      dialogue: shot.dialogue,
      duration_sec: String(shot.duration_sec),
      image_prompt: shot.image_prompt,
    })
  }

  function cancelEditShot() {
    setEditingShotId(null)
    setShotDraft(null)
  }

  function patchShotDraft(key: 'shot_type' | 'camera' | 'description' | 'dialogue' | 'duration_sec' | 'image_prompt', value: string) {
    setShotDraft((prev) => (prev ? { ...prev, [key]: value } : prev))
  }

  async function saveShot(shotId: number) {
    if (!shotDraft) return
    setBusy(true)
    setError('')
    try {
      await api.updateShot(shotId, {
        shot_type: shotDraft.shot_type,
        camera: shotDraft.camera,
        description: shotDraft.description,
        dialogue: shotDraft.dialogue,
        duration_sec: Number(shotDraft.duration_sec) || 3,
        image_prompt: shotDraft.image_prompt,
      })
      cancelEditShot()
      if (selectedEpisode) await loadEpisode(selectedEpisode.id)
      setMediaVersion((v) => v + 1) // 台词改了，后端已经把音频作废
    } catch (e) {
      showError(e)
    } finally {
      setBusy(false)
    }
  }

  /**
   * 删除短剧后的收尾（删除动作本身在「短剧设置」面板里）。
   *
   * 为什么把删除从"列表右侧的按钮"挪到设置里：
   * 列表里紧挨着标题的删除按钮太容易被误点，而删短剧会**级联删掉剧集、分镜、
   * 角色、以及磁盘上所有生成的图片/音频/视频** —— 不可恢复。
   * 放进设置面板后，用户必须先展开设置、看到"危险操作"的说明，才可能点到它。
   */
  function handleDramaDeleted() {
    setSelectedDramaId(null)
    setSelectedEpisode(null)
    setEpisodes([])
    setCharacters([])
    setTask(null)
    void loadDramas()
  }

  return (
    <div className="workspace">
      <header>
        <h1>AI 短剧工作室</h1>
        <span className="muted">
          {user.username} · <button className="link" onClick={onLogout}>退出</button>
        </span>
      </header>

      {error && (
        <div className="error-banner">
          {error}
          <button className="link" onClick={() => setError('')}>关闭</button>
        </div>
      )}

      <div className="columns">
        {/* 左栏：短剧 */}
        <aside>
          <h2>我的短剧</h2>
          <form onSubmit={createDrama}>
            <input
              placeholder="剧名（必填）"
              value={newDrama.title}
              onChange={(e) => setNewDrama({ ...newDrama, title: e.target.value })}
            />
            <input
              placeholder="题材，如：都市 / 古装 / 悬疑"
              value={newDrama.genre}
              onChange={(e) => setNewDrama({ ...newDrama, genre: e.target.value })}
            />
            <textarea
              placeholder="一句话梗概（可选）"
              value={newDrama.logline}
              onChange={(e) => setNewDrama({ ...newDrama, logline: e.target.value })}
              rows={2}
            />
            <input
              placeholder="视觉风格（可选），如：冷色调、手持镜头"
              value={newDrama.style}
              onChange={(e) => setNewDrama({ ...newDrama, style: e.target.value })}
            />
            <button type="submit" disabled={busy || !newDrama.title}>新建短剧</button>
          </form>
          <ul className="drama-list">
            {dramas.map((d) => (
              <li
                key={d.id}
                className={d.id === selectedDramaId ? 'active' : ''}
                onClick={() => setSelectedDramaId(d.id)}
              >
                {/* 删除按钮挪到「短剧设置」里了：列表里紧挨标题的删除按钮太容易误点 */}
                <span>{d.title}</span>
              </li>
            ))}
            {dramas.length === 0 && <li className="muted">还没有短剧，先建一部吧</li>}
          </ul>
        </aside>

        {/* 右栏：剧集与分镜 */}
        <main>
          {selectedDramaId === null ? (
            <p className="muted">选择或创建一部短剧开始创作</p>
          ) : (
            <>
              {/* 短剧设置：和"角色卡""剧集"并列，属于**这部短剧**而不是某一集 */}
              <details className="collapsible">
                <summary>短剧设置（名称 / 模型 / 我的 API Key）</summary>
                <DramaSettingsPanel
                  dramaId={selectedDramaId}
                  onSaved={() => {
                    void loadDramas()
                  }}
                  onDeleted={handleDramaDeleted}
                />
              </details>

              {/*
                角色卡提到**剧级**：它本来就是按剧存的（characters.drama_id），
                这一集的每个镜头都会参考它。放在这里的好处是
                **不用先点开某一集**就能看到/编辑角色，先定角色再写剧本更顺。
                外面套一层 <details> 是为了能收起 —— 它内容很长（每个角色一张三视图 + 9 个字段），
                展开时会把下面的剧集列表挤得很远。
              */}
              <details className="collapsible">
                <summary>
                  角色卡与三视图（{characters.length} 个角色
                  {characters.filter((c) => c.turnaround_path).length > 0
                    ? ` · ${characters.filter((c) => c.turnaround_path).length} 个已有三视图`
                    : ''}
                  ）
                </summary>
                <CharacterPanel
                  dramaId={selectedDramaId}
                  characters={characters}
                  version={mediaVersion}
                  onChanged={() => {
                    void loadCharacters(selectedDramaId)
                    setMediaVersion((v) => v + 1)
                  }}
                />
              </details>

              <h2>剧集</h2>
              <div className="episode-header">
                <button onClick={openNewEpisode} disabled={showNewEpisode}>＋ 新增一集</button>
              </div>

              {showNewEpisode && (
                <section className="new-episode-panel">
                  <h4>新增一集</h4>

                  <label>本集标题（可选，留空则让 AI 起名）</label>
                  <input
                    placeholder="例如：末班车上的同一张脸"
                    value={newEp.title}
                    onChange={(e) => setNewEp({ ...newEp, title: e.target.value })}
                  />

                  <label>本集内容（大纲）—— AI 生成分镜时会以它为主要依据</label>
                  <textarea
                    rows={4}
                    placeholder="写清这一集发生了什么：谁、在哪、做了什么、结尾留什么悬念。留空则让 AI 自行发挥。"
                    value={newEp.synopsis}
                    onChange={(e) => setNewEp({ ...newEp, synopsis: e.target.value })}
                  />

                  <div className="panel-actions">
                    <button className="secondary" onClick={() => void aiDraftForNew()} disabled={aiBusy !== null}>
                      {aiBusy === 'new' ? 'AI 起草中…' : 'AI 生成大纲'}
                    </button>
                    <button onClick={() => void createEpisode()} disabled={busy}>创建这一集</button>
                    <button className="link" onClick={() => setShowNewEpisode(false)}>取消</button>
                    {newEpHint && <span className="muted">{newEpHint}</span>}
                  </div>
                </section>
              )}

              <ul className="episode-list">
                {episodes.map((ep) => (
                  <li
                    key={ep.id}
                    className={ep.id === selectedEpisode?.id ? 'active' : ''}
                    onClick={() => void loadEpisode(ep.id)}
                  >
                    第 {ep.number} 集 {ep.title || '（未命名）'}
                    <span className={`badge ${ep.status}`}>{ep.status === 'ready' ? '已生成' : '草稿'}</span>
                  </li>
                ))}
              </ul>

              {selectedEpisode && (
                <section className="episode-detail">
                  <h3>第 {selectedEpisode.number} 集</h3>

                  <label>本集标题</label>
                  <input
                    placeholder="（未命名）"
                    value={draft.title}
                    onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                  />

                  <label>本集内容（大纲）—— 这是 AI 生成分镜的主要依据</label>
                  <textarea
                    rows={4}
                    placeholder="写清这一集发生了什么。留空则让 AI 自行发挥。"
                    value={draft.synopsis}
                    onChange={(e) => setDraft({ ...draft, synopsis: e.target.value })}
                  />

                  <div className="panel-actions">
                    <button className="secondary" onClick={() => void aiDraftForCurrent()} disabled={aiBusy !== null}>
                      {aiBusy === 'draft' ? 'AI 起草中…' : 'AI 生成大纲'}
                    </button>
                    <button onClick={() => void saveDraft()} disabled={savingDraft}>
                      {savingDraft ? '保存中…' : '保存'}
                    </button>
                    {draftHint && <span className="muted">{draftHint}</span>}
                  </div>

                  <h4 className="section-title">镜头生成</h4>
                  <div className="generate-bar">
                    <label>
                      镜头数
                      <input
                        type="number"
                        min={1}
                        max={20}
                        value={shotCount}
                        onChange={(e) => setShotCount(Number(e.target.value))}
                      />
                    </label>
                    <input
                      placeholder="额外要求（可选），如：结尾留一个反转钩子"
                      value={hint}
                      onChange={(e) => setHint(e.target.value)}
                    />
                    <button onClick={() => void startGeneration()} disabled={busy}>
                      {busy ? '提交中…' : 'AI 生成分镜'}
                    </button>
                    <button
                      className="secondary"
                      onClick={() => void startImageGeneration()}
                      disabled={busy || selectedEpisode.shots.length === 0}
                      title="用每个镜头的文生图提示词生成画面（已生成的会跳过）"
                    >
                      生成画面
                    </button>
                    <button
                      className="secondary"
                      onClick={() => void startAudioGeneration()}
                      disabled={busy || selectedEpisode.shots.length === 0}
                      title="把每个镜头的台词合成为语音（没有台词的镜头会跳过）"
                    >
                      生成配音
                    </button>
                    <button
                      className="secondary"
                      onClick={() => void startClipGeneration()}
                      disabled={busy || selectedEpisode.shots.length === 0}
                      title="把静图变成会动的短片（图生视频）。不做这步，成片里的画面是静止的；已生成的会跳过。"
                    >
                      生成动态镜头
                    </button>
                    <button
                      className="secondary"
                      onClick={() => void startVideoComposition()}
                      disabled={busy || selectedEpisode.shots.length === 0}
                      title="把这一集的画面和配音拼成竖屏 MP4（需要先生成画面）"
                    >
                      合成视频
                    </button>
                  </div>

                  {task && (
                    <p className="task-status">
                      任务 #{task.id}（{TASK_KIND_TEXT[task.kind] ?? task.kind}）：
                      {task.status === 'failed'
                        ? `${TASK_STATUS_TEXT[task.status]}：${task.error ?? '未知错误'}`
                        : task.status === 'running' && task.total > 0
                          ? `${TASK_STATUS_TEXT[task.status]} ${task.done}/${task.total}`
                          : TASK_STATUS_TEXT[task.status]}
                    </p>
                  )}

                  <table className="shots">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>画面</th>
                        <th>景别</th>
                        <th>运镜</th>
                        <th>时长</th>
                        <th>画面描述</th>
                        <th>台词 / 旁白</th>
                        <th>配音</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedEpisode.shots.map((s) => {
                        const editing = editingShotId === s.id && shotDraft !== null
                        return (
                        <tr key={s.id} className={editing ? 'editing' : undefined}>
                          <td>{s.number}</td>
                          <td className="shot-image">
                            {s.clip_path ? (
                              /* 有动态片段就直接播它 —— 让用户一眼看到"画面真的在动"，
                                 而不是只给一个"已完成"的文字标记。 */
                              <div className="clip-cell">
                                <AuthMedia
                                  kind="video"
                                  path={api.shotClipPath(s.id)}
                                  className="thumb"
                                  cacheKey={mediaVersion}
                                  controls={false}
                                  autoPlay
                                  loop
                                  muted
                                />
                                <span className="clip-badge" title="这个镜头会用这段动态片段">
                                  会动
                                </span>
                              </div>
                            ) : s.image_path ? (
                              <div className="clip-cell">
                                <AuthMedia
                                  kind="image"
                                  path={api.shotImagePath(s.id)}
                                  alt={`镜头 ${s.number} 的画面`}
                                  className="thumb"
                                  cacheKey={mediaVersion}
                                />
                                <span className="clip-badge static" title="这个镜头是静图：点「生成动态镜头」让它动起来">
                                  静图
                                </span>
                              </div>
                            ) : s.image_error ? (
                              <span className="image-error" title={s.image_error}>生成失败</span>
                            ) : (
                              <span className="muted">未生成</span>
                            )}
                            {s.clip_error && (
                              <span className="image-error" title={s.clip_error}>
                                动态片段失败
                              </span>
                            )}
                          </td>

                          {editing && shotDraft ? (
                            <>
                              <td>
                                <input
                                  className="mini"
                                  value={shotDraft.shot_type}
                                  onChange={(e) => patchShotDraft('shot_type', e.target.value)}
                                />
                              </td>
                              <td>
                                <input
                                  className="mini"
                                  value={shotDraft.camera}
                                  onChange={(e) => patchShotDraft('camera', e.target.value)}
                                />
                              </td>
                              <td>
                                <input
                                  className="mini num"
                                  type="number"
                                  step={0.5}
                                  min={0.5}
                                  max={60}
                                  value={shotDraft.duration_sec}
                                  onChange={(e) => patchShotDraft('duration_sec', e.target.value)}
                                />
                              </td>
                              <td>
                                <textarea
                                  rows={3}
                                  value={shotDraft.description}
                                  onChange={(e) => patchShotDraft('description', e.target.value)}
                                  placeholder="画面描述（中文）"
                                />
                                <textarea
                                  rows={2}
                                  className="mono"
                                  value={shotDraft.image_prompt}
                                  onChange={(e) => patchShotDraft('image_prompt', e.target.value)}
                                  placeholder="图像提示词（英文更准，可留空用描述）"
                                  title="这一栏直接决定画面。注意：出场角色的外貌会自动拼在它前面"
                                />
                              </td>
                              <td>
                                <textarea
                                  rows={3}
                                  value={shotDraft.dialogue}
                                  onChange={(e) => patchShotDraft('dialogue', e.target.value)}
                                  placeholder="格式：角色名：台词"
                                />
                                {shotDraft.dialogue !== s.dialogue ? (
                                  <span className="muted small">⚠️ 改台词会作废本镜头的配音</span>
                                ) : (
                                  <span className="muted small">每句一行，写成「角色名：台词」</span>
                                )}
                              </td>
                            </>
                          ) : (
                            <>
                              <td>{s.shot_type}</td>
                              <td>{s.camera}</td>
                              <td>{s.duration_sec}s</td>
                              <td>{s.description}</td>
                              <td>{s.dialogue}</td>
                            </>
                          )}

                          <td className="shot-audio">
                            {s.audio_path ? (
                              <AuthMedia
                                kind="audio"
                                path={api.shotAudioPath(s.id)}
                                className="audio-player"
                                cacheKey={mediaVersion}
                              />
                            ) : !(s.dialogue || '').trim() ? (
                              <span className="muted">无台词</span>
                            ) : s.audio_error ? (
                              <span className="image-error" title={s.audio_error}>生成失败</span>
                            ) : (
                              <span className="muted">未生成</span>
                            )}
                          </td>

                          <td className="shot-actions">
                            {editing ? (
                              <>
                                <button onClick={() => void saveShot(s.id)} disabled={busy}>
                                  保存
                                </button>
                                <button className="secondary" onClick={cancelEditShot} disabled={busy}>
                                  取消
                                </button>
                              </>
                            ) : (
                              <button className="secondary" onClick={() => beginEditShot(s)}>
                                编辑
                              </button>
                            )}
                          </td>
                        </tr>
                        )
                      })}
                      {selectedEpisode.shots.length === 0 && (
                        <tr>
                          <td colSpan={9} className="muted">还没有分镜 —— 点"AI 生成分镜"</td>
                        </tr>
                      )}
                    </tbody>
                  </table>

                  <h4 className="section-title">成片</h4>
                  {selectedEpisode.video_path ? (
                    <div className="video-panel">
                      <AuthMedia
                        kind="video"
                        path={api.episodeVideoPath(selectedEpisode.id)}
                        className="player"
                        cacheKey={mediaVersion}
                      />
                      <p className="muted">
                        竖屏 MP4（720×1280）· 点右上角"合成视频"可重新生成
                      </p>
                    </div>
                  ) : selectedEpisode.video_error ? (
                    <p className="image-error" title={selectedEpisode.video_error}>
                      上次合成失败：{selectedEpisode.video_error}
                    </p>
                  ) : (
                    <p className="muted">
                      还没有成片 —— 先生成画面（配音可选），再点"合成视频"
                    </p>
                  )}
                </section>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}
