import express from 'express';
import cors from 'cors';

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

// ---------------------------------------------------------------------------
// 健康检查
// ---------------------------------------------------------------------------
app.get('/api/health', (_req, res) => {
	res.json({ status: 'ok' });
});

// ---------------------------------------------------------------------------
// 说明：
// 本服务在容器部署中仅作为可选代理层。前端到后端 Agent Service 的请求实际
// 由 nginx 网关统一转发（见 Docker-agentscope/nginx/nginx.conf），因此这里
// 不再内置 http-proxy-middleware。
// 若需要在纯 Node 环境下代理 /api/* → Agent Service，可自行引入并挂载
// createProxyMiddleware。
// ---------------------------------------------------------------------------

app.listen(PORT, () => {
	console.log(`Server running on http://localhost:${PORT}`);
});
