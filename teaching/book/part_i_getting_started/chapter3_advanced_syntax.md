# 第3章 Python高级语法

> **目标**：掌握装饰器、上下文管理器和元类，理解AgentScope源码中的高级用法

---

## 🎯 学习目标

学完之后，你能：
- 理解装饰器原理并阅读装饰器代码
- 掌握上下文管理器用于资源管理
- 了解元类在框架设计中的应用
- 理解AgentScope中的钩子机制

---

## 🚀 先跑起来

```python
from agentscope import DS

# 装饰器：用@ds_agent标记Agent类
@DS
class MyAgent:
    def reply(self, msg):
        return f"Hello: {msg.content}"

# 上下文管理器：用with管理Runtime
with DS(agents=[MyAgent()]) as runtime:
    result = runtime.run()
```

---

## 🔍 装饰器（Decorator）

### 什么是装饰器

装饰器是"修饰函数的函数"，类似Java的AOP：

```python
# 装饰器定义
def log_calls(func):
    def wrapper(*args, **kwargs):
        print(f"调用 {func.__name__}")
        result = func(*args, **kwargs)
        print(f"返回 {result}")
        return result
    return wrapper

# 使用装饰器
@log_calls
def add(a, b):
    return a + b

# 等价于
def add(a, b):
    return a + b
add = log_calls(add)
```

### 带参数的装饰器

```python
def repeat(times):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for _ in range(times):
                result = func(*args, **kwargs)
            return result
        return wrapper
    return decorator

@repeat(3)
def greet():
    print("Hello!")

greet()  # 打印3次Hello!
```

### 类装饰器

```python
@dataclass
class AgentConfig:
    name: str
    model: str

# 等价于
class AgentConfig:
    def __init__(self, name: str, model: str):
        self.name = name
        self.model = model
```

### AgentScope中的装饰器

```python
# agentscope/_runtime.py
def ds_runtime(cls):
    """将类标记为Runtime并注册"""
    _runtime_registry[cls.__name__] = cls
    return cls

@ds_runtime
class Runtime:
    def run(self):
        pass
```

---

## 🔍 上下文管理器（Context Manager）

### 什么是上下文管理器

上下文管理器管理资源的获取和释放，类似Java的try-with-resources：

```python
# Python with语句
with open("file.txt") as f:
    content = f.read()

# 等价于Java
try (var f = new FileReader("file.txt")) {
    String content = f.read();
}
```

### 自定义上下文管理器

**方式1：类实现**
```python
class MsgHub:
    def __init__(self, participants):
        self.participants = participants
    
    def __enter__(self):  # 进入with时调用
        # 设置订阅关系
        for agent in self.participants:
            agent.subscribe(self)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):  # 退出with时调用
        # 清理订阅关系
        for agent in self.participants:
            agent.unsubscribe(self)

# 使用
async with MsgHub(participants=[a1, a2]) as hub:
    await hub.broadcast(msg)
```

**方式2：async实现**
```python
class AsyncMsgHub:
    async def __aenter__(self):
        # 异步初始化
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 异步清理
        await self.disconnect()

async with AsyncMsgHub() as hub:
    await hub.broadcast(msg)
```

### @contextmanager

用生成器简化上下文管理器：

```python
from contextlib import contextmanager

@contextmanager
def timer():
    start = time.time()
    yield  # 暂停，执行with块内代码
    elapsed = time.time() - start
    print(f"耗时: {elapsed:.2f}秒")

with timer():
    # 这段代码计时
    result = expensive_operation()
```

---

## 🔍 元类（Metaclass）

### 什么是元类

元类是"创建类的类"，类似Java的Class类：

```python
# type是内置元类
MyClass = type("MyClass", (), {"x": 1})

# 相当于
class MyClass:
    x = 1
```

### 自定义元类

```python
class AgentMeta(type):
    """Agent的元类 - 自动注册Agent类"""
    _registry = {}
    
    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        # 自动注册Agent类
        if attrs.get('__agent_name__'):
            AgentMeta._registry[attrs['__agent_name__']] = cls
        return cls

class BaseAgent(metaclass=AgentMeta):
    pass

class MyAgent(BaseAgent, __agent_name__="my_agent"):
    pass

# 自动注册
print(AgentMeta._registry)  # {'my_agent': MyAgent}
```

### AgentScope中的元类

```python
# _agent_meta.py
class _AgentMeta(type):
    """ReActAgent使用的元类"""
    
    def __new__(mcs, name, bases, attrs):
        cls = super().__new__(mcs, name, bases, attrs)
        
        # 扫描并注册Hook
        for attr_name in dir(cls):
            attr = getattr(cls, attr_name)
            if hasattr(attr, '_hook_marker'):
                cls._hooks.append(attr)
        
        return cls

class ReActAgentBase(metaclass=_AgentMeta):
    _hooks = []
```

---

## 💡 Java开发者注意

| Python特性 | Java对应 | 说明 |
|-----------|----------|------|
| `@decorator` | AOP切面 | 装饰器是编译时/运行时拦截 |
| `with` | try-with-resources | 上下文管理器自动资源清理 |
| `metaclass` | Class对象 | 元类是Class的Class |
| `@dataclass` | Lombok | 自动生成样板代码 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **装饰器和继承有什么区别？**
   - 装饰器在运行时修改行为，不改变类结构
   - 继承在编译时决定，静态的
   - 装饰器更灵活，可叠加使用

2. **为什么需要上下文管理器？**
   - 确保资源被正确释放
   - 避免忘记close()导致的资源泄漏
   - 异常情况下也能清理资源

3. **元类有什么实际用途？**
   - 自动注册：框架自动发现并注册组件
   - 验证：检查类是否符合规范
   - 注入：自动添加属性或方法

</details>

---

★ **Insight** ─────────────────────────────────────
- **装饰器** = 修改函数行为的"包装器"，用`@`语法糖
- **上下文管理器** = 自动资源管理，用`with`确保清理
- **元类** = 控制类的创建，用于框架自动注册
- **DS装饰器** = AgentScope的简化和注册机制
─────────────────────────────────────────────────
