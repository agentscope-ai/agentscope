# Web UI 前端镜像（多阶段：dev 开发服务器 / prod 构建 + nginx 托管）
# 构建上下文为本目录（examples/web_ui/frontend）：
#   生产：docker build -t webui-frontend examples/web_ui/frontend
#   开发：docker build --target dev -t webui-frontend:dev examples/web_ui/frontend
# 可通过 --build-arg 注入 API 路径前缀（如 --build-arg VITE_API_PREFIX=/api）

# 基础阶段：安装依赖（dev/prod 共用，利用缓存）
FROM node:20-bookworm AS base

RUN corepack enable && corepack prepare pnpm@10 --activate

WORKDIR /app

# npm 镜像源（可通过 --build-arg NPM_REGISTRY=... 覆盖）
ARG NPM_REGISTRY=https://registry.npmmirror.com

# 复制 package 文件安装依赖
# 若目录中已有 pnpm-lock.yaml，可改为 COPY package.json pnpm-lock.yaml ./ 并
# 将下行 RUN 替换为 pnpm install --frozen-lockfile 以获得可复现构建
COPY package.json ./
RUN pnpm install --registry=$NPM_REGISTRY

# 开发阶段：vite dev server（热更新）
FROM base AS dev

# 复制源码（运行时可通过 volume 挂载覆盖，实现改码即热更）
COPY . .

# vite dev 代理目标（容器网络内经 Docker DNS 解析到后端服务）
ENV VITE_API_BASE_URL=http://webui-backend:3000

EXPOSE 5173

# --host 0.0.0.0 使容器外（端口映射/网关代理）可访问
CMD ["pnpm", "dev", "--host", "0.0.0.0"]

# 构建阶段：产出静态文件
FROM base AS build

# 构建时注入 API 路径前缀（相对路径时经由 nginx 网关代理）
ARG VITE_API_PREFIX=/api
ENV VITE_API_PREFIX=$VITE_API_PREFIX

COPY . .
RUN pnpm build

# 生产阶段：nginx:alpine 托管静态文件
FROM nginx:alpine AS prod

# 复制构建产物
COPY --from=build /app/dist /usr/share/nginx/html

# 复制自定义 nginx 配置（SPA history 路由回退）
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
