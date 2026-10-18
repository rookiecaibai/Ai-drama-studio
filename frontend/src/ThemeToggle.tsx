import { useState } from 'react'
import { applyTheme, currentTheme, type Theme } from './theme'

/**
 * 深色 / 浅色切换按钮。
 *
 * 放在顶部导航里：主题是"偏好设置"，属于全局，不该藏在某个短剧的设置里。
 * 状态初始化读的是 `<html>` 上的 data-theme（main.tsx 启动时已经设好了），
 * 所以这里不需要再读一次 localStorage。
 */
export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(() => currentTheme())

  function toggle() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    applyTheme(next)
    setTheme(next)
  }

  return (
    <button
      className="link theme-toggle"
      onClick={toggle}
      title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
    >
      {theme === 'dark' ? '☀️ 浅色' : '🌙 深色'}
    </button>
  )
}
