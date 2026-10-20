import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/schibsted-grotesk/wght.css'
import '@fontsource/azeret-mono/latin-400.css'
import '@fontsource/azeret-mono/latin-500.css'
import '@fontsource/azeret-mono/latin-600.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
