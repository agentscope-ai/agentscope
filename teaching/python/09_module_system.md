# 09 - Python 模块系统

## 概述

本教程从**入门到专家**全面讲解 Python 模块与包的核心机制，涵盖从基础的模块创建、导入系统，到高级的自定义导入钩子、字节码缓存、插件架构，再到专家级的 C 层实现、导入锁机制等主题。

**前置知识：** 本教程面向已掌握 Python 基础语法的开发者。不假设 Java 或其他语言背景。

**学习目标：**

- 理解 Python 模块与包的概念
- 掌握导入机制的底层原理
- 理解字节码缓存和 `__pycache__` 的工作原理
- 能够设计良好的项目结构
- 识别并解决循环导入问题
- 实现自定义导入钩子（从 ZIP、数据库等加载）
- 构建插件系统
- 应用最佳实践

---

## 与其他教程的对比

| 教程资源 | 覆盖广度 | 覆盖深度 | 特色 |
|----------|:--------:|:--------:|------|
| **本教程（AgentScope）** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 27章节 + 7附录，含 C 层实现、导入锁、插件架构、供应链安全 |
| [Real Python: Import](https://realpython.com/python-import/) | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 实战驱动，代码示例丰富 |
| [Chris Yeh: Definitive Guide](https://chrisyeh96.github.io/2017/08/08/definitive-guide-python-imports.html) | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ | Python 2/3 兼容详细解释 |
| [Python 官方文档](https://docs.python.org/3/reference/import.html) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 权威参考，但缺乏引导性 |
| [PEP 302/420/451](https://peps.python.org/) | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 底层设计文档 |

**本教程独特内容：**
- **第 26 章**：C 层实现（`_gcd_import`、`IMPORT_NAME` 字节码）
- **第 16 章**：导入锁与线程安全（死锁分析）
- **第 19 章**：Frozen 模块的引导机制
- **第 22 章**：`zipimport` ZIP 文件导入深度剖析
- **第 21 章**：隔离插件执行的进程级方案
- **第 14 章**：**PEP 810 已批准！** PEP 690/810 延迟导入演进详解（Python 3.15 lazy import）
- **第 27 章**：供应链安全（typosquatting、dependency confusion）全球首创
- AgentScope 源码对照（贴近工业级实践）

---

## 学习路径建议

```
入门（1-5 章）
  └─ 理解模块、包、导入语句、搜索路径

进阶（6-10 章）
  └─ 掌握 __all__、循环导入解决、项目布局、常见问题

高级（11-15 章）
  └─ 字节码缓存、自定义导入钩子、importlib.metadata

专家（16-21 章）
  └─ 导入锁、sys.modules 测试隔离、__path__、Frozen、__main__

架构（22-27 章）
  └─ zipimport、pkgutil、调试技术、importlib.util、C 层、供应链安全
```

---

## 目录

1. [模块基础](#1-模块基础)
2. [导入机制](#2-导入机制)
3. [包与 `__init__.py`](#3-包与-__init__py)
4. [绝对导入 vs 相对导入](#4-绝对导入-vs-相对导入)
5. [模块搜索路径](#5-模块搜索路径)
6. [`__all__` 控制公共API](#6-__all__-控制公共api)
7. [循环导入：问题与解决方案](#7-循环导入问题与解决方案)
8. [项目布局最佳实践](#8-项目布局最佳实践)
9. [实战示例](#9-实战示例)
10. [常见问题速查表](#10-常见问题速查表)
11. [字节码缓存 (`__pycache__`)](#11-字节码缓存-__pycache__)
12. [自定义导入钩子](#12-自定义导入钩子)
13. [importlib.metadata 与 importlib.resources](#13-importlibmetadata-与-importlibresources)
14. [模块生命周期与卸载](#14-模块生命周期与卸载)
    - [延迟导入的演进：PEP 690 vs PEP 810](#延迟导入的演进pep-690-vs-pep-810)
15. [模块全局状态与进程隔离](#15-模块全局状态与进程隔离)
16. [导入锁与线程安全](#16-导入锁与线程安全)
17. [sys.modules 操作与测试隔离](#17-sysmodules-操作与测试隔离)
18. [模块 `__path__` 属性深度剖析](#18-模块-__path__-属性深度剖析)
19. [Frozen 模块](#19-frozen-模块)
20. [`__main__` 与 `sys.modules` 的关系](#20-__main__-与-sysmodules-的关系)
21. [高级插件系统架构](#21-高级插件系统架构)
22. [zipimport：ZIP 文件导入](#22-zipimportzip-文件导入)
23. [pkgutil 与包发现](#23-pkgutil-与包发现)
24. [导入调试技术](#24-导入调试技术)
25. [importlib.util 高级用法](#25-importlibutil-高级用法)
26. [导入系统的 C 层实现](#26-导入系统的-c-层实现)
27. [包供应链安全： typosquatting 与 dependency confusion](#27-包供应链安全-typosquatting-与-dependency-confusion)

---

## 1. 模块基础

### 什么是模块

**模块**是一个 `.py` 文件，包含 Python 代码定义（函数、类、变量）和语句。

```python
# mymodule.py
def greet(name: str) -> str:
    return f"Hello, {name}"

class Calculator:
    def add(self, a: int, b: int) -> int:
        return a + b

PI = 3.14159
```

导入和使用：

```python
import mymodule

result = mymodule.greet("Alice")
calc = mymodule.Calculator()
calc.add(1, 2)  # 3
```

`★ Insight ─────────────────────────────────────`
- 每个 `.py` 文件就是一个模块
- 模块是 Python 代码组织的基本单元
- 模块可以被其他模块导入复用
`─────────────────────────────────────────────────`

### 标准库模块

Python 内置了丰富的标准库模块：

```python
import math
import random
import datetime
import os
import sys
import json

math.pi              # 3.141592653589793
random.randint(1, 10)  # 随机整数
datetime.datetime.now()  # 当前时间
os.getcwd()          # 当前工作目录
sys.version          # Python 版本
```

### 内置模块（Built-in Modules）

部分模块由 C 编写，与标准库不同：

```python
import sys    # 解释器相关
import cmath  # 复数数学
```

### `dir()` 函数

`dir()` 列出模块中定义的所有名称：

```python
import math

print(dir(math))
# ['__doc__', '__file__', '__loader__', '__name__', ...,
#  'acos', 'asin', 'atan', 'ceil', 'cos', 'e', 'exp', ...]
```

### `__name__` 属性

每个模块都有 `__name__` 属性：

```python
# 当模块被直接运行时
# python mymodule.py
print(__name__)  # "__main__"

# 当模块被导入时
# import mymodule
print(__name__)  # "mymodule"
```

利用 `__name__` 可以让模块在被直接运行时执行测试代码：

```python
# mymodule.py
def add(a, b):
    return a + b

if __name__ == "__main__":
    # 仅在直接运行时执行
    print("Running tests...")
    assert add(1, 2) == 3
    print("All tests passed!")
```

`★ Insight ─────────────────────────────────────`
- `__name__ == "__main__"` 是 Python 常见的条件执行模式
- 用于区分"直接运行"和"被导入"两种场景
- 测试代码通常放在这个条件块内
`─────────────────────────────────────────────────`

### `__main__.py` 与可运行包

包可以包含 `__main__.py` 文件，使其可以通过 `python -m` 直接运行：

```
mypackage/
├── __init__.py
├── __main__.py    # 关键：使包可运行
├── module.py
└── subpackage/
    ├── __init__.py
    └── __main__.py
```

```python
# __main__.py
"""包的入口点"""
from .module import main

if __name__ == "__main__":
    main()
```

运行方式：

```bash
# 方式 1：通过包运行
python -m mypackage

# 方式 2：直接运行（如果包已安装或 PYTHONPATH 正确）
python mypackage
```

**实际案例：** AgentScope 的运行方式：

```bash
python -m agentscope
```

对应源码结构：
```
src/agentscope/
├── __init__.py
├── __main__.py    # 入口点
├── agent/
├── model/
└── ...
```

`★ Insight ─────────────────────────────────────`
- `__main__.py` 让包可以作为脚本运行
- 始终使用 `python -m` 运行包，而不是 `python path/to/__main__.py`
- 这是 Python 包的惯用运行方式
`─────────────────────────────────────────────────`

---

## 2. 导入机制

### 导入语句形式

Python 提供多种导入形式：

```python
# 形式 1: 导入整个模块
import math

# 形式 2: 导入特定名称
from math import pi, sqrt

# 形式 3: 导入时指定别名
from math import sqrt as square_root

# 形式 4: 导入模块内所有名称（不推荐）
from math import *

# 形式 5: 相对导入（仅在包内使用）
from . import sibling_module
from .. import parent_module
```

### 导入的执行过程

当执行 `import mymodule` 时，Python 内部按以下步骤进行：

```
1. 在 sys.modules 中查找 "mymodule"
   - 找到 → 直接返回已缓存的模块对象
   - 未找到 → 继续下一步

2. 查找模块文件
   - 按 sys.path 中的目录顺序搜索
   - 依次尝试 .py 文件、目录（包）、.so/.pyd 扩展模块

3. 加载模块
   - 读取源代码
   - 创建模块对象
   - 执行模块顶层代码（定义函数、类、变量）
   - 将模块存入 sys.modules

4. 在当前命名空间中创建引用
   - import math → 当前命名空间有 "math" 指向模块对象
   - from math import pi → 当前命名空间有 "pi" 指向具体对象
```

### 导入链与 `sys.modules`

`sys.modules` 是模块缓存字典，键为模块名，值为模块对象：

```python
import sys

print(len(sys.modules))  # 已加载的模块数量
print('math' in sys.modules)  # 检查某模块是否已加载

# 手动清除缓存（会导致重新加载）
# del sys.modules['mymodule']
```

### 包的多层导入

导入带点的名称时，Python 逐级导入：

```python
import numpy.linalg.norm

# 等价于：
# 1. import numpy
# 2. import numpy.linalg
# 3. import numpy.linalg.norm
```

导入 `numpy.linalg.norm` 会自动触发 `numpy` 和 `numpy.linalg` 的导入。

### 导入流程图

```
┌─────────────────────────────────────────────────────────────┐
│                      import mymodule                        │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  1. 检查 sys.modules                                        │
│     ┌─────────────────┐    ┌─────────────────┐           │
│     │ 模块已存在?      │───▶│ 是 │ 返回缓存      │           │
│     └────────┬────────┘    └─────────────────┘           │
│              │ 否                                          │
│              ▼                                             │
│  2. 查找模块文件 (按 sys.path 顺序)                         │
│     ┌─────────────────────────────────────────┐          │
│     │  .py 文件  │  目录(包)  │  .so/.pyd  │          │
│     └─────────────────────────────────────────┘          │
│              │                                             │
│              ▼                                             │
│  3. 创建 ModuleSpec                                        │
│     ┌─────────────────────────────────────────┐          │
│     │  name, origin, loader, parent, ...       │          │
│     └─────────────────────────────────────────┘          │
│              │                                             │
│              ▼                                             │
│  4. 执行模块代码                                           │
│     ┌─────────────────────────────────────────┐          │
│     │  定义函数、类、变量                      │          │
│     │  执行顶层语句                            │          │
│     └─────────────────────────────────────────┘          │
│              │                                             │
│              ▼                                             │
│  5. 存入 sys.modules                                      │
│     ┌─────────────────────────────────────────┐          │
│     │  sys.modules["mymodule"] = module_obj  │          │
│     └─────────────────────────────────────────┘          │
│              │                                             │
│              ▼                                             │
│  6. 创建当前命名空间引用                                    │
│     ┌─────────────────────────────────────────┐          │
│     │  import mymodule → "mymodule" 指向模块   │          │
│     │  from m import X → "X" 指向具体对象      │          │
│     └─────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────────┘
```

`★ Insight ─────────────────────────────────────`
- sys.modules 是第一道检查，比文件系统搜索快 100 倍
- ModuleSpec（PEP 451）封装了模块的完整元数据
- 理解这个流程是调试导入问题的关键
`─────────────────────────────────────────────────`

- 导入链中任何一步失败，整个导入失败
- `import a.b.c` 不会让 `a` 和 `a.b` 自动可用，除非显式导入

### 模块的 `__file__` 和 `__spec__` 属性

每个模块都有描述其来源的属性：

```python
import math

print(math.__file__)      # 模块文件路径
print(math.__spec__)      # ModuleSpec 对象
print(math.__name__)      # 模块全名
print(math.__loader__)    # 加载器对象

# 示例输出：
# /usr/lib/python3.12/math.py
# ModuleSpec(name='math', loader=<_frozen_importlib.ExtensionFileLoader ...>, ...)
# math
# <'_frozen_importlib.ExtensionFileLoader' object ...>
```

`ModuleSpec`（PEP 451）包含模块的完整元数据：

```python
import mypackage

spec = mypackage.__spec__
print(f"Name: {spec.name}")           # mypackage
print(f"Origin: {spec.origin}")        # 文件路径
print(f"Parent: {spec.parent}")        # 父包名
print(f"Submodule_search_locations: {spec.submodule_search_locations}")  # 包搜索路径
```

`★ Insight ─────────────────────────────────────`
- `__file__` 揭示模块的物理位置
- `__spec__` 提供完整的模块元数据
- 调试导入问题时，检查这些属性非常有用
`─────────────────────────────────────────────────`

---

## 3. 包与 `__init__.py`

### 包的概念

**包**是一个包含 `__init__.py` 文件的目录，用于组织多个模块：

```
mypackage/
├── __init__.py      # 包初始化文件
├── module1.py       # 模块1
├── module2.py       # 模块2
└── subpackage/
    ├── __init__.py
    └── module3.py
```

使用包：

```python
import mypackage.module1
from mypackage import module2
from mypackage.subpackage import module3
```

### `__init__.py` 的作用

`__init__.py` 有两个核心作用：

#### 作用一：标记目录为包

传统 Python（< 3.3）要求包目录必须包含 `__init__.py`：

```python
# mypackage/__init__.py
# 空文件即可，标记 mypackage 为一个包
```

#### 作用二：包初始化代码

`__init__.py` 在包首次被导入时执行：

```python
# mypackage/__init__.py
print("mypackage is being imported!")

# 可以在这里做初始化工作
__version__ = "1.0.0"
```

### `__init__.py` 最佳实践

**原则：保持简单，避免副作用**

```python
# ✅ 推荐：最小化 __init__.py
from .core import CoreClass
from .utils import helper_function

__all__ = ["CoreClass", "helper_function"]
__version__ = "1.0.0"
```

```python
# ❌ 避免：重操作和副作用
# __init__.py
import heavy_module          # 拖慢导入速度
import pandas               # 第三方依赖
connect_to_database()       # I/O 操作
print("Initializing...")    # 副作用
```

`★ Insight ─────────────────────────────────────`
- `__init__.py` 在包首次导入时执行一次
- 重操作会导致所有使用该包的代码变慢
- 现代 Python（3.3+）支持命名空间包，可省略 `__init__.py`
`─────────────────────────────────────────────────`

### 命名空间包（PEP 420）

Python 3.3+ 支持**命名空间包**，不需要 `__init__.py`：

```
namespace_package/
├── __init__.py   # 可选（无则自动成为命名空间包）
├── module_a.py
└── subpkg/
    └── module_b.py
```

完全省略 `__init__.py` 创建纯命名空间包：

```
pure_namespace_pkg/
├── module1.py
└── subpkg/
    └── module2.py
```

命名空间包的特点：

- 多个目录可以贡献同一个包名
- 没有 `__file__` 属性
- 无法使用 `__init__.py` 做初始化

```python
# 命名空间包的限制
import pure_namespace_pkg
print(pure_namespace_pkg.__file__)  # AttributeError!
```

### 何时用普通包 vs 命名空间包

| 场景 | 推荐 |
|------|------|
| 单个发行版的包 | 普通包（有 `__init__.py`） |
| 多个发行版共享顶级命名空间 | 命名空间包（PEP 420） |
| 需要包级初始化 | 普通包 |
| 纯 Python 3.3+ 项目 | 都可以 |

---

### `__init__.py` 模板模式

#### 模式 A：最小化（推荐）

```python
# 小型包的最佳实践
from .core import API, Helper

__all__ = ["API", "Helper"]
__version__ = "1.0.0"
```

#### 模式 B：延迟加载 facade

对于大型包，使用 `__getattr__` 实现延迟加载：

```python
# __init__.py
__all__ = ["HeavyObject", "light_function"]

# 延迟加载映射表
_LAZY_IMPORTS = {
    "HeavyObject": ("mypackage.heavy", "HeavyObject"),
    "ExpensiveClass": ("mypackage.expensive", "ExpensiveClass"),
}

def __getattr__(name: str):
    if name in _LAZY_IMPORTS:
        module_path, attr_name = _LAZY_IMPORTS[name]
        mod = __import__(module_path, fromlist=[attr_name])
        value = getattr(mod, attr_name)
        globals()[name] = value  # 缓存到全局
        return value
    raise AttributeError(f"{__name__!r} has no attribute {name!r}")

def __dir__():
    return sorted(list(globals()) + list(_LAZY_IMPORTS.keys()))
```

#### 模式 C：版本和元数据

```python
# __init__.py
from ._version import get_version

__version__ = get_version()
__author__ = "Your Name"
__email__ = "your@email.com"

# 版本格式化和比较
from ._version import version_info
__version_info__ = version_info  # tuple like (1, 0, 0)
```

#### 模式 D：`TYPE_CHECKING` 隔离

```python
from __future__ import annotations
from typing import TYPE_CHECKING

__all__ = ["PublicAPI"]

if TYPE_CHECKING:
    from .heavy import HeavyClass

# 运行时只导出轻量符号
from .light import PublicAPI
```

`★ Insight ─────────────────────────────────────`
- **小包用模式 A**：简单直接
- **大包用模式 B**：延迟加载减少启动时间
- **需要版本管理用模式 C**：配合 `versioneer` 或 `setuptools_scm`
- **类型注解循环用模式 D**：`TYPE_CHECKING` 隔离
`─────────────────────────────────────────────────`

---

## 4. 绝对导入 vs 相对导入

### 绝对导入

**绝对导入**使用模块的完整路径：

```python
from mypackage.subpackage import module
import mypackage.utils
from mypackage.core import MyClass
```

特点：

- 从 `sys.path` 开始搜索
- Python 3 中的默认和推荐方式
- 清晰无歧义

### 相对导入

**相对导入**使用点号（`.`）表示当前位置：

```python
from . import module          # 同级模块
from .subpkg import module    # 子包模块
from .. import parent         # 上级模块
from ..sibling import mod     # 上级的同级模块
```

### 对比示例

```
mypackage/
├── __init__.py
├── main.py
├── core/
│   ├── __init__.py
│   ├── engine.py
│   └── utils.py
└── api/
    ├── __init__.py
    ├── server.py
    └── client.py
```

在 `api/server.py` 中导入 `core/utils.py`：

```python
# 绝对导入
from mypackage.core.utils import helper

# 相对导入
from ..core.utils import helper
```

### 为什么相对导入会失败

常见的错误：

```
ImportError: attempted relative import with no known parent package
```

原因：直接运行模块文件时，模块的 `__name__` 是 `"__main__"`，没有父包上下文：

```python
# mypackage/api/server.py
from . import client  # 相对导入需要父包

# 错误：直接运行
# python mypackage/api/server.py
# __name__ == "__main__"，没有父包

# 正确：使用 -m 运行
# python -m mypackage.api.server
# __name__ == "mypackage.api.server"，有父包
```

`★ Insight ─────────────────────────────────────`
- **始终使用 `python -m` 运行包内模块**，不要直接 `python path/to/module.py`
- 绝对导入是默认和推荐方式
- 相对导入仅在包内部使用，避免跨包引用
`─────────────────────────────────────────────────`

### 导入规则总结

| 类型 | 语法 | 示例 | 建议 |
|------|------|------|------|
| 绝对导入 | `from package import module` | `from mypkg.utils import f` | **默认使用** |
| 相对导入 | `from . import module` | `from .utils import f` | 包内使用 |
| 隐式相对导入 | `import module`（无前缀） | `import utils` | **避免**，Python 3 已禁用 |

### 条件导入

根据环境或条件选择性地导入模块：

```python
# 根据平台选择导入
import sys

if sys.platform == "win32":
    import msvcrt  # Windows 专用
else:
    import tty     # Unix 专用
    import termios

# 根据 Python 版本选择
import sys
if sys.version_info >= (3, 11):
    from importlib.resources import files
else:
    from importlib_resources import files

# 可选依赖的导入
try:
    import numpy as np
except ImportError:
    np = None  # 优雅降级
    warnings.warn("numpy not installed, some features disabled")
```

**最佳实践：** 使用 `try/except` 处理可选依赖，而不是检查包是否存在：

```python
# ❌ 不推荐：检查后再导入
import importlib.util
if importlib.util.find_spec("numpy"):
    import numpy as np

# ✅ 推荐：直接尝试导入，失败时处理
try:
    import numpy as np
except ImportError:
    np = None
    np = None

def use_numpy():
    if np is None:
        raise RuntimeError("numpy is required for this function")
    return np.array([1, 2, 3])
```

`★ Insight ─────────────────────────────────────`
- 条件导入用于跨平台兼容、可选依赖、版本差异
- 优雅降级比直接失败更好
- `try/except` 是处理可选依赖的标准方式
`─────────────────────────────────────────────────`

---

## 5. 模块搜索路径

### `sys.path` 详解

Python 导入模块时，按以下顺序在 `sys.path` 中搜索：

```
sys.path 初始顺序（简化）：
1. 当前脚本所在目录（或当前目录）
2. PYTHONPATH 环境变量指定的目录
3. Python 安装目录的 site-packages
4. 标准库目录
```

```python
import sys
print(sys.path)
```

输出示例：

```
['',
 '/usr/local/lib/python3.12/site-packages',
 '/usr/local/lib/python3.12',
 '/usr/lib/python3.12',
 ...]
```

### 搜索顺序的影响

**重要：第一个匹配的模块被使用**

```python
# 当前目录有个 math.py
# 会覆盖标准库的 math 模块！
import math
print(math.pi)  # 可能不是你期望的结果
```

这意味着：
- 不要创建与标准库同名的模块（`math.py`、`os.py`、`sys.py` 等）
- 当前目录优先于标准库

### 添加自定义搜索路径

**不推荐：在运行时修改 sys.path**

```python
# ❌ 避免：运行时修改 sys.path
import sys
sys.path.insert(0, '/my/custom/path')  # 脆弱，不推荐
import mymodule
```

**推荐：使用环境变量或安装**

```bash
# ✅ 推荐：设置 PYTHONPATH
export PYTHONPATH=/my/custom/path:$PYTHONPATH
```

```bash
# ✅ 推荐：安装包（开发模式）
pip install -e .
```

### `sys.path` 初始化源码分析

Python 解释器启动时，`sys.path` 的初始化逻辑：

```python
# 伪代码，展示初始化过程
def init_sys_path():
    # 1. 脚本所在目录（或当前目录）
    script_dir = os.path.dirname(sys.argv[0])
    if script_dir:
        sys.path.insert(0, script_dir)
    else:
        sys.path.insert(0, '')  # 当前目录

    # 2. PYTHONPATH 环境变量
    pythonpath = os.environ.get('PYTHONPATH', '')
    for path in pythonpath.split(os.pathsep):
        if path:
            sys.path.append(path)

    # 3. 安装目录配置
    for path in installation_directories:
        sys.path.append(path)
```

### Python 3.12+ 改进

Python 3.12 对导入系统做了显著改进：

#### 改进的错误信息

```python
# Python 3.11 错误
# ModuleNotFoundError: No module named 'nonexistent'

# Python 3.12 改进
# ModuleNotFoundError: No module named 'nonexistent' (consider using --python option for alternative Python)
```

#### 更详细的导入追踪

```bash
# Python 3.12+ 显示详细的导入决策
python -v -X importtime your_script.py 2>&1 | head -30
```

#### PEP 749 (`importlib.metadata` 改进)

```python
# Python 3.12+ 更快的包元数据访问
from importlib.metadata import version, packages_distributions

# 获取已安装包的版本
print(version("numpy"))  # e.g., "1.26.0"

# 包的依赖关系
dist = packages_distributions()
print(dist.get("numpy", []))  # ['numpy-*']
```

#### 改进的 `-X importtime`

```bash
# Python 3.12+ 更精确的导入时间测量
python -X importtime -c "import numpy" 2>&1 | head -20
```

`★ Insight ─────────────────────────────────────`
- Python 3.12 大幅改进了导入错误信息
- `importlib.metadata` 性能显著提升
- 调试导入问题时，Python 3.12+ 提供的诊断信息更丰富
`─────────────────────────────────────────────────`

### 包与 `__path__`

包有一个特殊的 `__path__` 属性，是 `sys.path` 的子集：

```python
import mypackage
print(mypackage.__path__)
# 包含 mypackage 目录，供子模块导入时搜索
```

`__path__` 使得包可以**横跨多个目录**（命名空间包的实现基础）。

---

## 6. `__all__` 控制公共API

### `__all__` 的作用

`__all__` 定义模块/包的公共接口，控制 `from module import *` 的行为：

```python
# mymodule.py
def public_function():
    pass

def _private_function():
    pass

__all__ = ['public_function']  # 只暴露这个
```

```python
from mymodule import *

public_function()  # 可用
_private_function()  # 不可用（NameError）
```

### `__all__` 的最佳实践

```python
# ✅ 推荐：显式声明公共 API
__all__ = [
    'MyClass',
    'helper_function',
    'IMPORTANT_CONSTANT',
]

# 保持 __all__ 和实际导出一致
from .core import MyClass, helper_function, _internal
```

```python
# ❌ 避免：__all__ 与实际导出不符
__all__ = ['public_api']  # 列了但没导出

from .impl import public_api  # 实际在这里
```

### `__all__` 与文档

`__all__` 同时是文档工具：

- 告诉用户哪些是公共 API
- Sphinx/Read The Docs 等工具使用 `__all__` 生成文档
- IDE 自动补全参考 `__all__`

### 默认行为（无 `__all__`）

没有 `__all__` 时，`from module import *` 导入所有**不以单下划线开头**的名称：

```python
# mymodule.py
public_var = 1
_private_var = 2
another_public = 3

from mymodule import *
print(another_public)  # 3
print(_private_var)    # NameError
```

`★ Insight ─────────────────────────────────────`
- `__all__` 是 API 合约的一部分
- 始终显式定义 `__all__`，保持其与实际导出同步
- 即使不写 `__all__`，单下划线前缀的名称默认也是私有的
`─────────────────────────────────────────────────`

### 导入调试技巧

#### 1. 查看模块来源

```python
import mymodule
print(mymodule.__file__)      # 文件路径
print(mymodule.__spec__)      # 完整规格
print(mymodule.__loader__)    # 加载器

# 检查是否是内置模块
import sys
print('math' in sys.builtin_module_names)  # True
```

#### 2. 追踪导入过程

```bash
# Python 3.11+ 显示导入来源
python -X importtime -c "import numpy"

# Python 3.12+ 更详细的追踪
python -v -c "import mypackage" 2>&1 | head -50
```

#### 3. 分析导入时间

```python
import time
import sys

# 方法 1：手动计时
start = time.perf_counter()
import heavy_module
print(f"Import time: {time.perf_counter() - start:.3f}s")

# 方法 2：使用 cProfile
import cProfile
cProfile.run('import mypackage', sort='cumulative')

# 方法 3：使用 pyinstrument（更精确）
# pip install pyinstrument
# pyinstrument -c "import mypackage"
```

#### 4. 查找导入位置

```python
import importlib.util

# 查找模块规格
spec = importlib.util.find_spec('numpy')
print(f"Name: {spec.name}")
print(f"Origin: {spec.origin}")
print(f"Loader: {spec.loader}")

# 列出所有已安装的包位置
import pkg_resources
for pkg in pkg_resources.working_set:
    print(f"{pkg.project_name}: {pkg.location}")
```

`★ Insight ─────────────────────────────────────`
- `python -X importtime` 是分析导入性能的首选工具
- `importlib.util.find_spec()` 可查找模块的实际位置
- 导入时间超过 100ms 的模块值得优化
`─────────────────────────────────────────────────`

### 模块命名下划线约定

Python 模块中下划线有特殊含义：

```python
# mymodule.py

public_name = 1      # 公共 API，可被 import * 导入
_public_name = 2     # 约定私有，import * 不会导入
__name = 3           # 名称修饰（name mangling）
__name__ = 4        # 特殊属性，不是私有

# 名称修饰示例
class MyClass:
    def __private_method(self):
        pass

# 名称修饰后变成 _MyClass__private_method
```

| 形式 | 示例 | 含义 |
|------|------|------|
| `name` | `public_name` | 公共 API |
| `_name` | `_private` | 约定私有，外部不应访问 |
| `__name` | `__data` | 名称修饰，子类不覆盖 |
| `__name__` | `__init__` | 魔术方法/属性 |
| `__all__` | `__all__` | 公共 API 定义 |

---

## 7. 循环导入：问题与解决方案

### 什么是循环导入

当两个模块相互导入时，形成循环：

```python
# a.py
import b

def func_a():
    b.func_b()  # 使用 b

# b.py
import a

def func_b():
    a.func_a()  # 使用 a
```

执行 `import a` 时：

```
1. 开始导入 a.py
2. 遇到 import b，开始导入 b.py
3. b.py 中 import a → a 已在 sys.modules 中但未完成初始化
4. a.py 中调用 b.func_b()，但 b 中的代码可能还没执行完
5. 报错：ImportError 或 AttributeError
```

### 错误表现

常见的循环导入错误：

```
ImportError: cannot import name 'X' from 'Y'

AttributeError: partially initialized module 'X' has no attribute 'Y'
```

### 解决方案一：推迟导入（局部导入）

将导入语句移到函数内部：

```python
# a.py
def func_a():
    import b  # 局部导入
    b.func_b()
```

适用于：只在特定函数中需要对方模块。

### 解决方案二：`import module` 而非 `from module import name`

```python
# a.py
import b  # 只绑定模块，不立即解析属性

def func_a():
    b.func_b()  # 延迟到函数调用时，b 已完全加载
```

`from module import name` 需要立即解析 `name`，更容易触发循环问题。

### 解决方案三：`TYPE_CHECKING` 处理类型注解

仅用于类型注解的导入可以用 `TYPE_CHECKING` 隔离：

```python
# a.py
from __future__ import annotations  # Python 3.7+，推迟注解求值
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from b import BClass  # 仅类型检查器可见

def func_a(b: BClass) -> None:  # 运行时不求值
    pass
```

### 解决方案四：提取共享模块

将双方都需要的代码提取到第三个模块：

```python
# common.py（第三模块）
def shared_logic():
    pass

# a.py
from common import shared_logic
import b

# b.py
from common import shared_logic
import a
```

### 解决方案五：依赖反转

通过接口/抽象类解耦：

```python
# interfaces.py
class NotifierInterface:
    def notify(self, msg: str) -> None:
        raise NotImplementedError

# a.py
from interfaces import NotifierInterface

class A:
    def __init__(self, notifier: NotifierInterface):
        self.notifier = notifier

# b.py
from interfaces import NotifierInterface

class B:
    def __init__(self, notifier: NotifierInterface):
        self.notifier = notifier
```

### 解决方案六：合并模块

如果两个模块强耦合，考虑合并：

```python
# a.py 和 b.py 合并为 ab.py
class A:
    pass

class B:
    pass
```

### 循环导入检查工具

诊断循环导入：

```bash
# 使用 pydeps 查看依赖图
pip install pydeps
pydeps mypackage

# 或运行 Python 查看详细导入过程
python -vvv -c "import mymodule" 2>&1 | grep -E "(import|Module)"
```

`★ Insight ─────────────────────────────────────`
- 循环导入通常是**设计信号**：模块间耦合太紧
- 临时解决方案：局部导入
- 长期方案：重构模块边界
- `TYPE_CHECKING` 是处理类型注解循环导入的标准方式
`─────────────────────────────────────────────────`

### 循环导入速查

| 症状 | 原因 | 快速修复 |
|------|------|----------|
| `ImportError: cannot import name` | 导入顺序问题 | 改用 `import module` |
| `AttributeError: partially initialized` | 模块初始化未完成时访问 | 局部导入 |
| 重复模块名在 traceback | 循环导入 | 重构或 `TYPE_CHECKING` |

---

## 附录：常见陷阱与反模式

### 陷阱 1：与标准库同名

```python
# ❌ 创建一个名为 math.py 的文件
# 这会覆盖标准库的 math 模块！
import math
print(math.pi)  # 可能是你自定义的值，而非标准库的 3.14159...
```

**常见冲突名称：** `math`, `os`, `sys`, `time`, `json`, `logging`, `email`, `threading`

**解决方法：** 使用独特的前缀或命名空间：

```python
# ✅ my_math.py 或 math_utils.py
# ✅ mypackage.math 内部使用
```

---

### 陷阱 2：修改 `sys.path` 运行时

```python
# ❌ 运行时修改 sys.path
import sys
sys.path.insert(0, '/my/path')  # 脆弱、不安全

# ❌ 在 __init__.py 中修改
# mypackage/__init__.py
import sys
sys.path.insert(0, 'some/path')  # 影响所有导入者
```

**正确做法：**
- 设置 `PYTHONPATH` 环境变量
- 使用可编辑安装 `pip install -e .`
- 或将路径写入 `pyproject.toml` 配置

---

### 陷阱 3：`__init__.py` 中的副作用

```python
# ❌ 在 __init__.py 中执行 I/O
# mypackage/__init__.py
import pandas as pd  # 慢！每次 import 都加载
import requests      # 每次导入都建立连接
print("Initializing...")  # 副作用
connect_to_database()  # 危险

# ✅ 正确做法：保持最小化
from .core import CoreClass
__all__ = ["CoreClass"]
```

---

### 陷阱 4：隐式相对导入（Python 3 已禁用）

```python
# ❌ 在 Python 3 中这样做会失败
# mypackage/utils.py
import mypackage.core  # 隐式绝对导入（不是相对导入）

# ✅ 正确：显式绝对导入
from mypackage import core

# ✅ 或使用相对导入
from .. import core
```

---

### 反模式：`from module import *`

```python
# ❌ 反模式：污染命名空间
from os import *  # os.path, os.remove 等全部导入

# ✅ 推荐：显式导入
from os import path, remove

# ✅ 或使用模块前缀
import os
os.path.join()
```

**使用 `from module import *` 的风险：**
- 名称冲突
- 难以追踪变量来源
- IDE 无法提供准确的自动补全

---

### 反模式：全局导入所有子模块

```python
# ❌ __init__.py 中导入所有子模块
# mypackage/__init__.py
from . import module1, module2, module3, module4, module5
from .module1 import *
from .module2 import *
# ... 每次 import mypackage 都要加载所有模块！

# ✅ 正确：延迟加载或按需导入
__all__ = ["Module1", "Module2"]
from .module1 import Module1  # 只导入常用的
```

---

### 反模式：循环依赖不修复

```python
# ❌ 长期使用局部导入掩盖循环依赖问题
# a.py
def func_a():
    import b  # 每次调用都执行一次导入
    return b.func_b()

# ✅ 正确：重构模块结构，消除循环
# 将共享逻辑提取到 shared.py
```

`★ Insight ─────────────────────────────────────`
- 大多数导入问题是**设计问题**的信号
- 临时修复（局部导入）只是掩盖了耦合过紧的根本问题
- 优先通过重构解决，而不是添加更多变通方案
`─────────────────────────────────────────────────`

---

## 8. 项目布局最佳实践

### 推荐：`src` 布局

**当前最佳实践是 `src` 布局**：

```
project/
├── pyproject.toml          # 项目配置
├── src/
│   └── mypackage/
│       ├── __init__.py
│       ├── core.py
│       └── utils.py
├── tests/
│   ├── __init__.py
│   ├── test_core.py
│   └── test_utils.py
├── docs/
└── README.md
```

`src` 布局的优点：

- 测试运行时导入的是**已安装的包**，而非本地源码
- 避免测试意外导入本地未安装的代码
- 更接近用户的实际使用场景

### 另一种布局：平铺结构

小型项目可使用：

```
mypackage/
├── __init__.py
├── module1.py
└── module2.py
```

### `pyproject.toml` 配置

现代 Python 项目使用 `pyproject.toml`：

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mypackage"
version = "0.1.0"
description = "A sample package"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = ["pytest", "black", "mypy"]

[tool.hatch.build.targets.wheel]
packages = ["src/mypackage"]
```

### 可编辑安装

开发时使用可编辑安装：

```bash
# 安装为可编辑包
pip install -e .

# 修改源码后无需重新安装
```

可编辑安装通过 `.egg-link` 或 PEP 660 实现，将源码目录链接到 site-packages。

### 虚拟环境

每个项目使用独立虚拟环境：

```bash
# 创建
python -m venv .venv

# 激活（Linux/macOS）
source .venv/bin/activate

# 激活（Windows）
.venv\Scripts\activate

# 安装依赖
pip install -e ".[dev]"
```

`★ Insight ─────────────────────────────────────`
- `src` 布局是当前社区推荐的大型项目布局
- `pyproject.toml` 是现代 Python 打包的标准
- 虚拟环境隔离依赖，避免版本冲突
`─────────────────────────────────────────────────`

### 测试包的导入

测试包导入是否正常：

```python
# tests/test_import.py
import pytest

def test_package_import():
    """验证包可以正常导入"""
    import mypackage
    assert hasattr(mypackage, "__version__")

def test_submodule_import():
    """验证子模块可以正常导入"""
    from mypackage.core import Processor
    assert Processor is not None

def test_public_api():
    """验证公共 API 导出正确"""
    from mypackage import __all__
    assert "Processor" in __all__
```

**pytest 配置：**

```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]  # 确保可以找到包
```

或者在 `tests/conftest.py` 中：

```python
# tests/conftest.py
import sys
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
```

### 发布包到 PyPI

完整的发布流程：

```bash
# 1. 安装构建工具
pip install build twine

# 2. 源码包和 wheel
python -m build

# 3. 检查构建产物
ls dist/
# mypackage-0.1.0-py3-none-any.whl
# mypackage-0.1.0.tar.gz

# 4. 上传到 PyPI（先到 TestPyPI 测试）
twine upload --repository testpypi dist/*

# 5. 正式发布
twine upload dist/*
```

**包结构示例：**

```
mypackage/
├── src/
│   └── mypackage/
│       ├── __init__.py
│       └── ...
├── tests/
├── pyproject.toml
├── README.md
└── LICENSE
```

```toml
# pyproject.toml 完整示例
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "mypackage"
version = "0.1.0"
description = "A sample package"
readme = "README.md"
requires-python = ">=3.10"
license = {text = "MIT"}
authors = [
    {name = "Your Name", email = "you@example.com"}
]
classifiers = [
    "Programming Language :: Python :: 3",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
]
dependencies = []

[project.optional-dependencies]
dev = ["pytest", "black", "mypy"]

[tool.hatch.build.targets.wheel]
packages = ["src/mypackage"]
```

### 模块的 `__future__` 导入

`__future__` 模块启用未来语言特性：

```python
# 必须放在文件顶部（除注释和 docstring 之外）
from __future__ import annotations  # PEP 563 - 推迟注解求值
from __future__ import absolute_import  # PEP 328 - 明确绝对导入
from __future__ import division  # Python 3 行为
from __future__ import print_function  # 使用 print() 函数

# 注解求值影响：
# without: from __future__ import annotations
#   def f(x: int) -> List[int]:  # 需要引号 "List[int]"
# with: from __future__ import annotations
#   def f(x: int) -> List[int]:  # 不需要引号
```

`★ Insight ─────────────────────────────────────`
- `from __future__ import annotations` 是现代 Python 3.7+ 的最佳实践
- 减少循环导入问题，加快导入速度
- 与 `TYPE_CHECKING` 配合使用效果更好
`─────────────────────────────────────────────────`

### 类型存根文件（.pyi）

类型存根文件为 C 扩展模块或第三方库提供类型信息：

```python
# mypackage/stubs/numpy.pyi（类型存根）
"""Type stubs for numpy"""
from typing import Any, overload

class ndarray:
    @overload
    def __init__(self, shape: tuple[int, ...], dtype: str = ...) -> None: ...
    def dot(self, other: ndarray) -> ndarray: ...
    def __add__(self, other: ndarray) -> ndarray: ...

# 配置 mypy 使用存根
# pyproject.toml
[tool.mypy]
python_version = "3.10"
mypy_path = "mypackage/stubs"
```

`★ Insight ─────────────────────────────────────`
- `.pyi` 文件提供纯 Python 类型的类型信息
- 不需要为 C 扩展编写实际代码
- 改善 IDE 补全和类型检查
`─────────────────────────────────────────────────`

---

## 9. 实战示例

### 示例一：创建自己的包

创建 `mypackage` 包：

```python
# src/mypackage/__init__.py
"""mypackage - A sample package"""

__version__ = "0.1.0"

from .core import Processor
from .utils import validate_input

__all__ = ["Processor", "validate_input", "__version__"]
```

```python
# src/mypackage/core.py
class Processor:
    def __init__(self, name: str):
        self.name = name

    def process(self, data: str) -> dict:
        return {"processor": self.name, "data": data}
```

```python
# src/mypackage/utils.py
def validate_input(data: str) -> bool:
    return bool(data and data.strip())
```

### 示例二：相对导入在包内的使用

```
mypackage/
├── __init__.py
├── api/
│   ├── __init__.py
│   ├── routes.py
│   └── models.py
└── core/
    ├── __init__.py
    └── engine.py
```

```python
# mypackage/api/routes.py
from .models import User  # 相对导入：同级的 models

def get_user(user_id: int) -> User:
    return User(id=user_id, name="Example")
```

```python
# mypackage/core/engine.py
from ..api.models import User  # 相对导入：上级目录的 models

def create_default_user() -> User:
    return User(id=0, name="Default")
```

### 示例三：循环导入的解决

原始（有循环）：

```python
# user.py
import order

class User:
    def __init__(self, name: str):
        self.name = name

    def get_orders(self):
        return order.get_by_user(self.name)

# order.py
import user

class Order:
    def __init__(self, user_name: str, amount: float):
        self.user_name = user_name
        self.amount = amount

def get_by_user(name: str):
    return [o for o in orders if o.user_name == name]

orders = [Order("Alice", 100)]
```

修复方案 - 提取共享函数：

```python
# models.py（新增第三模块）
class User:
    def __init__(self, name: str):
        self.name = name

class Order:
    def __init__(self, user_name: str, amount: float):
        self.user_name = user_name
        self.amount = amount

# user.py
from models import User, Order, get_orders_by_user  # 使用共享模块

class User:
    def __init__(self, name: str):
        self.name = name

    def get_orders(self):
        return get_orders_by_user(self.name)

# order.py
from models import User, Order, get_orders_by_user

def get_orders_by_user(name: str):
    return [o for o in orders if isinstance(o, Order) and o.user_name == name]

orders = [Order("Alice", 100)]
```

### 示例四：延迟导入优化

对于启动慢的模块，使用延迟导入：

```python
# __init__.py
__all__ = ["HeavyObject", "light_function"]

def light_function():
    return "light"

# 使用延迟导入避免重操作
_imports = {
    "HeavyObject": "mypackage.heavy:HeavyObject",
}

def __getattr__(name: str):
    if name in _imports:
        module_path, attr = _imports[name].split(":")
        mod = __import__(module_path, fromlist=[attr])
        val = getattr(mod, attr)
        globals()[name] = val  # 缓存
        return val
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def __dir__():
    return sorted(list(globals()) + list(_imports.keys()))
```

### 示例五：类型注解避免循环导入

```python
# a.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from b import B

class A:
    def use_b(self, b: B) -> None:
        pass
```

---

## 10. 常见问题速查表

### `import` vs `from import`

| 场景 | 推荐 |
|------|------|
| 需要模块的多个功能 | `import module` |
| 频繁使用某几个名称 | `from module import a, b` |
| 避免名称冲突 | `import module` + `module.name` |
| 类型注解（仅需类型） | `TYPE_CHECKING` 块 |
| 重新导出子模块功能 | `from .sub import func` + `__all__` |

### `__init__.py` 清单

| 操作 | 是否适合 |
|------|----------|
| 版本号定义 | ✅ `__version__ = "1.0"` |
| 显式导入导出 | ✅ `from .mod import X` |
| 轻量初始化 | ✅ 配置检查 |
| 重量 I/O | ❌ 数据库连接、网络请求 |
| 长时间计算 | ❌ 模块级循环 |
| 副作用 | ❌ 打印、logging 配置 |

### 项目布局选择

| 项目规模 | 布局 | 说明 |
|----------|------|------|
| 单文件脚本 | 无 | 脚本直接运行 |
| 小型工具 | 平铺 | `mypkg/__init__.py` + 少数模块 |
| 库/框架 | `src/` | 测试隔离，推荐 |
| 多发行版共享命名空间 | 命名空间包 | PEP 420 |

### 调试导入问题

```bash
# 1. 查看导入搜索路径
python -c "import sys; print('\n'.join(sys.path))"

# 2. 详细导入过程
python -vvv myscript.py 2>&1 | head -100

# 3. 检查模块加载顺序
python -c "import mymodule; print(mymodule.__file__)"

# 4. 使用工具检测循环导入
pip install pydeps
pydeps mypackage --show-deps
```

### PEP 速查

| PEP | 主题 |
|-----|------|
| PEP 302 | import 钩子机制 |
| PEP 328 | 相对/绝对导入 |
| PEP 366 | `__package__` 与 `-m` 运行 |
| PEP 420 | 命名空间包 |
| PEP 451 | ModuleSpec |
| PEP 562 | 模块级 `__getattr__` |
| PEP 563 | 推迟注解求值 |
| PEP 660 | 可编辑安装 |
| PEP 690 | 延迟导入（**已被拒绝**——2023年 Steering Council 投票拒绝，理由：全局行为导致代码行为不可预测） |
| PEP 810 | **✅ 已批准！** 显式延迟导入（`lazy import` 关键字，Python 3.15+） |

---

### 动态导入：`importlib`

`importlib` 模块支持运行时动态导入：

```python
import importlib

# 动态导入（模块名在运行时确定）
module_name = "math"
math = importlib.import_module(module_name)
print(math.pi)  # 3.14159...

# 动态导入子模块
numpy = importlib.import_module("numpy.linalg")
print(numpy.norm([1, 2, 3]))  # 计算范数
```

**插件系统示例：**

```python
import importlib
from pathlib import Path

def load_plugins(plugin_dir: str = "plugins"):
    """动态加载插件"""
    plugins = {}
    plugin_path = Path(plugin_dir)

    for file in plugin_path.glob("*.py"):
        if file.stem.startswith("_"):
            continue
        module_name = file.stem
        spec = importlib.util.spec_from_file_location(module_name, file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if hasattr(module, "register"):
            plugins[module_name] = module

    return plugins
```

**与 `__import__()` 的区别：**

```python
# importlib.import_module() 是推荐方式
import importlib
math = importlib.import_module("math")

# __import__() 是底层方式，功能相同但更复杂
math = __import__("math")
```

`★ Insight ─────────────────────────────────────`
- `importlib.import_module()` 是运行时导入的首选方式
- 插件系统、条件加载、动态模块名都需要 `importlib`
- 比 `__import__()` 更简洁、更推荐
`─────────────────────────────────────────────────`

### `importlib.resources` 访问包资源

Python 3.9+ 推荐使用 `importlib.resources` 访问包内数据文件：

```python
# Python 3.9+
from importlib.resources import files

# 访问包内的数据文件
config_path = files("mypackage.config").joinpath("settings.json")
with config_path.open() as f:
    settings = json.load(f)

# 读取文本文件
readme = (files("mypackage") / "README.txt").read_text()

# 列出包内文件
for file in files("mypackage.data").iterdir():
    print(file.name)
```

**版本兼容性：**

```python
# Python 3.9+ 推荐用法
from importlib.resources import files

# Python 3.7/3.8 用法（已废弃）
# from importlib.resources import open_text, read_text

# Python 3.11+ 额外功能
from importlib.resources import as_file  # 临时解压资源文件
```

**实际案例：AgentScope 的配置加载**

```python
# AgentScope 使用类似模式加载默认配置
from importlib.resources import files

def get_default_config():
    config_dir = files("agentscope.config")
    default_config = config_dir / "default_config.json"
    return json.loads(default_config.read_text())
```

`★ Insight ─────────────────────────────────────`
- `importlib.resources` 是访问包内资源文件的标准方式
- 兼容 zip 文件安装和普通安装
- 比 `__file__` 相对路径更可靠
`─────────────────────────────────────────────────`

---

## 总结

### 核心要点

1. **模块是 `.py` 文件，包是包含 `__init__.py` 的目录**
2. **导入顺序：`sys.modules` 缓存 → `sys.path` 搜索 → 执行**
3. **绝对导入是默认和推荐方式**
4. **`__init__.py` 保持最小化，避免 I/O 和副作用**
5. **`__all__` 定义公共 API，始终显式声明**
6. **循环导入是架构问题，解法包括局部导入、提取模块、TYPE_CHECKING**
7. **`src` 布局是现代项目推荐结构**
8. **使用 `python -m` 运行包内模块，避免相对导入失败**

### 推荐阅读

- [Python 官方教程 - Modules](https://docs.python.org/3/tutorial/modules.html)
- [Python 官方文档 - import 系统](https://docs.python.org/3/reference/import.html)
- [Real Python - Modules and Packages](https://realpython.com/courses/python-modules-packages/)
- [Python Packaging User Guide](https://packaging.python.org/)

---

## 11. 字节码缓存 (`__pycache__`)

### 什么是 `__pycache__`

当你首次导入一个模块时，Python 会将源代码编译为**字节码**并缓存在 `__pycache__` 目录中。下次导入时，Python 直接读取缓存的字节码，避免重新编译。

```bash
# 首次导入后生成
mymodule/
├── __pycache__/
│   └── mymodule.cpython-312.pyc   # 字节码缓存
└── mymodule.py
```

`.pyc` 文件名格式：`{module}.cpython-{major}{minor}.pyc`

- `cpython`：解释器实现
- `312`：Python 3.12
- `.opt-1`、`.opt-2`：优化级别的字节码

`★ Insight ─────────────────────────────────────`
- 字节码是 Python VM 的指令，不是机器码
- `.pyc` 文件可跨平台但需匹配 Python 主版本
- Python 3.12+ 使用 "source hash" 而非时间戳来验证缓存有效性
`─────────────────────────────────────────────────`

### 字节码文件命名规则

```python
import sys
import math

# 查看模块的字节码文件位置
print(math.__cached__)
# /usr/lib/python3.12/__pycache__/math.cpython-312.pyc

# 查看编译后的字节码
import dis
dis.dis(math)  # 查看数学模块的字节码指令
```

### Python 3.12+ 的改进：基于哈希的缓存

Python 3.12 改用 **source hash** 替代时间戳来决定缓存有效性：

```bash
# Python 3.12+ 生成的 .pyc 文件
__pycache__/mymodule.cpython-312.pyc
# 文件内容包含 source code 的 hash，而非 modification time
```

**优势**：
- 可重现构建（reproducible builds）
- 不同机器上只要源代码相同，`.pyc` 就相同
- 更适合打包和分发

### 手动控制字节码生成

```bash
# 预编译模块（生成 .pyc）
python -m py_compile mymodule.py

# 预编译整个目录
python -m compileall .

# 指定优化级别
python -OO -m py_compile mymodule.py  # 移除 docstring
python -O -m py_compile mymodule.py  # 基本优化
```

优化级别：
- `-O`：生成 `.opt-1` 文件
- `-OO`：生成 `.opt-2` 文件（移除 docstring 和断言）

### `__pycache__` 位置控制

```bash
# 使用 pycache_prefix 改变缓存位置
python -X pycache_prefix=/tmp/pycache -m mymodule

# 环境变量方式
export PYTHONPYCACHEPREFIX=/tmp/pycache
python -m mymodule
```

### 禁用字节码缓存

```bash
# 完全禁用（每次重新编译）
python -B -m mymodule

# 或设置环境变量
export PYTHONDONTWRITEBYTECODE=1
```

**注意**：禁用缓存会显著增加导入时间。

### 缓存失效机制

```python
# Python 如何判断缓存是否过期？
# 旧逻辑（Python 3.7-）：比较源文件修改时间
# 新逻辑（Python 3.12+）：比较 source hash

import importlib.util

# 强制重新加载（忽略缓存）
importlib.invalidate_caches()

# 或者删除缓存文件
import pathlib
cache_dir = pathlib.Path("__pycache__")
for f in cache_dir.glob("*.pyc"):
    f.unlink()
```

### 打包与 Frozen 应用

当使用 PyInstaller、cx_Freeze 等工具打包时：

```python
# frozen 应用中 __file__ 可能不存在
# 字节码直接加载到内存，不生成 .pyc 文件

# 检测是否在 frozen 环境
import sys
if getattr(sys, 'frozen', False):
    # frozen 环境
    pass
```

### 调试字节码

```python
# 查看模块的字节码
import dis
import mymodule

dis.dis(mymodule)

# 反汇编函数
def my_function():
    x = 1
    return x

print(dis.dis(my_function))
```

---

## 12. 自定义导入钩子

### 导入系统的可扩展性

Python 的导入系统是完全可扩展的。通过 `sys.meta_path` 和 `sys.path_hooks`，你可以：

- 从数据库加载模块
- 从网络加载模块
- 动态生成模块代码
- 实现插件系统

`★ Insight ─────────────────────────────────────`
- `sys.meta_path`：全局查找器列表，在 `sys.path` 搜索前检查
- `sys.path_hooks`：路径钩子列表，用于处理特定路径类型
- 自定义导入钩子让 Python 可以从任何地方加载代码
`─────────────────────────────────────────────────`

### MetaPathFinder vs PathFinder

| 组件 | 作用域 | 查找方式 |
|------|--------|----------|
| `sys.meta_path` | 全局 | 所有导入 |
| `PathFinder` | 按路径 | `sys.path` 中的目录 |

**查找顺序**：

```
import mymodule
    │
    ├─▶ sys.meta_path 中的每个 MetaPathFinder
    │       │
    │       └─▶ 找到？→ 返回 ModuleSpec
    │
    └─▶ PathFinder 在 sys.path 中搜索
            │
            └─▶ 找到？→ 返回 ModuleSpec
```

### 实现一个 MetaPathFinder

```python
import sys
from importlib.abc import Loader, MetaPathFinder
from importlib.util import spec_from_loader
from typing import Optional

class DatabaseFinder(MetaPathFinder):
    """从数据库加载模块的查找器"""

    def find_spec(
        self,
        fullname: str,
        path: Optional[tuple],
        target: Optional[object] = None
    ) -> Optional[ModuleSpec]:
        """查找模块规格"""
        if not fullname.startswith("db_modules."):
            return None

        module_name = fullname.replace("db_modules.", "")
        source_code = load_source_from_db(module_name)
        if source_code is None:
            return None

        loader = DatabaseLoader(module_name, source_code)
        return spec_from_loader(fullname, loader, origin="database")

class DatabaseLoader(Loader):
    """从数据库加载模块的加载器"""

    def __init__(self, name: str, source: str):
        self.name = name
        self.source = source

    def create_module(self, spec: ModuleSpec) -> Optional[ModuleSpec]:
        return None

    def exec_module(self, module: Module) -> None:
        exec(self.source, module.__dict__)

# 注册查找器
sys.meta_path.insert(0, DatabaseFinder())
```

### 实现一个 PathEntryFinder（路径钩子）

```python
import sys
import importlib.abc
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Optional

class ZipPathFinder(importlib.abc.PathEntryFinder):
    """从 ZIP 文件加载模块的路径查找器"""

    def __init__(self, path: str):
        self.path = Path(path)
        if not self.path.suffix == ".zip":
            raise ImportError("Not a zip file")

    def find_spec(
        self,
        fullname: str,
        target: Optional[ModuleSpec] = None
    ) -> Optional[ModuleSpec]:
        parts = fullname.rsplit(".", 1)
        module_file = self.path / f"{parts[-1]}.py"

        if module_file.exists():
            return ModuleSpec(
                fullname,
                SourceFileLoader(fullname, str(module_file)),
                origin=str(module_file)
            )
        return None

def zipfile_hook(path: str) -> Optional[ZipPathFinder]:
    """路径钩子工厂函数"""
    try:
        return ZipPathFinder(path)
    except ImportError:
        return None

sys.path_hooks.append(zipfile_hook)
```

### 自定义 Loader 的最佳实践

```python
from importlib.abc import Loader
from importlib.util import module_from_spec
from typing import Optional

class SourceLoader(Loader):
    """SourceLoader 模板"""

    def create_module(self, spec: ModuleSpec) -> Optional[ModuleSpec]:
        return None

    def exec_module(self, module: Module) -> None:
        with open(spec.origin, "r") as f:
            source = f.read()
        exec(source, module.__dict__)
```

`★ Insight ─────────────────────────────────────`
- `find_module`/`load_module` 在 Python 3.10+ 已废弃
- 始终使用 `find_spec`/`create_module`/`exec_module` 模式
- `invalidate_caches()` 清除 finder 的内部缓存
`─────────────────────────────────────────────────`

### 临时导入钩子示例

```python
import sys
from importlib.abc import MetaPathFinder
from importlib.util import spec_from_loader
from types import ModuleType

class MockModuleFinder(MetaPathFinder):
    """模拟模块的查找器（用于测试）"""

    def __init__(self):
        self.mocks = {}

    def add_mock(self, name: str, attrs: dict):
        self.mocks[name] = attrs

    def find_spec(self, fullname, path, target=None):
        if fullname in self.mocks:
            loader = MockLoader(self.mocks[fullname])
            return spec_from_loader(fullname, loader)
        return None

class MockLoader:
    def __init__(self, attrs: dict):
        self.attrs = attrs

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        module.__dict__.update(self.attrs)

finder = MockModuleFinder()
finder.add_mock("myapp.config", {"DEBUG": True, "VERSION": "1.0"})
sys.meta_path.insert(0, finder)

from myapp.config import DEBUG, VERSION
print(f"DEBUG={DEBUG}, VERSION={VERSION}")
```

### 安全注意事项

```python
# 安全风险：自定义加载器可能执行任意代码
class UnsafeFinder(MetaPathFinder):
    """不安全：执行任意代码"""
    def find_spec(self, fullname, path, target):
        code = fetch_code_from_network(fullname)  # 危险！
        loader = UnsafeLoader(code)
        return spec_from_loader(fullname, loader)
```

**安全建议**：
- 验证加载的代码来源
- 不要从不可信来源执行代码
- 在隔离进程或容器中运行
- 最小化钩子的作用范围

---

## 13. importlib.metadata 与 importlib.resources

### 访问已安装包的元数据

```python
from importlib.metadata import version, metadata, files

# 获取包的版本
print(version("requests"))  # 2.31.0

# 获取包的详细信息
print(metadata("pip"))

# 获取包包含的文件
for f in files("numpy"):
    print(f.name)
```

### 访问包的资源文件

```python
# Python 3.9+ 推荐方式
from importlib.resources import files

pkg_path = files("my_package") / "data"
for item in pkg_path.iterdir():
    print(item.name)

text_content = (files("my_package") / "README.txt").read_text()
binary_content = (files("my_package") / "image.png").read_bytes()

# 临时文件访问（适用于需要路径的操作）
with files("my_package").as_file("data/config.json") as path:
    import json
    config = json.load(open(path))
```

### 版本约束检查

```python
from importlib.metadata import version, PackageNotFoundError

def require_version(package: str, min_version: str):
    try:
        ver = version(package)
        if ver < min_version:
            raise ImportError(f"{package} {ver} is too old, need {min_version}")
    except PackageNotFoundError:
        raise ImportError(f"{package} is not installed")

require_version("numpy", "1.20")
```

`★ Insight ─────────────────────────────────────`
- `importlib.metadata` 替代了废弃的 `pkg_resources`
- `importlib.resources.files()` 是 Python 3.9+ 的推荐 API
- 资源访问应使用 `as_file()` 上下文管理器
`─────────────────────────────────────────────────`

### 访问入口点（Entry Points）

```python
from importlib.metadata import entry_points

eps = entry_points()
console_scripts = entry_points(group="console_scripts")
for ep in console_scripts:
    print(f"{ep.name}: {ep.value}")

my_plugin = entry_points.select(group="myapp.plugins", name="myplugin")
```

---

## 14. 模块生命周期与卸载

### 模块的加载过程

```python
# 导入时发生的事：
import mymodule

# 1. 在 sys.modules 中查找
# 2. 未找到？查找并加载
# 3. 创建空的 module 对象
# 4. 设置 __name__, __file__, __loader__, __spec__
# 5. 执行模块代码（填充命名空间）
# 6. 将模块对象存入 sys.modules
# 7. 将模块名绑定到当前作用域
```

### 重新加载模块

```python
import importlib

import mymodule
importlib.reload(mymodule)  # 重新执行代码
```

### 卸载模块的陷阱

```python
# 删除 sys.modules 条目 ≠ 完全卸载
import sys
import mymodule

del sys.modules["mymodule"]  # 理论上卸载

# 但问题：
# 1. 其他模块的引用仍然存在
# 2. 全局单例对象仍然存活
# 3. C 扩展的内存可能无法释放
```

### 正确的模块清理模式

```python
# mymodule.py - 显式初始化和清理 API

class _State:
    initialized = False
    resources = []

def init():
    if _State.initialized:
        return
    _State.resources.append(acquire_resource())
    _State.initialized = True

def cleanup():
    global _State
    while _State.resources:
        release_resource(_State.resources.pop())
    _State.initialized = False

import atexit
atexit.register(cleanup)
```

`★ Insight ─────────────────────────────────────`
- Python 没有真正的"模块卸载"机制
- `del sys.modules[name]` 只是移除缓存引用
- 完整的进程隔离需要使用子进程
- 显式初始化/清理 API 是负责任库的标准模式
`─────────────────────────────────────────────────`

### 延迟导入的演进：PEP 690 vs PEP 810

Python 社区长期关注导入性能问题，以下是延迟导入提案的演进历史：

#### PEP 690（已被拒绝）

**PEP 690** 提议全局延迟导入——所有导入默认延迟到首次使用时才执行。

```python
# 启用后，所有模块都会延迟加载
python -L myscript.py

# 或在代码中启用
import sys
sys.set_lazy_imports(True)
```

**拒绝理由**（2023年 Steering Council 投票）：

| 问题 | 说明 |
|------|------|
| 代码行为不可预测 | 导入错误可能在运行时任何位置出现，而非启动时 |
| 社区分裂 | 两种不同的导入语义会导致代码兼容性问题 |
| 实现复杂度 | 全局行为变更影响范围太大 |

> 原文：*"A problem we deem significant when adding lazy imports as a language feature is that it becomes a split in the community over how imports work."*

#### PEP 810（**✅ 已批准，Python 3.15+**）

**PEP 810** 采取了完全不同的思路——**显式、粒度化**的 opt-in 方案：

```python
# 只有标记的导入是延迟的
lazy import numpy as np
lazy from sklearn import model_selection

# 不会影响其他导入
import os  # 立即导入
```

**设计原则**：
- 显式：每个延迟导入都需要 `lazy` 关键字
- 局部：不影响其他模块的导入行为
- 安全：不存在隐式的行为变更

**使用场景**：
- CLI 工具：减少启动时间
- 大型单体仓库：按需加载模块
- 插件系统：延迟加载可选依赖

```python
# Python 3.15+ 示例
lazy import heavy_module  # 启动时不加载

def use_heavy():
    heavy_module.do_something()  # 首次使用时才真正导入
```

#### 工业界实践

虽然 PEP 690 被拒绝，但部分公司实现了自己的延迟导入方案：

```python
# HRT (Hudson River Trading) 的内部方案
# 他们fork了CPython实现全局延迟导入用于加速启动

# Instagram/Meta 的实践
# 在大型单体仓库中，延迟导入显著减少启动时间
```

#### 对开发者的建议

1. **Python 3.15+**：直接使用 `lazy import` 语法
2. **当前版本**：使用 `importlib` 动态导入或局部导入
3. **避免 fork CPython**：维护成本过高，仅适合有特殊需求的团队

```python
# 当前推荐的延迟导入模式（Python < 3.15）
def get_heavy_module():
    import heavy_module  # 局部导入，按需加载
    return heavy_module
```

---

## 15. 模块全局状态与进程隔离

### 模块级全局变量的陷阱

```python
# 反模式：模块级可变状态
cache = {}  # 全局缓存

def get_item(key):
    if key not in cache:
        cache[key] = expensive_computation(key)
    return cache[key]
```

### 显式状态管理模式

```python
# 工厂函数模式
class DatabaseConnection:
    def __init__(self, config: dict):
        self.config = config
        self.conn = None

    def connect(self):
        if self.conn is None:
            self.conn = create_connection(self.config)
        return self.conn

def create_connection(config: dict) -> DatabaseConnection:
    return DatabaseConnection(config)

# 上下文管理器模式
from contextlib import contextmanager

@contextmanager
def database_context():
    conn = create_connection()
    try:
        yield conn
    finally:
        conn.close()
```

### 线程安全问题

```python
# 不安全的全局状态
count = 0

def increment():
    global count
    count += 1  # 不是原子操作

# 使用锁保护
import threading

_lock = threading.Lock()
count = 0

def increment():
    global count
    with _lock:
        count += 1
```

`★ Insight ─────────────────────────────────────`
- 模块全局状态是 Python 程序中许多 bug 的根源
- 显式优于隐式：工厂函数和上下文管理器
- 进程隔离是最可靠的隔离方式
- 虚拟环境解决依赖冲突，但不解决状态冲突
`─────────────────────────────────────────────────`

---

## 16. 导入锁与线程安全

### CPython 的导入锁机制

CPython 在执行导入操作时会获取**导入锁**（import lock），用于协调并发导入。

```python
import sys

# 检查默认的 meta_path（包含 BuiltinImporter 和 FrozenImporter）
print([finder.__class__.__name__ for finder in sys.meta_path])
# ['BuiltinImporter', 'FrozenImporter', 'PathFinder']
```

### 并发导入的语义

```python
# 线程 A 开始导入 pkg（pkg/__init__.py 可能导入其他模块）
# 线程 B 在 A 导入期间尝试导入同一模块
# 导入锁和 sys.modules 检查决定了 B 的行为：
# - 等待 A 完成？
# - 使用 sys.modules 中的部分模块？
# - 触发钩子？
```

### _ModuleLock：CPython 导入锁的实现

`importlib._bootstrap._ModuleLock` 是 CPython 用于协调并发导入的核心类：

```python
# 简化结构
class _ModuleLock:
    def __init__(self, name):
        self.name = name          # 模块名
        self.lock = threading.Lock()
        self.count = 0            # 重入计数
        self.owner = None        # 当前持有线程

    def acquire(self, blocking=True, timeout=-1):
        # 获取锁，支持超时
        ...

    def release(self):
        # 释放锁
        ...
```

**关键机制**：
- 每个模块有独立的 `_ModuleLock` 实例
- 使用**全局排序**避免循环等待死锁
- 检测到循环依赖时主动放弃（不阻塞）

### 死锁检测算法：等待图（wait-for graph）

CPython 使用等待图算法检测潜在死锁：

```
线程 A 导入模块 M1
线程 B 导入模块 M2

情况 1：安全
  A 持有 M1 的锁，等待 B 释放 M2 的锁
  B 持有 M2 的锁，等待 A 释放 M1 的锁
  → 循环等待 → 检测到并报错

情况 2：安全（已解决）
  A 等待 B：B 还未请求锁
  → 无循环 → 可以继续
```

**检测触发条件**（Python 3.4+）：

```python
# 当线程 T 尝试获取模块 M 的锁时：
# 1. 检查 M 是否已有等待队列
# 2. 如果有，构建等待图：T -> M -> (当前持有者) -> ...
# 3. 检测图中是否存在环
# 4. 如果有环，T 放弃等待并抛出 CircularImport 错误
```

### 历史 bug：Issue #38011 和 #82272

CPython 的导入锁机制经历了多次修复：

#### Issue #38011（2019）：死锁检测导致死锁

```
问题：死锁检测本身会触发死锁

场景：
1. 线程 A 持有模块 X 的锁
2. 线程 B 尝试导入 X，进入等待
3. 死锁检测启动，检查线程 B 是否应该等待
4. 检测过程中，线程 B 的状态被修改但未完全释放
5. 线程 A 此时需要获取模块 Y 的锁
6. 线程 C 持有 Y，等待检测完成
→ 多线程同时等待检测，形成新的死锁
```

**修复**：引入更细粒度的锁保护检测过程

#### Issue #82272（2022）：竞态条件

```
问题：_ModuleLock 的 count 和 owner 更新存在竞态

时序：
1. 线程 A：count = 1, owner = A
2. 线程 A 释放锁：count = 0
3. 线程 B 立即获取锁：count = 1, owner = B
4. 线程 A 的 release() 仍在执行，写入旧值
→ owner 被覆盖为 A，但 count 已是 1
```

### 更细粒度的导入锁提案（Issue #9260, #53506）

CPython 团队长期讨论改进导入锁机制：

| 提案 | 核心思路 | 状态 |
|------|----------|------|
| #9260 | per-module lock 替代全局锁 | 讨论中 |
| #53506 | 更细粒度的死锁检测 | 讨论中 |

**当前实现的问题**：
- 全局锁导致所有导入串行化
- 死锁检测有 O(n) 复杂度
- 持有外部锁时导入会阻塞检测

**理想方案**：
- 每个模块独立的锁
- 锁排序基于模块名（字典序）
- 只在获取多个锁时进行检测

### 实际调试：检测导入死锁

```python
# 启用导入调试（Python 3.7+）
python -X importtime -c "import mymodule"

# 或使用 faulthandler
python -X faulthandler -c "import mymodule"

# 在代码中启用
import sys
sys.setprofile(lambda *args: None)  # 轻量级追踪
```

### 潜在的死锁和竞态条件

| 场景 | 风险 | 解决方案 |
|------|------|----------|
| 持有外部锁时导入 | 死锁 | 使用懒加载，避免在 import 时获取锁 |
| 模块级代码执行 I/O | 阻塞 | 将 I/O 延迟到首次使用时 |
| 修改 sys.path 时并发导入 | 竞态 | 使用导入锁保护 sys.path 修改 |

### 安全的并发导入模式

```python
# 安全模式：最小化导入时副作用
# mymodule.py
import threading

_lock = threading.Lock()
_cache = {}

def get_cached(key):
    """首次访问时才初始化（懒加载）"""
    with _lock:
        if key not in _cache:
            _cache[key] = expensive_init(key)
        return _cache[key]
```

`★ Insight ─────────────────────────────────────`
- 导入锁防止并发导入竞态，但不消除所有线程安全问题
- 持有其他锁时执行导入可能导致死锁
- 自定义钩子应快速返回 None，避免阻塞导入系统
`─────────────────────────────────────────────────`

---

## 17. sys.modules 操作与测试隔离

### sys.modules 的核心作用

```python
import sys

# sys.modules 是所有已加载模块的缓存
# 检查模块是否已加载
"requests" in sys.modules

# 获取已加载模块对象
requests_mod = sys.modules.get("requests")
```

### 测试隔离模式

```python
import sys
import types
import importlib.util

@contextlib.contextmanager
def isolated_modules():
    """上下文管理器：测试期间隔离 sys.modules"""
    saved = sys.modules.copy()
    try:
        yield
    finally:
        for name in list(sys.modules.keys()):
            if name not in saved:
                del sys.modules[name]

import contextlib

# 使用
with isolated_modules():
    from myapp import module_under_test
```

### importlib.reload 的行为

```python
import importlib

# reload 重新执行模块代码，但保留模块对象 identity
import mymodule
id1 = id(mymodule)

importlib.reload(mymodule)
id2 = id(mymodule)

print(id1 == id2)  # True - 同一个对象
```

---

## 18. 模块 __path__ 属性深度剖析

### __path__ 的来源

```python
import agentscope

# __path__ 对于普通包来自 spec.submodule_search_locations
print(agentscope.__path__)

# 命名空间包的 __path__ 是动态计算的
# type: <class '_NamespacePath'>
```

### 命名空间包的 __path__

```python
# 命名空间包：多个目录贡献同一个包
import sys
print(namespace_pkg.__path__)
# _NamespacePath(['dir_a/ns_pkg', 'dir_b/ns_pkg'])
print(namespace_pkg.__file__)  # None
```

### pkgutil 与 __path__

```python
import pkgutil

# 列出包中所有模块
for importer, modname, ispkg in pkgutil.iter_modules(mypackage.__path__):
    print(f"{'PKG' if ispkg else 'MOD'}: {modname}")
```

---

## 19. Frozen 模块

### 检测 Frozen 环境

```python
import sys

def is_frozen():
    """检测是否在 frozen 环境（如 PyInstaller）"""
    return getattr(sys, 'frozen', False)
```

### Frozen 模块与普通模块的区别

| 特性 | 普通模块 | Frozen 模块 |
|------|----------|-------------|
| 来源 | .py 文件 | 编译到解释器 |
| __file__ | 有文件路径 | 通常 None |
| 重新加载 | 可用 importlib.reload | 不支持 |

---

## 20. __main__ 与 sys.modules 的关系

### runpy 模块

```python
import runpy

# 以模块方式运行（相当于 python -m）
result = runpy.run_module("mymodule", run_name="__main__")

# 运行指定路径的脚本
result = runpy.run_path("/path/to/script.py")
```

### 常见陷阱

```python
# 陷阱：在 __main__ 中使用相对导入
# from . import sibling  # 直接运行会失败

# 正确做法：使用 python -m mypackage.module
```

`★ Insight ─────────────────────────────────────`
- __main__ 的身份取决于启动方式（-m vs 直接运行）
- runpy.run_module 提供了一种可控的模块执行方式
- 包内模块如需相对导入，必须通过 python -m 运行
`─────────────────────────────────────────────────`

---

## 21. 高级插件系统架构

### 插件发现机制

```python
import pkgutil
import sys

def discover_plugins(package_name: str, prefix: str = "plugin_"):
    """发现包中所有符合前缀的插件模块"""
    package = sys.modules[package_name]
    plugins = []

    for importer, modname, ispkg in pkgutil.iter_modules(
        package.__path__, prefix=f"{prefix}"
    ):
        plugins.append(modname)

    return plugins
```

### 基于入口点的插件

```python
from importlib.metadata import entry_points

# 发现入口点
eps = entry_points(group="myapp.plugins")
for ep in eps:
    PluginClass = ep.load()
    plugin = PluginClass()
```

### 隔离插件执行

```python
import subprocess
import sys

def load_plugin_isolated(plugin_path: str):
    """在独立进程中加载插件，避免插件污染主进程"""
    result = subprocess.run(
        [sys.executable, plugin_path],
        capture_output=True,
        text=True,
        timeout=30
    )
    return result.stdout
```

### 懒加载插件

```python
class PluginRegistry:
    def __init__(self):
        self._plugins = {}
        self._loaded = set()

    def register(self, name, loader):
        """注册插件加载器（不立即加载）"""
        self._plugins[name] = loader

    def get(self, name):
        """懒加载插件"""
        if name not in self._loaded:
            self._plugins[name]()
            self._loaded.add(name)
        return sys.modules.get(name)
```

---

## 22. zipimport：ZIP 文件导入

### ZIP 作为模块搜索路径

Python 可以从 ZIP 文件中导入模块，ZIP 文件只需出现在 `sys.path` 中：

```python
import sys

# 添加 ZIP 文件到 sys.path
sys.path.insert(0, "/path/to/modules.zip")

# 现在可以从 ZIP 中导入模块
from mymodule import my_function
```

### zipimport 工作原理

```python
import zipimport

# zipimporter 是 sys.path_hooks 中的一个钩子
# 当 sys.path 的条目是 ZIP 文件时自动使用

import sys
print(sys.path_hooks)
# [..., <class '_frozen_importlib.FileFinder'>, ...]
```

### 创建可导入的 ZIP 包

```bash
# 创建 ZIP 文件结构
mkdir -p myapp
echo 'def hello(): return "Hello from zip!"' > myapp/__init__.py
zip -r myapp.zip myapp/

# 使用
python -c "import sys; sys.path.insert(0, 'myapp.zip'); import myapp; print(myapp.hello())"
```

### 检查 ZIP 中的模块

```python
import zipimport

# 创建 zipimporter
importer = zipimport.zipimporter("/path/to/modules.zip")

# 列出所有模块
for module_name in importer.find_modules():
    print(module_name)
```

### ZIP 导入的性能考虑

| 情况 | 性能 |
|------|------|
| ZIP 中只有 `.py` 文件 | 较慢（每次导入重新编译）|
| ZIP 中有 `.pyc` 文件 | 快（使用缓存字节码）|

`★ Insight ─────────────────────────────────────`
- Python 不会自动向 ZIP 中的 `.py` 文件添加 `.pyc`
- 如果 ZIP 只包含 `.py`，导入会较慢
- 使用 `zipapp` 工具创建自包含的 Python 应用
`─────────────────────────────────────────────────`

---

## 23. pkgutil 与包发现

### iter_modules：列出直接子模块

```python
import pkgutil
import mypackage

# 列出包的直接子模块（不递归）
for importer, modname, ispkg in pkgutil.iter_modules(mypackage.__path__):
    print(f"{'PKG' if ispkg else 'MOD'}: {modname}")
```

### walk_packages：递归遍历

```python
import pkgutil

# 递归遍历所有子包和模块
for importer, modname, ispkg in pkgutil.walk_packages(mypackage.__path__):
    print(f"{'PKG' if ispkg else 'MOD'}: {modname}")
```

### extend_path：扩展命名空间包

```python
# 在包的 __init__.py 中使用 extend_path
# 让命名空间包从多个目录收集模块

import pkgutil

__path__ = pkgutil.extend_path(__path__, __name__)

# 这样 mypackage 可以从多个目录收集内容
# site-packages/mypackage/
# custom/mypackage/
```

### find_loader：查找加载器

```python
import pkgutil

# 查找模块的加载器
loader = pkgutil.find_loader("requests")
print(loader)  # <zipimporter ...> 或 <class '...SourceFileLoader'>

# 获取模块的源码（如果可用）
if hasattr(loader, 'get_source'):
    source = loader.get_source("requests")
```

---

## 24. 导入调试技术

### 详细导入追踪

```bash
# -v 选项显示每个导入的详细信息
python -v -c "import json"

# -vv 更详细
python -vv -c "import json"
```

### 导入时间分析

```bash
# 使用 -X importtime 查看导入耗时
python -X importtime -c "import numpy"

# 输出示例：
# import time: [top] total | calls
# import time: [    1173] |        1 | <module 'importlib'>
# import time: [    2340] |        1 | <module 'importlib._bootstrap'>
# ...
```

### 导入瓶颈定位

```bash
# 组合使用找出最慢的导入
python -X importtime -c "import heavy_module" 2>&1 | grep "import time" | sort -t'|' -k3 -n -r | head -20
```

### 强制重新加载

```python
import importlib

# 清除导入缓存，强制重新导入
importlib.invalidate_caches()

# 重新加载模块
importlib.reload(existing_module)
```

### sys.modules 检查

```python
import sys

# 查看已缓存的模块数量
print(f"共缓存 {len(sys.modules)} 个模块")

# 找出特定前缀的模块
import re
my_modules = [m for m in sys.modules if re.match(r'mypackage\..*', m)]
print(f"mypackage 相关模块: {len(my_modules)}")
```

---

## 25. importlib.util 高级用法

### module_from_spec：创建模块

```python
import importlib.util
import sys

def load_source_as_module(source_code: str, module_name: str):
    """从源码字符串创建模块对象"""
    # 创建规范
    spec = importlib.util.spec_from_loader(
        module_name,
        loader=None,  # 无加载器，手动执行
        origin="<string>"
    )

    # 从规范创建模块
    module = importlib.util.module_from_spec(spec)

    # 执行源码
    exec(source_code, module.__dict__)

    # 注册到 sys.modules
    sys.modules[module_name] = module

    return module
```

### spec_from_loader：创建规范

```python
import importlib.util
from importlib.abc import Loader

class MyLoader(Loader):
    def create_module(self, spec):
        return None  # 使用默认模块创建

    def exec_module(self, module):
        # 加载逻辑
        pass

# 从加载器创建模块规范
spec = importlib.util.spec_from_loader(
    "mymodule",
    MyLoader(),
    is_package=False
)
```

### SourceFileLoader 子类

```python
import importlib.util
from importlib.machinery import SourceFileLoader

class WatchedSourceFileLoader(SourceFileLoader):
    """带监控的源码加载器"""
    def exec_module(self, module):
        print(f"Loading {module.__name__}")
        super().exec_module(module)

# 使用
spec = importlib.util.spec_from_loader(
    "mymodule",
    WatchedSourceFileLoader("mymodule", "/path/to/mymodule.py")
)
```

---

## 26. 导入系统的 C 层实现

### 字节码层面的 IMPORT_NAME

Python 3.13+ 的 `import` 语句由 `IMPORT_NAME` 字节码指令实现：

```python
# 编译后的 import 语句对应字节码
import json

# 等价的字节码序列：
# LOAD_NAME    0 (json)
# IMPORT_NAME  0 (json)
# STORE_NAME   0 (json)
```

### 调用链概览

```
import json
    │
    ▼
builtins.__import__("json")
    │
    ▼
importlib.__import__(...)  # 或直接调用 C 函数
    │
    ▼
PyImport_ImportModuleLevelObject()  # C level
    │
    ▼
importlib._bootstrap._find_and_load()
    │
    ├── sys.meta_path (MetaPathFinder)
    ├── PathFinder (sys.path)
    └── sys.modules (缓存)
```

### _gcd_import 的作用

```python
# importlib._bootstrap._gcd_import 是核心函数
# _gcd_import(name, level=0)
#   └─> _find_and_load(name, block=True)

# 它的职责：
# 1. 检查 sys.modules 缓存
# 2. 如果未找到，调用 _find_and_load
# 3. 处理包层级 (level 参数)
```

`★ Insight ─────────────────────────────────────`
- 理解 C 层调用链对调试复杂导入问题很有帮助
- `sys.meta_path` 中的查找器在 `_find_and_load` 之前被调用
- Python 3.13+ 使用 `IMPORT_NAME` 字节码，之前的版本使用 `IMPORT_FROM`
`─────────────────────────────────────────────────`

---

## 27. 包供应链安全：typosquatting 与 dependency confusion

### 什么是供应链攻击

Python 包管理系统（pip、PyPI）在软件开发中扮演核心角色，但也成为攻击目标。攻击者利用开发者对第三方包的信任，通过包管理器注入恶意代码。

**主要攻击类型**：

| 攻击类型 | 原理 | 示例 |
|----------|------|------|
| **typosquatting** | 注册与流行包名相似的包名 | `requests` → `request`、`numpy` → `numppy` |
| **dependency confusion** | 在公网发布与私包同名的恶意包 | 内部 `mycorp-utils` → 公网 `mycorp-utils` |
| **account takeover** | 接管流行包维护者的账户 | 更改代码注入恶意代码 |
| **maintainer sabotage** | 原维护者主动引入恶意代码 | 开发者罢工或被贿赂 |

### Typosquatting（误植攻击）

攻击者注册与流行包名**仅差一个字符**的包名：

```
流行包          误植变体
---------       ---------
requests        request
numpy           numppy
pandas          pandans
tensorflow      tensoflow
```

**真实案例**：

```python
# 开发者本想安装
pip install requests

# 但可能意外输入
pip install request   # 恶意包！
```

**误植包的行为**：
- 功能与原包相似（包含相同函数名）
- 同时执行恶意代码（窃取环境变量、SSH 密钥、API 密钥）
- 发布初期在 PyPI 上搜索排名靠前

### Dependency Confusion（依赖混淆攻击）

当组织同时使用**内部私有仓库**和**公网 PyPI**时，攻击者可在公网发布与私包同名的恶意包：

```bash
# 攻击前：内部使用私有包
pip install mycompany-internal-utils  # 从内部仓库安装

# 攻击后：pip 优先使用公网包
pip install mycompany-internal-utils  # 安装了攻击者的恶意版本！
```

**攻击原理**：

```
pip install 命令
       │
       ▼
检查 PYPI（公网）◄────────── 攻击者发布同名恶意包
       │
       ▼（如果私包不同名或不存在）
检查内部仓库
```

### 防御策略

#### 1. 包名验证

```bash
# 安装前检查包的真伪
pip install pip check    # 验证已安装包的依赖完整性

# 使用 hash 验证（pip 9+）
pip install package==1.0.0 --require-hashes

# 或使用 pip-audit 检查已知漏洞
pip install pip-audit
pip-audit
```

#### 2. 使用 pip 的安全选项

```bash
# 仅从指定索引安装
pip install package --index-url https://pypi.org/simple/

# 禁止从其他索引安装
pip install package --no-index

# 使用 trusted-host 验证
pip install package --trusted-host pypi.org
```

#### 3. 私包命名规范

```toml
# pyproject.toml - 使用作用域避免冲突
[project]
name = "mycompany-utils"        # ❌ 易受攻击
name = "@mycompany/utils"       # ✅ 作用域包，PyPI 独有
```

#### 4. CI/CD 验证

```yaml
# GitHub Actions 示例
- name: Verify dependencies
  run: |
    pip install pip-audit
    pip-audit --fail-on-vulns

- name: Verify package hashes
  run: |
    pip hash package.whl
```

#### 5. 代码执行前验证

```python
# 安装后检查包来源
import importlib.metadata

dist = importlib.metadata.distribution("requests")
print(f"Name: {dist.name}")
print(f"Version: {dist.version}")
print(f"Files: {dist.files}")  # 检查安装文件

# 验证包的签名（如果使用 pip 21.0+）
```

### 检测工具

| 工具 | 用途 |
|------|------|
| `pip-audit` | 扫描已知漏洞 |
| `pipcheck` | 检查依赖完整性 |
| `safety` | 商业级漏洞扫描 |
| `pyup` | 自动更新有漏洞的依赖 |
| `snyk` | 全面依赖分析（商业） |

### 安全 checklist

```markdown
□ 使用 `--index-url` 明确指定包索引
□ 内部包使用作用域命名 `@company/`
□ 启用 pip 的 `--require-hashes` 模式
□ 定期运行 `pip-audit` 扫描漏洞
□ 在 CI/CD 中验证依赖完整性
□ 避免使用 `pip search`（已禁用）
□ 使用虚拟环境隔离依赖
□ 审核所有自动执行的 setup.py / __init__.py
```

`★ Insight ─────────────────────────────────────`
- **永远不要**假设包名是唯一的——pip 不验证包所有权
- 作用域包（`@scope/name`）是防止名字冲突的最有效方式
- 供应链攻击的受害者往往是开发者而非最终用户
- 最安全的做法：验证所有依赖，包括传递依赖
`─────────────────────────────────────────────────`

---

## 附录：关键参考

| 资源 | 链接 |
|------|------|
| Python Tutorial: Modules | https://docs.python.org/3/tutorial/modules.html |
| Python Reference: import | https://docs.python.org/3/reference/import.html |
| PEP 420 (Namespace Packages) | https://peps.python.org/pep-0420/ |
| PEP 451 (ModuleSpec) | https://peps.python.org/pep-0451/ |
| Python Packaging User Guide | https://packaging.python.org/ |
| Real Python: Import System | https://realpython.com/python-import/ |

---

## 附录：自测题

### 选择题

**Q1.** 当执行 `import mypackage` 时，Python 首先检查哪个位置？

A) `sys.path` 中的目录
B) `sys.modules` 缓存
C) 当前工作目录
D) Python 安装目录

<details>
<summary>答案</summary>
**B) `sys.modules` 缓存** — Python 首先检查模块是否已加载
</details>

---

**Q2.** 以下哪种情况会导致相对导入失败？

A) 使用 `python -m package.module` 运行
B) 使用 `python package/module.py` 直接运行
C) 模块中使用了 `from . import sibling`
D) 包目录下有 `__init__.py`

<details>
<summary>答案</summary>
**B) 使用 `python package/module.py` 直接运行** — 直接运行时 `__name__` 为 `"__main__"`，没有父包上下文
</details>

---

**Q3.** `__all__ = ["Foo", "bar"]` 的作用是？

A) 限制可导入的模块数量
B) 控制 `from module import *` 的行为
C) 阻止私有名称被导入
D) 自动导出所有名称

<details>
<summary>答案</summary>
**B) 控制 `from module import *` 的行为** — 定义公共 API，控制通配符导入
</details>

---

### 问答题

**Q4.** 解释为什么以下代码会出错：

```python
# a.py
from b import B

class A:
    b = B()

# b.py
from a import A

class B:
    a = A()
```

<details>
<summary>答案</summary>
**循环导入问题：**
1. `import a` 开始导入 a.py
2. a.py 的 `from b import B` 开始导入 b.py
3. b.py 的 `from a import A` 发现 a 已在 sys.modules 但未完成初始化
4. 尝试访问 `a.A`，但 a.py 尚未执行完
5. 报错：`AttributeError: partially initialized module 'a'`

**解决方案：**
- 使用 `import b` 替代 `from b import B`
- 使用 `TYPE_CHECKING` 隔离类型注解
- 将共享类提取到第三个模块
</details>

---

**Q5.** 说明 `src` 布局相比平铺布局的优势。

<details>
<summary>答案</summary>
`src` 布局优势：
1. **测试隔离**：测试运行时导入已安装的包，而非本地源码
2. **避免意外导入**：防止测试错误导入 `src/mypackage` 而非安装的包
3. **更真实场景**：模拟用户安装后的使用方式
4. **项目结构清晰**：源码在 src/ 下，与配置、文档、测试分离
</details>

---

**Q6.** 什么时候适合使用命名空间包（PEP 420）？

<details>
<summary>答案</summary>
适合场景：
1. **多个发行版共享顶级命名空间**：如 `namespace_pkg.sub_a` 和 `namespace_pkg.sub_b` 来自不同包
2. **纯 Python 3.3+ 项目**：不需要 Python 2 兼容
3. **不需要包级初始化**：无需 `__init__.py` 做配置

不适合场景：
- 需要 `__file__` 属性
- 需要包级初始化代码
- 单个发行版的包（用普通包更简单）
</details>

---

## 附录：AgentScope 源码对照

| 模块系统概念 | AgentScope 源码示例 | 位置 |
|-------------|---------------------|------|
| 包结构 | `agentscope/` 主包 | `src/agentscope/` |
| `__init__.py` | 包版本和公共 API | `src/agentscope/__init__.py` |
| `__main__.py` | CLI 入口点 | `src/agentscope/__main__.py` |
| 子包 | `agentscope.agent` | `src/agentscope/agent/` |
| 绝对导入 | 所有模块间导入 | `from agentscope.model import ...` |
| 相对导入 | 包内模块间导入 | `from .message import Msg` |
| `__all__` | 公共 API 定义 | 各 `__init__.py` 中 |
| `src` 布局 | 源码在 src/ 下 | `src/agentscope/` |
| 可编辑安装 | `pip install -e .` | `pyproject.toml` |

**AgentScope 的模块结构示例：**

```python
# src/agentscope/__init__.py
from .version import __version__
from .message import Msg
from .agent import AgentBase

__all__ = [
    "Msg",
    "AgentBase",
    "__version__",
    # ... 更多公共 API
]
```

```bash
# AgentScope 运行方式
python -m agentscope

# 可编辑安装
pip install -e .
```

---

## 附录：最佳实践检查清单

### Python 版本兼容性

| 功能 | 3.8 | 3.9 | 3.10 | 3.11 | 3.12 | 说明 |
|------|:---:|:---:|:---:|:---:|:---:|------|
| `__future__ annotations` | ✅ | ✅ | ✅ | ✅ | ✅ | 推迟注解求值 |
| `importlib.resources.files` | ❌ | ✅ | ✅ | ✅ | ✅ | 资源文件访问 |
| `importlib.metadata` | ✅ | ✅ | ✅ | ✅ | ✅ | 包元数据 |
| `TYPE_CHECKING` | ✅ | ✅ | ✅ | ✅ | ✅ | 类型注解隔离 |
| `__getattr__` 模块级 | ✅ | ✅ | ✅ | ✅ | ✅ | 延迟加载 |
| 命名空间包 (PEP 420) | ✅ | ✅ | ✅ | ✅ | ✅ | 无 `__init__.py` 包 |
| ModuleSpec (PEP 451) | ✅ | ✅ | ✅ | ✅ | ✅ | 模块规格 |
| `-X importtime` | ✅ | ✅ | ✅ | ✅ | ✅ | 导入时间分析 |
| `importlib.metadata.packages_distributions` | ❌ | ❌ | ❌ | ❌ | ✅ | 包依赖关系 |

### 反模式 vs 最佳实践对照

| 反模式 | 问题 | 最佳实践 |
|--------|------|----------|
| `from module import *` | 命名空间污染 | `from module import specific` |
| 创建 `math.py` 覆盖标准库 | 难以调试的错误 | 使用 `my_math.py` 或 `math_utils` |
| 在 `__init__.py` 导入所有子模块 | 导入速度慢 | 按需导入或延迟加载 |
| 运行时修改 `sys.path` | 脆弱、不安全 | 使用 `PYTHONPATH` 或安装包 |
| 直接运行 `python pkg/module.py` | 相对导入失败 | 使用 `python -m pkg.module` |
| 在 `__init__.py` 执行 I/O | 副作用、启动慢 | 保持最小化 |
| 用局部导入长期掩盖循环依赖 | 设计问题信号 | 重构模块边界 |
| 隐式相对导入 | Python 3 已禁用 | 显式绝对或相对导入 |

### 开发检查清单

```markdown
## 新包开发检查清单

- [ ] 使用 `src` 布局
- [ ] `__init__.py` 保持最小化
- [ ] 定义 `__all__` 显式公共 API
- [ ] 使用绝对导入（包外）/ 相对导入（包内）
- [ ] 避免循环导入（重构或 TYPE_CHECKING）
- [ ] 不创建与标准库同名的模块
- [ ] 不在 `__init__.py` 执行 I/O
- [ ] 可选依赖使用 `try/except`
- [ ] 使用 `python -m` 运行包内模块
- [ ] 配置 `pyproject.toml`
- [ ] 编写导入测试
- [ ] 使用虚拟环境

## 发布前检查清单

- [ ] `python -m build` 构建成功
- [ ] 在虚拟环境中测试安装
- [ ] `import mypackage` 无警告
- [ ] 所有子模块可单独导入
- [ ] `__version__` 正确
- [ ] 文档完整
```

`★ Insight ─────────────────────────────────────`
- Python 3.9+ 推荐使用 `importlib.resources.files`
- Python 3.12+ 有更好的错误信息和 `importlib.metadata` 性能
- 开发检查清单可确保遵循最佳实践
`─────────────────────────────────────────────────`

## 附录：快速参考卡

### 导入命令速查

| 命令 | 用途 |
|------|------|
| `import module` | 导入模块 |
| `from module import name` | 导入特定名称 |
| `from module import *` | 导入所有公共名称（不推荐）|
| `import module as m` | 导入并起别名 |
| `from . import sibling` | 相对导入（同级）|
| `from .. import parent` | 相对导入（上级）|
| `importlib.import_module(name)` | 动态导入 |

### `__init__.py` 速查

```python
# 最小化模板
from .core import API
__all__ = ["API"]
__version__ = "1.0.0"

# 延迟加载模板
_LAZY = {"Heavy": ("pkg.heavy", "Heavy")}
def __getattr__(name):
    if name in _LAZY:
        mod, attr = _LAZY[name].split(":")
        val = getattr(__import__(mod, fromlist=[attr]), attr)
        globals()[name] = val
        return val
    raise AttributeError(name)
```

### sys.path 速查

| 位置 | 说明 |
|------|------|
| `''`（空字符串）| 当前目录 |
| `PYTHONPATH` | 环境变量目录 |
| `site-packages` | 第三方包 |
| 标准库目录 | 内置模块 |

### 调试命令速查

```bash
# 查看 sys.path
python -c "import sys; print(sys.path)"

# 详细导入追踪
python -v -c "import module"

# 导入时间分析
python -X importtime -c "import module"

# 查找模块位置
python -c "import module; print(module.__file__)"

# 检查包安装
pip show package-name
```

### PEP 速查

| PEP | 主题 |
|-----|------|
| 302 | import 钩子机制 |
| 328 | 相对/绝对导入 |
| 366 | `__package__` 与 `-m` |
| 420 | 命名空间包 |
| 451 | ModuleSpec |
| 562 | 模块级 `__getattr__` |
| 563 | 推迟注解求值 |
| 660 | 可编辑安装 |
| 690 | 延迟导入（已被拒绝）|
| 810 | **✅ 已批准** 显式延迟导入（`lazy` 关键字，Python 3.15+）|
| 749 | importlib.metadata 改进 |

### 常见错误速查

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| `ModuleNotFoundError` | 模块不存在 | 检查 `sys.path` |
| `ImportError: attempted relative import with no known parent package` | 直接运行模块 | 使用 `python -m` |
| `AttributeError: partially initialized module` | 循环导入 | 重构或 `TYPE_CHECKING` |
| `ImportError: cannot import name` | 导入顺序/循环 | 改用 `import module` |

### 下划线速查

| 形式 | 含义 |
|------|------|
| `name` | 公共 API |
| `_name` | 约定私有 |
| `__name` | 名称修饰 |
| `__name__` | 魔术属性 |
| `__all__` | 公共 API 列表 |
| `__version__` | 版本字符串 |
| `__init__.py` | 包初始化 |
| `__main__.py` | 包入口点 |
| `__path__` | 包搜索路径 |
| `__spec__` | 模块规格 |

---

*教程完*
