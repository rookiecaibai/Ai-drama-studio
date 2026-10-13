import { useEffect, useState } from 'react'
import { getToken } from './api'

/**
 * 带鉴权的图片组件。
 *
 * 为什么不直接写 <img src="/api/v1/shots/1/image">：
 * 浏览器发 <img> 请求时**不会带上 Authorization 头**，而后端要求
 * "登录 + 归属校验"才能看图（不能做成公开静态目录，否则谁猜到 URL 谁就能看别人的图）。
 *
 * 所以流程是：fetch 带 token 取回二进制 -> 转成 blob URL -> 再交给 <img> 显示。
 * 组件卸载时必须 revokeObjectURL，否则这些 blob 会一直占着内存（典型的内存泄漏）。
 */
export default function AuthImage({
  path,
  alt,
  className,
  cacheKey,
}: {
  path: string
  alt: string
  className?: string
  /** 变化时强制重新拉取（重新生成画面后，URL 没变但内容变了） */
  cacheKey?: string | number
}) {
  const [url, setUrl] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let objectUrl: string | null = null
    let cancelled = false
    setUrl(null)
    setFailed(false)

    fetch(path, { headers: { Authorization: `Bearer ${getToken() ?? ''}` } })
      .then((resp) => {
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        return resp.blob()
      })
      .then((blob) => {
        if (cancelled) return
        objectUrl = URL.createObjectURL(blob)
        setUrl(objectUrl)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })

    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [path, cacheKey])

  if (failed) return <span className="muted">加载失败</span>
  if (!url) return <span className="muted">加载中…</span>
  return <img src={url} alt={alt} className={className} />
}
