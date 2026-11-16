# TTS 模型发布日期排序

## 已确认方案

TTS 与 Chat、Realtime 使用相同的发布日期规则：供应商和凭证分组内按
`release_date` 倒序排列，空日期置后，同日期按模型名称自然倒序。

后端使用可选 `date` 字段，从 YAML 加载，API 输出 `YYYY-MM-DD` 或
`null`。旧配置无需修改。日期采用具体模型 ID 在提供商 API 首次可用的日期，
仅填写官方来源可确认的值；无法确认的省略字段，不从名称猜测日期。

## Checklist

- [x] 新增 TTSModelCard 日期字段及 YAML 加载。
- [x] 补齐前端类型并按日期排序。
- [x] 核实 25 份模型配置，补充可确认的日期和来源。
- [x] 添加日期加载及 API 序列化单测。
- [x] 在 agentscope2 运行相关后端测试及格式检查。
- [x] 完成前端类型、静态检查及最终 diff 检查。

## 日期核实记录

已核实 25 份配置，其中 23 份补充日期和官方来源注释。

| 模型 ID | release_date | 官方来源 |
| --- | --- | --- |
| `tts-1`、`tts-1-hd` | 2023-11-06 | [OpenAI DevDay](https://openai.com/index/new-models-and-developer-products-announced-at-devday/) |
| `gpt-4o-mini-tts` | 2025-03-20 | [OpenAI 音频模型公告](https://openai.com/index/introducing-our-next-generation-audio-models/) |
| `gemini-2.5-flash-preview-tts`、`gemini-2.5-pro-preview-tts` | 2025-05-20 | [Gemini API 更新日志](https://ai.google.dev/gemini-api/docs/changelog) |
| `gemini-3.8-flash-tts`、`gemini-3.8-flash-lite-tts` | 2026-09-22 | [Gemini API 更新日志](https://ai.google.dev/gemini-api/docs/changelog) |
| `speech-02-hd`、`speech-02-turbo` | 2025-04-02 | [MiniMax API 更新日志](https://platform.minimax.io/docs/release-notes/models) |
| `speech-2.6-hd`、`speech-2.6-turbo` | 2025-10-29 | [MiniMax API 更新日志](https://platform.minimax.io/docs/release-notes/models) |
| `speech-2.8-hd`、`speech-2.8-turbo` | 2026-01-23 | [MiniMax API 更新日志](https://platform.minimax.io/docs/release-notes/models) |
| `qwen3-tts-flash`、`qwen3-tts-flash-realtime` | 2025-11-27 | [百炼模型上架记录](https://help.aliyun.com/zh/model-studio/newly-released-models) |
| `cosyvoice-v3-plus` | 2025-09-03 | [百炼模型上架记录](https://help.aliyun.com/zh/model-studio/newly-released-models) |
| `cosyvoice-v3-flash` | 2025-11-17 | [百炼模型上架记录](https://help.aliyun.com/zh/model-studio/newly-released-models) |
| `cosyvoice-v3.5-plus`、`cosyvoice-v3.5-flash` | 2026-02-27 | [百炼模型上架记录](https://help.aliyun.com/zh/model-studio/newly-released-models) |
| `qwen-audio-3.0-tts-plus`、`qwen-audio-3.0-tts-flash` | 2026-07-14 | [百炼模型上架记录](https://help.aliyun.com/zh/model-studio/newly-released-models) |
| `qwen-audio-3.1-tts-flash` | 2026-09-18 | [百炼模型上架记录](https://help.aliyun.com/zh/model-studio/newly-released-models) |
| `qwen-audio-3.1-tts-plus` | 2026-10-21 | [百炼模型上架记录](https://help.aliyun.com/zh/model-studio/newly-released-models) |

百炼沿用聊天模型的北京地域上架日期口径。Qwen3 TTS 无日期后缀 ID
采用上架记录中明确列出该 ID 的 2025-11-27，不套用更早的日期后缀快照记录。
Gemini 3.8 TTS 与 MiniMax Speech 2.6 采用 API 更新日志日期，分别比
博客公告（2026-09-23、2025-10-30）早一天。

`speech-01-hd`、`speech-01-turbo` 暂不填写：官方 API 文档列出了这两个
ID，但已核查的官方更新日志没有明确其首次 API 上线日期。字段省略，
加载和 API 输出均为 `null`。

## 验证结果

- `agentscope2`：新增模型卡测试以及 OpenAI、Gemini、MiniMax、DashScope
  TTS 和 voice profile 回归测试全部通过：172 passed、365 subtests passed。
  覆盖日期缺省、显式 null、带引号及不带引号的日期、无效日期拒绝，
  并对完整模型卡及列表 API 响应进行字典断言。
- 25 份 YAML 均通过凭证层加载，字段值与配置一致，JSON 日期输出验证通过。
- 前端 `tsc -b --pretty false` 和修改文件的 ESLint 检查通过。
- Python AST、79 字符行长、Flake8、仓库锁定版本的 Black 和 Mypy 检查通过。
  Black/Mypy 使用本机已缓存的 pre-commit 依赖，在 agentscope2 中运行。
- `git diff --check` 通过。测试仅有既有依赖弃用警告，无失败。
