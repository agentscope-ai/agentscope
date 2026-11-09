import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import './index.css';
import './i18n';
import App from './App.tsx';
import { TooltipProvider } from '@/components/ui/tooltip.tsx';

createRoot(document.getElementById('root')!).render(
	<StrictMode>
		<TooltipProvider>
			<App />
		</TooltipProvider>
	</StrictMode>,
);
