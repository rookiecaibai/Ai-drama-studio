import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { Drama, Episode, EpisodeDetail, GenerationTask, User } from './types'

const TASK_STATUS_TEXT: Record<GenerationTask['status'], string> = {
  pending: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '失败',
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
  const [newEpisodeTitle, setNewEpisodeTitle] = useState('')
  const [shotCount, setShotCount] = useState(6)
  const [hint, setHint] = useState('')

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
      setSelectedEpisode(await api.getEpisode(episodeId))
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

  async function createEpisode(e: React.FormEvent) {
    e.preventDefault()
    if (selectedDramaId === null) return
    setBusy(true)
    setError('')
    try {
      await api.createEpisode(selectedDramaId, newEpisodeTitle)
      setNewEpisodeTitle('')
      await loadEpisodes(selectedDramaId)
    } catch (err) {
      showError(err)
    } finally {
      setBusy(false)
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
              <form onSubmit={createEpisode} className="inline-form">
                <input
                  placeholder="第 N 集标题（可选，自动编号）"
                  value={newEpisodeTitle}
                  onChange={(e) => setNewEpisodeTitle(e.target.value)}
                />
                <button type="submit" disabled={busy}>新增一集</button>
              </form>
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
                  <h3>第 {selectedEpisode.number} 集 · {selectedEpisode.title || '（未命名）'}</h3>
                  <p className="muted">{selectedEpisode.synopsis}</p>

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
                  </div>

                  {task && (
                    <p className="task-status">
                      任务 #{task.id}：
                      {task.status === 'failed'
                        ? `${TASK_STATUS_TEXT[task.status]}：${task.error ?? '未知错误'}`
                        : TASK_STATUS_TEXT[task.status]}
                    </p>
                  )}

                  <table className="shots">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>景别</th>
                        <th>运镜</th>
                        <th>时长</th>
                        <th>画面描述</th>
                        <th>台词 / 旁白</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selectedEpisode.shots.map((s) => (
                        <tr key={s.id}>
                          <td>{s.number}</td>
                          <td>{s.shot_type}</td>
                          <td>{s.camera}</td>
                          <td>{s.duration_sec}s</td>
                          <td>{s.description}</td>
                          <td>{s.dialogue}</td>
                        </tr>
                      ))}
                      {selectedEpisode.shots.length === 0 && (
                        <tr>
                          <td colSpan={6} className="muted">还没有分镜 —— 点"AI 生成分镜"</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </section>
              )}
            </>
          )}
        </main>
      </div>
    </div>
  )
}
