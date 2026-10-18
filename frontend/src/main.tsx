import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './style.css'
import { initTheme } from './theme'

// 在渲染之前先把主题挂到 <html> 上：
// 这样第一帧就是正确的颜色，不会出现"先白底再变深色"的闪烁
initTheme()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
