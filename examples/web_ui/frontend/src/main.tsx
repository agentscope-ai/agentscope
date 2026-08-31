import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import './index.css';
import './i18n';
import App from './App.tsx';
import { TooltipProvider } from '@/components/ui/tooltip.tsx';

// Follow the OS color-scheme preference: every dark token in index.css lives
// under a `.dark` class that nothing ever adds, while `:root` advertises
// `color-scheme: light dark`. Browsers with a built-in dark mode (Firefox /
// 360) then skip their full inversion but still render the light tokens, so
// the sidebar buttons — whose lucide icons inherit `currentColor` from
// `--sidebar-foreground` — vanish against the semi-transparent white rail
// (#2467). Toggling `.dark` here activates the real dark token set, which is
// exactly the "buttons visible" state, and keeps it in sync with the OS.
const darkModeQuery = window.matchMedia('(prefers-color-scheme: dark)');
const applySystemTheme = () => {
	document.documentElement.classList.toggle('dark', darkModeQuery.matches);
};
applySystemTheme();
darkModeQuery.addEventListener('change', applySystemTheme);

createRoot(document.getElementById('root')!).render(
	<StrictMode>
		<TooltipProvider>
			<App />
		</TooltipProvider>
	</StrictMode>,
);
