import { useEffect, useState } from 'react'
import { getToken } from './api'

type MediaKind = 'image' | 'audio' | 'video'

/**
 * 带鉴权的媒体组件（图片 / 音频 / 视频三合一）。
 *
 * 为什么不直接写 <img src> / <audio src> / <video src>：
 * 浏览器发这些标签的请求时**不会带上 Authorization 头**，而后端要求
 * "登录 + 归属校验"才能取媒体（不能做成公开静态目录，否则谁猜到 URL 谁就能看别人的图）。
 *
 * 所以流程是：fetch 带 token 取回二进制 -> 转成 blob URL -> 再交给对应标签。
 * 组件卸载时必须 revokeObjectURL，否则这些 blob 会一直占着内存（典型的内存泄漏）。
 */
export default function AuthMedia({
  path,
  kind,
  alt = '',
  className,
  cacheKey,
  controls = true,
}: {
  path: string
  kind: MediaKind
  alt?: string
  className?: string
  /** 变化时强制重新拉取（重新生成后 URL 没变但内容变了） */
  cacheKey?: string | number
  controls?: boolean
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

  if (kind === 'image') return <img src={url} alt={alt} className={className} />
  if (kind === 'audio') return <audio src={url} className={className} controls={controls} preload="none" />
  return <video src={url} className={className} controls={controls} preload="metadata" />
}
