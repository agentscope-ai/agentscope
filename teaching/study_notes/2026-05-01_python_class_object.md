# 学习笔记：Python 类与对象

**日期**：2026/05/01
**学习阶段**：阶段一 - Python 基础
**学习目标**：深入源码

---

## 1. 类与对象基础

### 核心问题：Python 成员变量不用声明？

**解答**：是的，Python 不需要预先声明成员变量，直接赋值即可创建。

```python
class Person:
    def __init__(self, name: str, age: int) -> None:
        self.name = name   # ✅ 直接赋值，创建属性
        self.age = age     # ✅ 直接赋值，创建属性
```

| Java | Python |
|------|--------|
| 必须先声明 `private String name;` | 直接 `self.name = name` |
| 静态语言 | 动态语言 |

**为什么？** Python 是动态语言，成员变量在首次赋值时创建，更灵活。

---

## 2. None 是什么？

**解答**：`None` 是 Python 的"空值"，相当于 Java 的 `null`。

```python
result = None
if result is None:
    print("没有结果")
```

| Python | Java |
|--------|------|
| `None` | `null` |
| `is None` | `== null` |

**注意**：判断用 `is None`，不用 `== None`（身份比较 vs 值比较）

---

## 3. void 呢？

**解答**：Python 没有 `void` 关键字，用 `-> None` 或省略表示无返回值。

```python
# Python - 两者等价
def greet() -> None:
    print("Hello")

def greet():      # 省略 -> None，效果相同
    print("Hello")
```

| Java | Python |
|------|--------|
| `void do_something()` | `def do_something() -> None:` |

---

## 4. 继承与多态

### 什么是继承？

继承是面向对象的**"is-a"**关系：子类复用父类的代码。

```python
class Animal:
    def __init__(self, name: str) -> None:
        self.name = name
    
    def eat(self) -> str:
        return f"{self.name} is eating"

class Dog(Animal):
    def __init__(self, name: str, breed: str) -> None:
        super().__init__(name)  # 调用父类构造器
        self.breed = breed

    def bark(self) -> str:
        return f"{self.name} says woof!"
```

### 多继承顺序（MRO）

多继承时，**先继承的类优先级更高**：

```python
class A:
    def greet(self) -> str:
        return "A"

class B:
    def greet(self) -> str:
        return "B"

class C(A, B):  # A 先继承，优先
    pass

print(C().greet())  # "A"
```

查看继承顺序：
```python
print(C.__mro__)  # (<class 'C'>, <class 'A'>, <class 'B'>, <class 'object'>)
```

| Python | Java |
|--------|------|
| `class Student(Person):` | `class Student extends Person {}` |
| `super().__init__(name, age)` | `super(name, age);` |
| 不需要 `@Override` 注解 | 需要 `@Override` |
| 支持**多继承** | 只支持**单继承+多接口** |

---

## 5. 抽象类与接口

Python 用抽象类代替接口：

```python
from abc import ABC, abstractmethod

class Animal(ABC):
    @abstractmethod
    def speak(self) -> str:
        """抽象方法 - 子类必须实现"""
        pass
    
    def common_method(self) -> str:
        """具体方法 - 子类可选重写"""
        return "I am an animal"

class Flyable(ABC):
    @abstractmethod
    def fly(self) -> None:
        pass

class Bird(Animal, Flyable):  # 多继承
    def speak(self) -> str:
        return "Tweet"
    
    def fly(self) -> None:
        print("Flying...")
```

---

## 6. @property（Pythonic 的 Getter/Setter）

Python 推荐用 `@property` 而不是 getter/setter 方法：

```python
class AgentBase:
    def __init__(self) -> None:
        self._disable_console_output = False

    @property
    def disable_console_output(self) -> bool:
        return self._disable_console_output

    @disable_console_output.setter
    def disable_console_output(self, value: bool) -> None:
        self._disable_console_output = value

# 使用 - 像访问属性一样
agent = AgentBase()
if agent.disable_console_output:  # 自动调用 @property
    agent.disable_console_output = True  # 自动调用 setter
```

| Python | Java |
|--------|------|
| `@property` | `getXxx()` |
| `@xxx.setter` | `setXxx()` |
| `agent.name` | `agent.getName()` |

---

## 关键概念对比表

| 概念 | Python | Java |
|------|--------|------|
| 空值 | `None` | `null` |
| 无返回值 | `-> None` 或省略 | `void` |
| 成员变量 | 直接 `self.xxx = value` | 必须先声明 |
| 继承 | `class A(B):` | `class A extends B {}` |
| 调用父类 | `super().__init__()` | `super()` |
| 抽象方法 | `@abstractmethod` | `abstract` |
| 抽象类 | `ABC` + `@abstractmethod` | `abstract class` |
| 接口 | 用抽象类代替 | `interface` |
| 多继承 | 支持 | 不支持 |

---

## 下一步学习

- 异步编程（async/await）
- 装饰器
- 类型提示

---

## Q&A 记录

1. **Q: Python 成员变量不用声明？** → 是的，直接赋值即可
2. **Q: None 是什么？** → 相当于 Java 的 null
3. **Q: void 呢？** → Python 用 -> None 或省略
4. **Q: 什么是继承？** → 子类复用父类代码，is-a 关系
5. **Q: 多继承顺序？** → 先继承的优先，可用 `__mro__` 查看
