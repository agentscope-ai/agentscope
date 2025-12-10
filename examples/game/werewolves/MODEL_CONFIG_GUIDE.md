# 统一模型配置管理指南

## 概述

现在所有模型配置都统一通过环境变量进行管理，支持以下功能：
- 统一配置API密钥和模型名称
- 支持通过`.env`文件管理配置
- 提供配置验证和错误处理
- 支持默认值和自定义配置

## 使用方法

### 1. 配置环境变量

编辑`.env`文件：
```bash
# DashScope API Configuration
DASHSCOPE_API_KEY=your_api_key_here

# Model Configuration
DASHSCOPE_MODEL_NAME=qwen3-next-80b-a3b-instruct
```

### 2. 支持的模型

可用的模型名称包括：
- `qwen3-next-80b-a3b-instruct` (默认)
- `qwen3-max-2025-09-23`
- `qwen-turbo`
- `qwen-plus`
- `qwen-max`

完整模型列表请参考：[DashScope模型文档](https://help.aliyun.com/document_detail/2712534.html)

### 3. 在代码中使用

```python
from model_config import ModelConfig

# 获取模型名称
model_name = ModelConfig.get_model_name()

# 获取API密钥
api_key = ModelConfig.get_api_key()

# 获取完整配置
config = ModelConfig.get_model_config()

# 验证配置
is_valid = ModelConfig.validate_config()
```

### 4. 修改模型配置

要更改使用的模型，只需修改`.env`文件中的`DASHSCOPE_MODEL_NAME`值：

```bash
# 切换到qwen3-max模型
DASHSCOPE_MODEL_NAME=qwen3-max-2025-09-23
```

修改后，所有使用`ModelConfig.get_model_name()`的地方都会自动使用新的模型配置。

## 验证配置

运行验证脚本检查配置是否正确：
```bash
py verify_config.py
```

## 文件结构

- `model_config.py` - 统一配置管理模块
- `.env` - 环境变量配置文件
- `verify_config.py` - 配置验证脚本
- `main.py`, `agent.py`, `agent_with_real_model.py` - 已更新为使用统一配置

## 注意事项

1. 确保`.env`文件中的API密钥正确设置
2. 模型名称必须是DashScope支持的模型
3. 修改配置后无需重启，配置会实时生效
4. 如果未设置环境变量，会使用默认值`qwen3-next-80b-a3b-instruct`