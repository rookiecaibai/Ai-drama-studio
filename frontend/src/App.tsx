import { useEffect, useState } from 'react'
import { api, getToken, setToken } from './api'
import DramaWorkspace from './DramaWorkspace'
import Login from './Login'
import type { User } from './types'

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(getToken() !== null)

  useEffect(() => {
    // 刷新页面后：本地有 token 就用 /me 恢复会话，无效则清掉回到登录页
    if (!getToken()) return
    api
      .me()
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <p className="center muted">加载中…</p>
  if (!user) return <Login onLogin={setUser} />

  return (
    <DramaWorkspace
      user={user}
      onLogout={() => {
        setToken(null)
        setUser(null)
      }}
    />
  )
}
