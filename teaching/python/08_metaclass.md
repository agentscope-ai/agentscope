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
- 类似于 Java 注解处理器或 CGLIB 代理
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

**文件**: `src/agentscope/agent/_agent_meta.py`

```python
class _AgentMeta(type):
    """Agent 的元类 - 控制 Agent 子类的创建"""

    def __new__(mcs, name, bases, namespace):
        """创建类时的钩子 - 类似 Java 注解处理器"""
        cls = super().__new__(mcs, name, bases, namespace)

        # 自动注册类级别的 hooks
        for hook_name, hook_func in namespace.items():
            if hook_name.startswith("_class_") and callable(hook_func):
                # 处理类级别钩子
                ...

        return cls
```

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

## 三个特殊方法

```python
class MyMeta(type):
    def __new__(mcs, name, bases, namespace):
        """创建类时调用 - 类似于 Java 类加载时的处理"""
        print(f"Creating class: {name}")
        return super().__new__(mcs, name, bases, namespace)

    def __init__(cls, name, bases, namespace):
        """类创建后的初始化"""
        print(f"Initializing class: {name}")
        super().__init__(name, bases, namespace)

    def __call__(cls, *args, **kwargs):
        """实例化类时调用 - 类似于 Java 构造函数"""
        print(f" Instantiating: {cls.__name__}")
        return super().__call__(*args, **kwargs)


class MyClass(metaclass=MyMeta):
    def __init__(self, x):
        self.x = x


print("---")
obj = MyClass(10)  # 触发 __call__
print("---")
# 输出:
# Creating class: MyClass
# Initializing class: MyClass
# ---
#  Instantiating: MyClass
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

## 与 Java 对比

| Python | Java | 说明 |
|--------|------|------|
| `class A(metaclass=M)` | 不直接支持 | 元类语法 |
| `type.__new__` | `ClassLoader.defineClass` | 类创建 |
| `type.__init__` | 注解处理器 | 类初始化 |
| `type.__call__` | `Class.newInstance()` | 实例创建 |

## 常见问题

### 元类 vs 装饰器

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
```
