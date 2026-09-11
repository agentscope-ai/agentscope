import path from 'path';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';
import svgr from 'vite-plugin-svgr';

const ELECTRON_CSP = [
	"default-src 'self'",
	"base-uri 'none'",
	"object-src 'none'",
	"form-action 'none'",
	"script-src 'self'",
	"style-src 'self' 'unsafe-inline'",
	"font-src 'self' data:",
	"img-src 'self' data: blob: https: http://127.0.0.1:*",
	"media-src 'self' data: blob: https: http://127.0.0.1:*",
	// The main process intersects these fallbacks with an exact-port policy.
	"connect-src 'self' http://127.0.0.1:*",
	'frame-src blob: http://127.0.0.1:*',
	"worker-src 'self' blob:",
].join('; ');

function electronCspPlugin(): Plugin {
	return {
		name: 'electron-content-security-policy',
		transformIndexHtml: {
			order: 'pre',
			handler: () => [
				{
					tag: 'meta',
					attrs: {
						'http-equiv': 'Content-Security-Policy',
						content: ELECTRON_CSP,
					},
					injectTo: 'head-prepend',
				},
			],
		},
	};
}

export default defineConfig(({ mode }) => {
	const isElectron = mode === 'electron';
	return {
		plugins: [react(), tailwindcss(), svgr(), ...(isElectron ? [electronCspPlugin()] : [])],
		base: isElectron ? './' : undefined,
		server: {
			port: 5173,
			strictPort: true,
			proxy: {
				'/api': 'http://localhost:3000',
			},
		},
		build: isElectron
			? {
					outDir: '../../../apps/desktop/electron-dist/frontend-dist',
					emptyOutDir: true,
				}
			: undefined,
		resolve: {
			alias: {
				'@': path.resolve(__dirname, './src'),
				'next/navigation': path.resolve(__dirname, './src/lib/next-navigation-shim.ts'),
			},
		},
		optimizeDeps: {
			include: ['mime-types'],
		},
	};
});
