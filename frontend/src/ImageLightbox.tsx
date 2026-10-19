import { useEffect } from 'react'
import AuthMedia from './AuthMedia'

/**
 * 图片放大查看（灯箱）。
 *
 * 为什么需要：三视图是 1344x768、分镜画面是 768x1344，
 * 但在界面里只能显示成 108px / 120px 的缩略图 —— **细节全丢了**，
 * 用户根本没法判断"这个角色的脸对不对、三个视角画得准不准"。
 * 缩略图只够"知道有没有"，判断质量必须能看大图。
 *
 * 实现要点：
 *  ① 复用 `AuthMedia` 取图 —— 媒体接口要带 token，不能直接写 <img src>（会 401）；
 *  ② 点空白处 / 点右上角 ✕ / 按 Esc 都能关；
 *  ③ 打开时锁住 body 滚动，否则滚轮会带着底下的表格一起滚，看大图时很难受。
 */
export default function ImageLightbox({
  imagePath,
  title,
  cacheKey,
  onClose,
}: {
  /** 媒体接口路径（不是 url 本身 —— 要带 token 去取） */
  imagePath: string
  /** 底部说明文字，比如「李小白 的三视图」 */
  title: string
  /** 变化时重新取图（重新生成后内容变了但路径没变） */
  cacheKey?: string | number
  onClose: () => void
}) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [onClose])

  return (
    <div
      className="lightbox"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <button className="lightbox-close" onClick={onClose} title="关闭（Esc）">
        ✕
      </button>
      {/* 点图片本身不要关掉 —— 用户可能在图片上停顿观察 */}
      <div className="lightbox-body" onClick={(event) => event.stopPropagation()}>
        <AuthMedia
          kind="image"
          path={imagePath}
          alt={title}
          className="lightbox-img"
          cacheKey={cacheKey}
        />
      </div>
      <div className="lightbox-caption">{title} · 点空白处或按 Esc 关闭</div>
    </div>
  )
}
