# 第1章 Python面向对象编程

> **目标**：理解Python面向对象与Java的区别，掌握Agent开发所需的OOP知识

---

## 🎯 学习目标

学完之后，你能：
- 理解Python类与Java类的区别
- 掌握Python的继承和接口实现
- 理解Python的特殊方法（魔术方法）
- 阅读AgentScope源码中的面向对象设计

---

## 🚀 先跑起来

```python
from agentscope.message import Msg

# 创建消息 - 像创建Java对象一样
msg = Msg(
    name="user",
    content="你好，Agent！",
    role="user"
)

print(f"发送者: {msg.name}")
print(f"内容: {msg.content}")
```

**输出**：
```
发送者: user
内容: 你好，Agent！
```

---

## 🔍 Python类 vs Java类

### 基本语法对比

```python
# Python
class Agent:
    def __init__(self, name: str):  # 构造函数
        self.name = name  # self相当于Java的this
    
    def speak(self, content: str) -> None:
        print(f"{self.name}: {content}")

# Java
public class Agent {
    private String name;
    
    public Agent(String name) {
        this.name = name;
    }
    
    public void speak(String content) {
        System.out.println(this.name + ": " + content);
    }
}
```

**关键区别**：

| Python | Java | 说明 |
|--------|------|------|
| `self` | `this` | 实例引用 |
| `__init__` | 构造函数 | 初始化方法 |
| `def` | 返回类型 | 方法定义 |
| `: type` | `Type` | 类型注解 |
| `@property` | getter方法 | 属性访问 |

---

## 🔬 关键概念解析

### 1. self参数

Python方法必须显式声明`self`参数（相当于Java的`this`）：

```python
class Agent:
    def greet(self):  # self是必须的
        print(f"Hello, I am {self.name}")

agent = Agent()
agent.greet()  # self自动传入
```

**vs Java**：
```java
public class Agent {
    public void greet() {
        System.out.println("Hello, I am " + this.name);
        // this是隐式的
    }
}
```

### 2. 构造函数 `__init__`

```python
class Agent:
    def __init__(self, name: str, model: Any):
        self.name = name      # 创建实例属性
        self.model = model    # 赋值给self.xxx就是创建属性
        self._history = []    # 下划线前缀表示"私有"
```

**vs Java**：
```java
public class Agent {
    private String name;
    private Object model;
    private List history;
    
    public Agent(String name, Object model) {
        this.name = name;
        this.model = model;
        this.history = new ArrayList<>();
    }
}
```

### 3. 继承与接口

**Python继承**（类似Java）：
```python
class BaseAgent:
    def reply(self, msg: Msg) -> Msg:
        raise NotImplementedError()

class ReActAgent(BaseAgent):
    def reply(self, msg: Msg) -> Msg:
        # 重写父类方法
        return Msg(name=self.name, content="响应")
```

**接口实现**（Python用抽象类）：
```python
from abc import ABC, abstractmethod

class AgentBase(ABC):
    @abstractmethod
    def reply(self, msg: Msg) -> Msg:
        pass

class ReActAgent(AgentBase):
    def reply(self, msg: Msg) -> Msg:
        return Msg(name=self.name, content="响应")
```

**vs Java接口**：
```java
public interface AgentBase {
    Msg reply(Msg msg);
}

public class ReActAgent implements AgentBase {
    @Override
    public Msg reply(Msg msg) {
        return new Msg(this.name, "响应");
    }
}
```

### 4. 特殊方法（魔术方法）

Python的类可以定义特殊方法，以双下划线开头和结尾：

```python
class Msg:
    def __init__(self, name: str, content: str, role: str):
        self.name = name
        self.content = content
        self.role = role
    
    def __str__(self) -> str:  # 类似Java的toString()
        return f"Msg({self.name}, {self.content})"
    
    def __repr__(self) -> str:
        return f"Msg(name={self.name!r}, content={self.content!r})"
    
    def __eq__(self, other) -> bool:  # == 运算符
        return (self.name == other.name and 
                self.content == other.content)
    
    def __call__(self) -> str:  # 可调用对象
        return self.content
```

**常用魔术方法**：

| 方法 | 用途 | Java对应 |
|------|------|----------|
| `__init__` | 初始化 | 构造函数 |
| `__str__` | 字符串表示 | toString() |
| `__eq__` | `==`比较 | equals() |
| `__hash__` | 哈希 | hashCode() |
| `__call__` | 像函数一样调用 | 无直接对应 |
| `__enter__` | with语句入口 | try-with-resources |
| `__exit__` | with语句退出 | finally |

---

## 💡 Java开发者注意

### Python没有`public/private`关键字

Python用命名约定表示可见性：

```python
class Agent:
    def __init__(self):
        self.name = "public"       # 公开
        self._name = "protected"   # 受保护（约定）
        self.__name = "private"     # 名称重整为_Agent__name
```

### 多继承

Python支持多继承，Java不支持：

```python
class Agent:
    pass

class Talkative:
    def speak(self):
        print("Hello!")

class SocialAgent(Agent, Talkative):  # 多继承
    pass
```

### dataclass简化

Python 3.7+的dataclass减少样板代码：

```python
from dataclasses import dataclass

@dataclass
class Msg:
    name: str
    content: str
    role: str

# 自动生成 __init__, __str__, __eq__
msg = Msg(name="user", content="Hi", role="user")
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Python的方法第一个参数为什么必须是self？**
   - Python显式传递实例引用，不像Java是隐式的
   - 这让方法可以是普通函数，也可以赋值给变量

2. **Python如何实现"私有"属性？**
   - 单下划线`_name`是约定私有
   - 双下划线`__name`会名称重整，但仍可访问
   - 真正私有需要用`@property`或`__slots__`

3. **dataclass和普通类有什么区别？**
   - dataclass自动生成`__init__`, `__str__`, `__eq__`
   - 适合用于"数据容器"类

</details>

---

## 📚 扩展阅读

- [Python官方：数据类](https://docs.python.org/3/library/dataclasses.html)
- [Python面向对象编程](https://docs.python.org/3/tutorial/classes.html)

---

★ **Insight** ─────────────────────────────────────
- **Python的self** = Java的this，但必须显式声明
- **Python没有访问修饰符**，用命名约定代替
- **dataclass** = 自动生成样板代码的数据类
- **多继承**在Python中允许，但慎用
─────────────────────────────────────────────────
