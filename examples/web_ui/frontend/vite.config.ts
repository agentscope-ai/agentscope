import path from 'path';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';
import svgr from 'vite-plugin-svgr';

export default defineConfig(({ mode }) => {
	const env = loadEnv(mode, process.cwd(), '');
	// 后端地址：默认 localhost:8000（Agent Service 本地直跑）；
	// 容器内运行时经 VITE_API_BASE_URL 注入（如 http://agentscope-service:8000）。
	const backendUrl = env.VITE_API_BASE_URL || 'http://localhost:8000';

	return {
		plugins: [react(), tailwindcss(), svgr()],
		server: {
			proxy: {
				'/api': backendUrl,
			},
		},
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
