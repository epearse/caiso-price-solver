import { createContext, useContext, useEffect, useState } from 'react';
import type { ReactNode } from 'react';

export type Theme = 'light' | 'dark';
type Setting = Theme | 'system';

interface Ctx { theme: Theme; setting: Setting; setSetting: (s: Setting) => void }
const ThemeCtx = createContext<Ctx>({ theme: 'dark', setting: 'system', setSetting: () => {} });

const KEY = 'caiso-theme';

function systemTheme(): Theme {
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [setting, setSetting] = useState<Setting>(() => {
    try { return (localStorage.getItem(KEY) as Setting) || 'system'; } catch { return 'system'; }
  });
  const [sysTheme, setSysTheme] = useState<Theme>(() =>
    typeof window === 'undefined' ? 'dark' : systemTheme());

  // Track the OS setting so 'system' stays live rather than sampled once at mount.
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const on = () => setSysTheme(systemTheme());
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);

  const theme: Theme = setting === 'system' ? sysTheme : setting;

  useEffect(() => {
    // An explicit choice stamps the root; 'system' leaves it bare so the media
    // query decides. Both are covered in the stylesheet.
    const root = document.documentElement;
    if (setting === 'system') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', setting);
    try { localStorage.setItem(KEY, setting); } catch { /* private mode */ }
  }, [setting]);

  return <ThemeCtx.Provider value={{ theme, setting, setSetting }}>{children}</ThemeCtx.Provider>;
}

export const useTheme = () => useContext(ThemeCtx);
