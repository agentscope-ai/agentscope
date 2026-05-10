# 1-0 Python基础速览

> **目标**：45分钟内掌握Python核心语法，为后续学习AgentScope打下基础

---

## 学习目标

学完之后，你能：
- 理解Python的基本语法与Java的区别
- 写出会动的Python代码（不用等到第2章）
- 掌握Python特有的缩进、None、动态类型
- 理解async/await异步编程基础

---

## 背景问题

**为什么需要先学Python基础？**

AgentScope是Python框架，要用它开发Agent必须先掌握Python语法。本章针对有Java背景的开发者，快速定位Python与Java的差异。

**Python vs Java核心差异速览**:

| 特性 | Python | Java |
|------|--------|------|
| 类型系统 | 动态类型 | 静态类型 |
| 代码块 | 缩进 | 大括号 |
| 空值 | None | null |
| 布尔值 | True/False (首字母大写) | true/false |
| 方法self | 必须显式声明 | 隐式this |
| 访问控制 | 下划线约定 | public/private/protected |

---

## 核心概念对比

### 缩进 vs 大括号

Python用缩进表示代码块，缩进就是语法。

```python
# Python - 缩进就是语法
if message:
    print("有消息")
    print("这是消息内容")
else:
    print("没有消息")
```

Java用大括号，缩进只是风格。

```java
// Java - 大括号表示代码块
if (message != null) {
    System.out.println("有消息");
    System.out.println("这是消息内容");
} else {
    System.out.println("没有消息");
}
```

### None vs null

```python
# Python判断None必须用is
result = None
if result is None:
    print("没有结果")
```

```java
// Java用==
Object result = null;
if (result == null) {
    System.out.println("没有结果");
}
```

**为什么用`is`而不是`==`？**

`is`比较对象身份（内存地址），`==`比较值。None是单例对象，用`is`更准确。

### 动态类型

Python变量类型可以随时变：

```python
x = 1      # x是int
x = "hello" # x变成str了
x = [1, 2, 3]  # x又变成list了
```

Java类型固定：

```java
int x = 1;
x = "hello";  // 编译错误！
```

---

## 变量和数据类型

### 基本数据类型

| Python | Java | 示例 |
|--------|------|------|
| `int` | `int` | `age = 25` |
| `float` | `double` | `temperature = 36.6` |
| `str` | `String` | `name = "Alice"` |
| `bool` | `boolean` | `is_active = True` |
| `None` | `null` | `result = None` |

### 容器类型

| Python | Java | 说明 |
|--------|------|------|
| `list` | `List` | 列表（有序可变） |
| `dict` | `Map` | 字典（键值对） |
| `set` | `Set` | 集合（无序不重复） |
| `tuple` | 不常用 | 元组（不可变） |

```python
# 列表
fruits = ["苹果", "香蕉", "橙子"]
fruits.append("葡萄")

# 字典
person = {"name": "Alice", "age": 25}
person["city"] = "北京"

# 集合
colors = {"红色", "绿色", "蓝色"}
colors.add("黄色")
```

---

## 控制流

### if语句

```python
age = 18

if age < 0:
    print("无效年龄")
elif age < 18:
    print("未成年")
else:
    print("成年人")
```

**注意**: Python用`elif`，Java用`else if`。

### for循环

Python的for是遍历，不是计数器：

```python
fruits = ["苹果", "香蕉", "橙子"]

# 遍历列表
for fruit in fruits:
    print(fruit)

# 如果需要索引，用enumerate
for i, fruit in enumerate(fruits):
    print(f"{i}: {fruit}")
```

**注意**: Python没有`i++`，用`i += 1`代替。

### while循环

```python
count = 0
while count < 5:
    print(count)
    count += 1
```

---

## 函数

### 定义函数

```python
def greet(name: str) -> str:
    """打招呼

    Args:
        name: 打招呼的名字

    Returns:
        问候语
    """
    return f"你好，{name}！"
```

**与Java对比**:
- Python不需要public/private
- 返回类型写在参数后面`: str`
- `-> str`表示返回类型

### 参数默认值

```python
def greet(name: str = "世界") -> str:
    return f"你好，{name}！"

print(greet())          # 你好，世界！
print(greet("Alice"))   # 你好，Alice！
```

### 可变参数

```python
# *args 接收任意数量的位置参数
def sum(*numbers):
    total = 0
    for n in numbers:
        total += n
    return total

print(sum(1, 2, 3))  # 6

# **kwargs 接收任意数量的关键字参数
def print_info(**info):
    for key, value in info.items():
        print(f"{key}: {value}")

print_info(name="Alice", age=25)
```

---

## 类和对象

### 定义类

```python
class Person:
    def __init__(self, name: str, age: int):
        self.name = name  # self必须显式声明
        self.age = age

    def greet(self) -> str:
        return f"你好，我是{self.name}"

# 创建对象 - 不需要new
person = Person("Alice", 25)
print(person.greet())
```

**注意**: `__init__`不是构造器（构造器是`__new__`），只是初始化方法。

### @property

Pythonic的getter/setter方式：

```python
class Person:
    def __init__(self, name: str):
        self._name = name  # 约定私有

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value

# 使用 - 像访问属性一样
person = Person("Alice")
print(person.name)      # 自动调用getter
person.name = "Bob"     # 自动调用setter
```

---

## 异步编程基础

### async/await

```python
import asyncio

async def fetch_data():
    # 模拟异步操作
    await asyncio.sleep(1)
    return "数据"

async def main():
    result = await fetch_data()
    print(result)

asyncio.run(main())
```

**与Java对比**:
- Python的`async`声明异步函数
- `await`等待异步结果，类似Java的`CompletableFuture.get()`
- `asyncio.run()`是入口，类似Java的main方法

---

## 工程经验

### 缩进错误是最常见的Python错误

```python
# ❌ 错误：混用空格和Tab
def greet():
    print("Hello")  # Tab
    print("World")  # 空格

# ✅ 正确：统一用4个空格
def greet():
    print("Hello")
    print("World")
```

### None判断必须用is

```python
# ❌ 危险
if x == None:
    print("x是None")

# ✅ 正确
if x is None:
    print("x是None")
```

### 动态类型的风险

```python
# ❌ 危险：类型错误在运行时才暴露
def calculate(a, b):
    return a + b

calculate("1", 2)  # TypeError

# ✅ 使用类型注解
def calculate(a: int, b: int) -> int:
    return a + b
```

---

## Contributor指南

### 适合新手修改的文件

| 文件 | 原因 |
|------|------|
| `examples/` | 示例代码，适合学习 |
| `tests/` | 测试用例，学习Python最佳实践 |
| `docs/` | 文档改进，不需要懂核心代码 |

### Python基础扩展方向

**1. 添加更多对比示例**:
```python
# 如果要添加Python vs Go对比
# 可以在文档末尾添加新表格
```

**2. 常见错误案例库**:
```python
# 创建 teaching/book/practice/station1_departure/python_errors.md
# 收集真实的Python错误和解决方案
```

### 学习路径建议

```
1-0 Python基础（本文档）
    ↓
1-1 环境搭建
    ↓
1-2 追踪第一个Agent
    ↓
Station 2+ 进入AgentScope核心学习
```

---

## 思考题

<details>
<summary>点击查看答案</summary>

1. **Python和Java在缩进上的最大区别是什么？**
   - Java用大括号`{}`表示代码块，缩进只是风格
   - Python用缩进表示代码块，缩进是语法要求

2. **如何判断一个Python变量是None？**
   - 用`if x is None`，不是`if x == None`
   - 因为`is`比较的是对象身份，`==`比较的是值

3. **Python的list和Java的List有什么区别？**
   - Python list用方括号`[1, 2, 3]`，Java用`new ArrayList<>()`
   - Python list是动态类型，Java List需要声明泛型类型
   - Python list可以存不同类型，Java List通常存相同类型

4. **为什么Python不需要public/private？**
   - Python没有访问修饰符关键字
   - 用下划线约定：`_name`约定私有，`__name`名称修饰
   - 真正私有靠程序员自觉，不是编译器强制

</details>
