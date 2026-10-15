import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import AuthMedia from './AuthMedia'
import type { Drama, Episode, EpisodeDetail, GenerationTask, User } from './types'

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

  useEffect(() => {
    void loadDramas()
  }, [loadDramas])

  useEffect(() => {
    if (selectedDramaId !== null) void loadEpisodes(selectedDramaId)
  }, [selectedDramaId, loadEpisodes])

  // 任务轮询：每 0.8 秒查一次，到达终态后刷新分镜
  useEffect(() => {
    if (!task) return
    if (task.status === 'succeeded' || task.status === 'failed') {
      if (task.status === 'succeeded' && loadedTaskId.current !== task.id) {
        loadedTaskId.current = task.id
        // 画面/配音/成片任务成功后，媒体内容变了，要强制重新拉取
        if (task.kind !== 'shots') setMediaVersion((v) => v + 1)
        void loadEpisode(task.episode_id)
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

  async function removeDrama(id: number) {
    if (!window.confirm('确定删除这部短剧？剧集和分镜会一起删除。')) return
    try {
      await api.deleteDrama(id)
      setDramas(dramas.filter((d) => d.id !== id))
      if (selectedDramaId === id) setSelectedDramaId(null)
    } catch (e) {
      showError(e)
    }
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
                <span>{d.title}</span>
                <button className="link" onClick={(e) => { e.stopPropagation(); void removeDrama(d.id) }}>
                  删除
                </button>
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
                      </tr>
                    </thead>
                    <tbody>
                      {selectedEpisode.shots.map((s) => (
                        <tr key={s.id}>
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
                          <td>{s.shot_type}</td>
                          <td>{s.camera}</td>
                          <td>{s.duration_sec}s</td>
                          <td>{s.description}</td>
                          <td>{s.dialogue}</td>
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
                        </tr>
                      ))}
                      {selectedEpisode.shots.length === 0 && (
                        <tr>
                          <td colSpan={8} className="muted">还没有分镜 —— 点"AI 生成分镜"</td>
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
