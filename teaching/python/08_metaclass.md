# 08 - 元类 (Metaclass)

## 概念理解

元类是"类的类"。在 Java 中没有直接对应，但可以类比：

| 概念 | Python | Java |
|------|--------|------|
| 类 | `class MyClass` | `class MyClass` |
| 类的类（元类） | `type` 或自定义 | `Class<?> object.getClass()` |
| 创建实例 | `obj = MyClass()` | `MyClass obj = new MyClass()` |
| 创建类 | `MyClass = type('MyClass', (), {})` | 编译时确定 |

```python
# 类是元类的实例
class MyClass:
    pass

print(type(MyClass))  # <class 'type'>
# MyClass 是 type 类的一个实例

# type 可以直接创建类
MyClass = type('MyClass', (), {})
print(MyClass)  # <class '__main__.MyClass'>
```

`★ Insight ─────────────────────────────────────`
- 默认情况下，所有类的元类都是 `type`
- 自定义元类可以控制类的创建过程
- 类似于 Java 注解处理器或 Spring AOP 拦截器
- 元类 vs 装饰器：元类在类创建时干预，装饰器在类创建后包装
`─────────────────────────────────────────────────`

## 为什么需要元类？

```python
# 问题：想要自动注册所有子类到一个注册表

# Java 方式：手动注册
class Animal:
    pass

class Dog(Animal):
    pass

class Cat(Animal):
    pass

# 需要手动维护注册表
ANIMAL_REGISTRY = {"dog": Dog, "cat": Cat}

# Python 元类方式：自动注册
class RegistryMeta(type):
    _registry = {}  # 必须初始化！

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        # 自动注册
        if hasattr(cls, 'registry_key'):
            RegistryMeta._registry[cls.registry_key] = cls
        return cls

class Animal(metaclass=RegistryMeta):
    registry_key = None

class Dog(Animal):
    registry_key = "dog"

class Cat(Animal):
    registry_key = "cat"

print(RegistryMeta._registry)  # {"dog": Dog, "cat": Cat}
```

## AgentScope 源码示例

**文件**: `src/agentscope/agent/_agent_meta.py:159`

```python
class _AgentMeta(type):
    """The agent metaclass that wraps the agent's reply, observe and print
    functions with pre- and post-hooks."""

    def __new__(mcs, name: Any, bases: Any, attrs: Dict) -> Any:
        """Wrap the agent's functions with hooks."""

        for func_name in [
            "reply",
            "print",
            "observe",
        ]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])

        return super().__new__(mcs, name, bases, attrs)


class _ReActAgentMeta(_AgentMeta):
    """The ReAct metaclass that adds pre- and post-hooks for the _reasoning
    and _acting functions."""

    def __new__(mcs, name: Any, bases: Any, attrs: Dict) -> Any:
        """Wrap the ReAct agent's _reasoning and _acting functions with
        hooks."""

        for func_name in [
            "_reasoning",
            "_acting",
        ]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])

        return super().__new__(mcs, name, bases, attrs)
```

**文件**: `src/agentscope/agent/_agent_base.py:30`

```python
class AgentBase(StateModule, metaclass=_AgentMeta):
    """Base class for asynchronous agents."""
    # 使用 _AgentMeta 元类自动包装 reply/print/observe 方法
    pass
```

**Java 对照理解**：

```java
// Java 没有语法层面的元类支持，只能用设计模式模拟部分功能

// _AgentMeta 的核心功能是为方法添加 pre/post hooks
// 这在 Java 中可以用代理模式或装饰器模式近似实现：

// 方式1：使用代理模式（需要接口）
public interface AgentInterface {
    Message reply(Message input);
}

public class AgentBase implements AgentInterface {
    @Override
    public Message reply(Message input) {
        // 手动调用 pre hook
        preReplyHook(input);
        Message result = doReply(input);
        // 手动调用 post hook
        postReplyHook(result);
        return result;
    }

    protected void preReplyHook(Message input) { }
    protected void postReplyHook(Message result) { }
    protected Message doReply(Message input) { return input; }
}

// 方式2：使用 Spring AOP 或 AspectJ（运行时织入）
// @Aspect
// @Component
// public class AgentAspect {
//     @Around("execution(* AgentBase.reply(..))")
//     public Object aroundReply(ProceedingJoinPoint pjp) { ... }
// }
```

**总结**：`_AgentMeta` 的 AOP 式 hooks 在 Java 中没有直接等价物，需要借助框架支持。

## 元类语法

```python
class MyMeta(type):
    """自定义元类"""
    def __new__(mcs, name, bases, namespace):
        """创建类时调用"""
        # mcs: 元类实例（元类本身）
        # name: 类名
        # bases: 基类元组
        # namespace: 类属性字典
        cls = super().__new__(mcs, name, bases, namespace)
        return cls

    def __call__(cls, *args, **kwargs):
        """实例化类时调用"""
        instance = super().__call__(*args, **kwargs)
        return instance

# 使用元类
class MyClass(metaclass=MyMeta):
    pass
```

## 三个特殊方法详解

```python
class MyMeta(type):
    def __new__(mcs, name, bases, namespace):
        """创建类时调用 - 类似于 Java 类加载时的处理"""
        print(f"1. Creating class: {name}")
        return super().__new__(mcs, name, bases, namespace)

    def __init__(cls, name, bases, namespace):
        """类创建后的初始化"""
        print(f"2. Initializing class: {name}")
        super().__init__(name, bases, namespace)

    def __call__(cls, *args, **kwargs):
        """实例化类时调用 - 类似于 Java 构造函数"""
        print(f"3. Instantiating: {cls.__name__}")
        return super().__call__(*args, **kwargs)


class MyClass(metaclass=MyMeta):
    def __init__(self, x):
        self.x = x


print("---")
obj = MyClass(10)  # 触发 __call__
print("---")
# 输出:
# 1. Creating class: MyClass
# 2. Initializing class: MyClass
# ---
# 3. Instantiating: MyClass
```

### 执行顺序图解

```
class MyClass(metaclass=MyMeta):
    │
    ▼
1. Python 执行类体（定义属性方法）
    │
    ▼
2. 调用 MyMeta.__new__(mcs, "MyClass", (), {...})
    │
    ▼
3. 调用 MyMeta.__init__(cls, "MyClass", (), {...})
    │
    ▼
MyClass 实例化时
    │
    ▼
4. 调用 MyMeta.__call__(cls, *args, **kwargs)
```

## 实际应用场景

### 1. 单例模式

```python
class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


class Database(metaclass=SingletonMeta):
    pass


db1 = Database()
db2 = Database()
print(db1 is db2)  # True - 同一个实例
```

### 2. 自动注册

```python
class RegistryMeta(type):
    _registry = {}

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)

        # 检查是否有 registry_key 属性
        registry_key = namespace.get('registry_key')
        if registry_key:
            mcs._registry[registry_key] = cls

        return cls


class Plugin(metaclass=RegistryMeta):
    registry_key = None


class TextPlugin(Plugin):
    registry_key = "text"


class ImagePlugin(Plugin):
    registry_key = "image"


print(RegistryMeta._registry)
# {'text': <class 'TextPlugin'>, 'image': <class 'ImagePlugin'>}

# 根据 key 获取类
def get_plugin(key: str):
    return RegistryMeta._registry.get(key)
```

### 3. ORM 模型（类似 Hibernate）

```python
class ModelMeta(type):
    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)

        # 自动设置表名
        if not hasattr(cls, '__table_name__'):
            cls.__table_name__ = name.lower()

        # 收集字段
        fields = {k: v for k, v in namespace.items()
                  if isinstance(v, Field)}
        cls._fields = fields

        return cls


class Field:
    def __init__(self, field_type: str):
        self.field_type = field_type


class User(metaclass=ModelMeta):
    name = Field("VARCHAR(255)")
    age = Field("INT")

    def __repr__(self):
        return f"User(table={self.__table_name__}, fields={list(self._fields.keys())})"


print(User())
# User(table=user, fields=['name', 'age'])
```

### 4. 字段验证

```python
class ValidatedMeta(type):
    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)

        # 检查所有方法是否有文档字符串
        for attr_name, attr_val in namespace.items():
            if callable(attr_val) and not attr_val.__doc__:
                print(f"Warning: {name}.{attr_name} has no docstring")

        return cls


class Service(metaclass=ValidatedMeta):
    def process(self):
        """处理业务逻辑"""
        pass

    def helper(self):  # 没有文档字符串
        pass
```

## 与 Java 对比

| Python | Java | 说明 |
|--------|------|------|
| `class A(metaclass=M)` | 不直接支持 | 元类语法 |
| `type.__new__` | `ClassLoader.defineClass` | 类创建 |
| `type.__init__` | 注解处理器 | 类初始化 |
| `type.__call__` | `Class.newInstance()` | 实例创建 |
| `_AgentMeta` | Spring AOP / AspectJ | AOP 拦截器 |

## 元类 vs 装饰器

```python
# 元类：控制类的创建
class Meta(type):
    def __new__(mcs, name, bases, namespace):
        # 类创建时的处理
        return super().__new__(mcs, name, bases, namespace)

# 装饰器：包装函数/类
def decorator(cls):
    # 类创建后的处理
    return cls

# 选择：
# - 元类：需要影响类的创建、注册
# - 装饰器：需要包装已创建的类
```

### 何时使用元类

```python
# 使用元类
class RegistryMeta(type):
    """自动注册 - 元类最合适"""
    _registry = {}
    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        if 'registry_key' in namespace:
            mcs._registry[namespace['registry_key']] = cls
        return cls

# 使用装饰器
def add_logging(cls):
    """为类的方法添加日志 - 装饰器更合适"""
    for name in dir(cls):
        if not name.startswith('_'):
            setattr(cls, name, log_wrapper(getattr(cls, name)))
    return cls
```

## 常见问题

### 元类继承

```python
class BaseMeta(type):
    def __new__(mcs, name, bases, namespace):
        print(f"Creating {name}")
        return super().__new__(mcs, name, bases, namespace)

class DerivedMeta(BaseMeta):
    """继承自 BaseMeta"""
    pass

class MyClass(metaclass=DerivedMeta):
    pass
# 输出: Creating MyClass
# DerivedMeta 继承自 BaseMeta，行为一致
```

### __prepare__ 方法

```python
# __prepare__ 在 __new__ 之前调用，返回 namespace
class OrderedMeta(type):
    @classmethod
    def __prepare__(mcs, name, bases, **kwargs):
        return OrderedDict()

    def __new__(mcs, name, bases, namespace):
        # namespace 是 OrderedDict，保持属性定义顺序
        cls = super().__new__(mcs, name, bases, dict(namespace))
        cls._order = list(namespace.keys())
        return cls

class MyClass(metaclass=OrderedMeta):
    a = 1
    b = 2
    c = 3

print(MyClass._order)  # ['a', 'b', 'c']
```

## 练习题

1. **创建单例元类**：使用元类实现单例模式

2. **自动注册**：创建 `RegistryMeta`，自动将带有 `registry_key` 属性的类注册

3. **理解执行顺序**：以下代码输出什么？
   ```python
   class Meta(type):
       def __new__(m, n, b, ns):
           print(f"Creating {n}")
           return super().__new__(m, n, b, ns)

   class A(metaclass=Meta):
       print("A class body")
       pass
   ```

4. **创建 Hook 元类**：创建一个元类，自动为所有方法添加调用日志

5. **理解 MRO 和元类**：
   ```python
   class A:
       pass

   class B:
       pass

   class C(A, B):
       pass

   print(C.__mro__)  # 输出什么？
   ```

---

**答案**：

```python
# 1. 单例元类
class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]

# 2. 自动注册
class RegistryMeta(type):
    _registry = {}

    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        key = namespace.get('registry_key')
        if key:
            mcs._registry[key] = cls
        return cls

# 3. 输出:
# A class body  # 类体在元类 __new__ 之前执行
# Creating A
# 因为 Python 先执行类体（定义属性方法），然后才调用元类创建类

# 4. Hook 元类
import functools

class HookMeta(type):
    def __new__(mcs, name, bases, namespace):
        for attr_name, attr_val in namespace.items():
            if callable(attr_val) and not attr_name.startswith('_'):
                namespace[attr_name] = mcs._wrap_method(attr_val)
        return super().__new__(mcs, name, bases, namespace)

    @staticmethod
    def _wrap_method(method):
        @functools.wraps(method)
        def wrapper(*args, **kwargs):
            print(f"Calling {method.__name__}")
            return method(*args, **kwargs)
        return wrapper

# 5.
# 输出: (<class 'C'>, <class 'A'>, <class 'B'>, <class 'object'>)
# Python 使用 C3 线性化算法计算 MRO
# 此例中无菱形继承，结果等价于深度优先、从左到右
```
