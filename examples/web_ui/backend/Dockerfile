# Web UI 后端服务镜像（多阶段：dev 热更新开发服务器 / prod 编译 + node 运行）
# 构建上下文为本目录（examples/web_ui/backend）：
#   生产：docker build -t webui-backend examples/web_ui/backend
#   开发：docker build --target dev -t webui-backend:dev examples/web_ui/backend

# 基础阶段：安装依赖（dev/prod 共用，利用缓存）
FROM node:20-bookworm AS base

WORKDIR /app

# 复制 package 文件安装依赖（含 devDependencies：dev 需 ts-node/nodemon，build 需 tsc）
COPY package.json ./
RUN npm install

# 开发阶段：nodemon + ts-node 热更新
FROM base AS dev

# 复制源码（运行时可通过 volume 挂载覆盖，实现改码即重载）
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev"]

# 构建阶段：编译 TypeScript
FROM base AS build

COPY . .
RUN npm run build

# 生产阶段：仅运行编译产物
FROM node:20-bookworm AS prod

WORKDIR /app

# 只装生产依赖
COPY package.json ./
RUN npm install --omit=dev

# 复制编译产物
COPY --from=build /app/dist ./dist

EXPOSE 3000

CMD ["node", "dist/index.js"]
