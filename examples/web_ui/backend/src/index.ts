import express, { type Request, type Response } from 'express';
import cors from 'cors';
import { createProxyMiddleware } from 'http-proxy-middleware';

const app = express();
const PORT = process.env.PORT || 3000;

/**
 * 后端 Agent Service 地址，默认指向容器网络内的 agentscope-service
 */
const AGENTSCOPE_API_URL =
	process.env.AGENTSCOPE_API_URL || 'http://localhost:8000';

app.use(cors());
app.use(express.json());

// ---------------------------------------------------------------------------
// 健康检查（不走代理）
// ---------------------------------------------------------------------------
app.get('/api/health', (_req, res) => {
	res.json({ status: 'ok' });
});

// ---------------------------------------------------------------------------
// 反向代理：/api/* → AgentScope Service
//
// 路径重写：剥离 /api 前缀（AgentScope FastAPI 路由无 /api 前缀）
// SSE 透传：changeOrigin + 流式响应，不缓冲 body
//
// 如需启用代理，取消下方注释即可；也可按需拆分为多个 createProxyMiddleware
// 分别代理不同路径（例如 /api/agent/* → Agent Service，/api/rag/* → RAG Service）
// ---------------------------------------------------------------------------
const proxyMiddleware = createProxyMiddleware({
	target: AGENTSCOPE_API_URL,
	changeOrigin: true,
	// 剥离 /api 前缀：/api/credential/schemas → /credential/schemas
	pathRewrite: { '^/api': '' },
	// SSE / 流式响应透传：不对响应体做任何缓冲或解析
	selfHandleResponse: false,
	// 代理出错时的兜底处理
	onError: (err: Error, _req: Request, res: Response) => {
		console.error('[Proxy Error]', err.message);
		if (!res.headersSent) {
			res.status(502).json({
				error: 'Bad Gateway webui backend',
				detail: err.message,
			});
		}
	},
	// 代理日志（按需开启）
	// onProxyReq: (proxyReq, req, res) => {
	// 	console.log(`[Proxy] ${req.method} ${req.url} → ${AGENTSCOPE_API_URL}`);
	// },
});

// 启用代理路由（取消注释即生效）
// app.use('/api', proxyMiddleware);

app.listen(PORT, () => {
	console.log(`Server running on http://localhost:${PORT}`);
	console.log(`Proxy target: ${AGENTSCOPE_API_URL} (currently disabled, uncomment to enable)`);
});
