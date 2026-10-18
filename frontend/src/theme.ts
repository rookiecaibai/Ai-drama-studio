/**
 * 主题（深色 / 浅色）—— 用户自己选，选择记在 localStorage 里。
 *
 * 为什么不用 CSS 的 `prefers-color-scheme` 一家独大：
 * 系统偏好只是"默认值"，很多人白天想要浅色、晚上想要深色，
 * 而系统设置不一定跟着变。所以做成**显式选择 > 系统偏好 > 深色**。
 *
 * 实现上只做一件事：给 `<html>` 打一个 `data-theme` 属性，
 * 所有颜色都走 CSS 变量（`:root` 和 `:root[data-theme="light"]` 两套值）。
 * 好处是切换主题**不需要 React 重渲染**任何样式，也没有闪白。
 */

export type Theme = 'dark' | 'light'

const STORAGE_KEY = 'ai_drama_theme'

export function readStoredTheme(): Theme | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw === 'light' || raw === 'dark' ? raw : null
  } catch {
    // 隐私模式下 localStorage 可能直接抛错 —— 不能因为记不住主题就让整个应用挂掉
    return null
  }
}

export function detectSystemTheme(): Theme {
  if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  }
  return 'dark'
}

export function applyTheme(theme: Theme, persist = true): void {
  document.documentElement.dataset.theme = theme
  // colorScheme 会让浏览器把滚动条、下拉框、日期选择器这些原生控件也切成对应明暗
  document.documentElement.style.colorScheme = theme
  if (persist) {
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch {
      /* 记不住就记不住，不影响使用 */
    }
  }
}

/** 启动时调用一次：优先用户上次的选择，否则跟随系统偏好。 */
export function initTheme(): Theme {
  const theme = readStoredTheme() ?? detectSystemTheme()
  applyTheme(theme, false)
  return theme
}

/** 读当前生效的主题（组件里初始化 state 用，避免重复读 localStorage）。 */
export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'
}
