import { useState } from 'react'
import { api, setToken } from './api'
import type { User } from './types'

export default function Login({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [isRegister, setIsRegister] = useState(false)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      if (isRegister) {
        await api.register(username, password)
      }
      const resp = await api.login(username, password)
      setToken(resp.access_token)
      onLogin(resp.user)
    } catch (err) {
      setError(err instanceof Error ? err.message : '出错了')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <h1>AI 短剧工作室</h1>
        <p className="muted">AI 驱动的短剧创作工具 —— 从一句话梗概到分镜脚本</p>
        <input
          placeholder="用户名（3-50 位字母数字下划线）"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoFocus
        />
        <input
          type="password"
          placeholder="密码（至少 8 位）"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <button type="submit" disabled={busy || !username || !password}>
          {busy ? '请稍候…' : isRegister ? '注册并登录' : '登录'}
        </button>
        <button
          type="button"
          className="link"
          onClick={() => {
            setIsRegister(!isRegister)
            setError('')
          }}
        >
          {isRegister ? '已有账号？去登录' : '没有账号？去注册'}
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </div>
  )
}
