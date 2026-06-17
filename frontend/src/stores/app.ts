import { create } from 'zustand';

type MenuModeOverride = 'server' | 'full' | 'personal';

const MENU_MODE_KEY = 'fundquant.menu_mode_override';
const AI_VISIBLE_KEY = 'fundquant.ai_menu_visible';

function loadMenuModeOverride(): MenuModeOverride {
  const value = localStorage.getItem(MENU_MODE_KEY);
  return value === 'full' || value === 'personal' ? value : 'server';
}

function loadAiVisibleOverride(): boolean | null {
  const value = localStorage.getItem(AI_VISIBLE_KEY);
  if (value === 'true') return true;
  if (value === 'false') return false;
  return null;
}

interface AppState {
  /** Whether the sidebar is collapsed */
  sidebarCollapsed: boolean;
  /** Local override for sidebar feature visibility */
  menuModeOverride: MenuModeOverride;
  /** Local override for AI menu visibility; null means follow server */
  aiMenuVisibleOverride: boolean | null;
  /** Toggle sidebar collapsed state */
  toggleSidebar: () => void;
  /** Set sidebar collapsed state */
  setSidebarCollapsed: (collapsed: boolean) => void;
  setMenuModeOverride: (mode: MenuModeOverride) => void;
  setAiMenuVisibleOverride: (visible: boolean | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  sidebarCollapsed: false,
  menuModeOverride: loadMenuModeOverride(),
  aiMenuVisibleOverride: loadAiVisibleOverride(),
  toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
  setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
  setMenuModeOverride: (mode) => {
    localStorage.setItem(MENU_MODE_KEY, mode);
    set({ menuModeOverride: mode });
  },
  setAiMenuVisibleOverride: (visible) => {
    if (visible === null) {
      localStorage.removeItem(AI_VISIBLE_KEY);
    } else {
      localStorage.setItem(AI_VISIBLE_KEY, String(visible));
    }
    set({ aiMenuVisibleOverride: visible });
  },
}));
