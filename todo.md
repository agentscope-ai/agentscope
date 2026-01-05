# todo: 001-absorb-main-rag-tierA

目标：从本地 `main` 选择性吸收低破坏/低难能力到 `easy`（A1+A2+A3）：
- A1: `py.typed`（PEP 561 typed package marker）
- A2: RAG `MilvusLiteStore`（Milvus Lite/Server 后端）
- A3: RAG `WordReader`（`.docx` reader，默认不抽取图片）

## 规范 → 不变量 → 测试 → 示例 → 代码（链路）

### 规范（spec）
- `specs/001-absorb-main-rag-tierA/spec.md`
- `specs/001-absorb-main-rag-tierA/plan.md`
- `specs/001-absorb-main-rag-tierA/tasks.md`

### 不变量（必须满足）
- 导出路径：`from agentscope.rag import WordReader, MilvusLiteStore` 必须成功（即使可选依赖缺失）。
- 可选依赖：缺少 `python-docx` / `pymilvus` 时，`import agentscope` / `import agentscope.rag` 不得失败；仅在运行时使用功能时抛出带安装提示的 `ImportError`。
- extras 策略：仅扩展现有 `agentscope[full]`（不新增 extras key）。
- Windows：`pymilvus[milvus_lite]` 必须用 `platform_system != "Windows"` marker，避免 Windows 安装 `.[dev]` 失败。
- `WordReader` 默认 `include_image=False`；启用后会产生 base64 图片块并要求多模态 embedding（此行为应在文档中明确）。

### 测试（必须可重复执行）
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/rag_reader_test.py tests/rag_store_test.py`
- `./.venv/bin/python -m ruff check src`

覆盖点：
- `py.typed` 文件存在（用于 typed package 识别）。
- `agentscope.rag` 导入不应 eager-import `docx` / `pymilvus`（契约测试）。
- `WordReader` 最小功能测试：pytest 运行时生成 `.docx`（无二进制 fixture），`WordReader(include_image=False)` 能解析出 `TextBlock`。
- `MilvusLiteStore` 至少通过“导出 + 子类契约”测试（不要求连接外部 Milvus）。

### 示例（本次不新增）
- 本次为低风险吸收，不新增 examples/tutorial；若后续需要，可单独开 feature 补充。

### 代码（落点）
- `src/agentscope/py.typed`
- `src/agentscope/rag/_reader/_word_reader.py`
- `src/agentscope/rag/_store/_milvuslite_store.py`
- `src/agentscope/rag/_reader/__init__.py`
- `src/agentscope/rag/_store/__init__.py`
- `src/agentscope/rag/__init__.py`
- `setup.py`
- `tests/rag_reader_test.py`
- `tests/rag_store_test.py`

