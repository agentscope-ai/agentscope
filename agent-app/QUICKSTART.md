# 快速开始指南

## 1. 环境准备

### 安装依赖
```bash
# 进入项目目录
cd agent-app

# 安装依赖
pip install -r requirements.txt

# 安装AgentScope (如果还没有安装)
cd ..
pip install -e .
cd agent-app
```

### 设置环境变量
创建 `.env` 文件：
```bash
# OpenAI API密钥
OPENAI_API_KEY=your_openai_api_key_here

# 数据库配置 (可选)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=knowledge_db
DB_USER=postgres
DB_PASSWORD=your_db_password

# 日志级别
LOG_LEVEL=INFO
```

## 2. 基础使用

### 运行知识收集演示
```bash
# 确保设置了OPENAI_API_KEY环境变量
export OPENAI_API_KEY="your_api_key"

# 运行演示
python examples/knowledge_collector_demo.py
```

### 基础API使用
```python
import asyncio
from agents.knowledge_collector import KnowledgeCollectorAgent

async def main():
    # 创建智能体
    collector = KnowledgeCollectorAgent()

    # 收集单个URL
    result = await collector.collect_from_url(
        url="https://example.com/article",
        categories=["技术", "学习"]
    )

    print(result)

if __name__ == "__main__":
    asyncio.run(main())
```

## 3. 目录结构说明

```
agent-app/
├── src/                    # 源代码
│   ├── agents/            # 智能体实现
│   ├── tools/             # 工具函数
│   ├── models/            # 数据模型
│   ├── utils/             # 工具函数
│   └── pipelines/         # 工作流编排
├── config/                # 配置文件
├── data/                  # 数据目录
│   ├── inbox/            # 收件箱
│   └── knowledge_base/   # 知识库
├── examples/             # 示例代码
└── tests/               # 测试代码
```

## 4. 核心功能

### 知识收集
- 从网页抓取文章
- 解析和提取关键信息
- 自动分类和打标签
- 批量处理URL

### 智能体协作
- 多智能体工作流
- 实时中断和控制
- 记忆管理
- 工具调用

### 数据存储
- 本地文件存储
- PostgreSQL支持 (开发中)
- 向量搜索 (开发中)

## 5. 配置说明

编辑 `config/app_config.json` 文件：

```json
{
  "openai": {
    "api_key": "your_api_key",
    "model_name": "gpt-4",
    "temperature": 0.7
  },
  "knowledge": {
    "inbox_path": "data/inbox",
    "knowledge_base_path": "data/knowledge_base",
    "auto_organize": true
  }
}
```

## 6. 下一步

1. 运行示例代码了解基本功能
2. 修改配置以适应你的需求
3. 开始构建你自己的知识管理Agent
4. 探索多智能体协作功能

## 7. 常见问题

**Q: 如何更换模型？**
A: 修改配置文件中的 `model_name` 字段，支持 GPT-4、Claude、通义千问等。

**Q: 数据保存在哪里？**
A: 默认保存在 `data/` 目录下，包含收件箱和知识库。

**Q: 如何添加自定义工具？**
A: 在 `src/tools/` 目录下创建新的工具函数，并使用 `@tool` 装饰器注册。

**Q: 支持哪些语言？**
A: 主要支持中文和英文，可以处理多种语言的网页内容。

## 8. 开发进度

- [x] 基础项目结构
- [x] 知识收集Agent
- [x] 网页抓取工具
- [x] 文件存储功能
- [ ] PostgreSQL数据库集成
- [ ] 多智能体协作
- [ ] 长期记忆系统
- [ ] Web浏览器插件
- [ ] 移动端支持