import { createRequire } from 'node:module';
import path from 'path';

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, normalizePath } from 'vite';
import { viteStaticCopy } from 'vite-plugin-static-copy';
import svgr from 'vite-plugin-svgr';

const require = createRequire(import.meta.url);
const vadRoot = path.dirname(require.resolve('@ricky0123/vad-web'));
const ortRoot = path.dirname(
	createRequire(require.resolve('@ricky0123/vad-web')).resolve('onnxruntime-web'),
);

export default defineConfig({
	plugins: [
		react(),
		tailwindcss(),
		svgr(),
		viteStaticCopy({
			targets: [
				{
					src: normalizePath(path.join(vadRoot, 'vad.worklet.bundle.min.js')),
					dest: 'voice',
					rename: { stripBase: true },
				},
				{
					src: normalizePath(path.join(vadRoot, 'silero_vad_v5.onnx')),
					dest: 'voice',
					rename: { stripBase: true },
				},
				{
					src: normalizePath(path.join(ortRoot, 'ort-wasm-simd-threaded.{mjs,wasm}')),
					dest: 'voice',
					rename: { stripBase: true },
				},
			],
		}),
	],
	server: {
		proxy: {
			'/api': 'http://localhost:3000',
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
});
