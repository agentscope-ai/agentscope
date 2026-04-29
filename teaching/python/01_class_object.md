# 01 - 类与对象

## Java 回顾

```java
// Java 类定义
public class Person {
    private String name;
    private int age;

    // 构造器
    public Person(String name, int age) {
        this.name = name;
        this.age = age;
    }

    // Getter/Setter
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }

    // 方法
    public String greet() {
        return "Hello, I'm " + name;
    }
}

// 使用
Person p = new Person("Alice", 25);
p.greet();
```

## Python 类定义

### 基础类定义

```python
# Python 类定义
class Person:
    # 类属性（类似 Java static 字段）
    species = "Human"

    # 实例方法（类似 Java 实例方法）
    def __init__(self, name: str, age: int) -> None:
        """构造器 - Java 的 constructor"""
        self.name = name      # 触发 @name.setter（见下方）
        self.age = age

    # 实例方法
    def greet(self) -> str:
        """方法"""
        return f"Hello, I'm {self.name}"

    # Getter/Setter（Python 推荐 @property）
    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value

# 使用
p = Person("Alice", 25)  # 不需要 new
p.greet()  # "Hello, I'm Alice"
```

`★ Insight ─────────────────────────────────────`
- `__init__` 不是构造器，真正的构造器是 `__new__`（很少用）
- `self` 必须显式声明，类似 Java 的 `this`
- 不需要声明public/private，约定用 `_` 前缀表示"私有"
`─────────────────────────────────────────────────`

### AgentScope 源码示例

> **提示**：以下示例使用了 `Literal`、`Sequence`、`|` 联合类型等类型注解，将在 [04 - 类型提示](04_type_hints.md) 中详细讲解。

**文件**: `src/agentscope/message/_message_base.py:21-73`

```python
class Msg:
    """消息类 - AgentScope 中的核心消息对象"""

    def __init__(
        self,
        name: str,
        content: str | Sequence[ContentBlock],
        role: Literal["user", "assistant", "system"],
        metadata: dict[str, JSONSerializableObject] | None = None,
        timestamp: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        """初始化消息

        Args:
            name: 发送者名称
            content: 消息内容
            role: 角色（user/assistant/system）
            metadata: 元数据
            timestamp: 时间戳
            invocation_id: 调用ID
        """
        self.name = name
        self.content = content
        self.role = role
        self.metadata = metadata or {}  # None 合并
        self.id = shortuuid.uuid()
        self.timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        self.invocation_id = invocation_id
```

**Java 对照理解**：
```java
// 相当于 Java 的:
public class Msg {
    private String name;
    private String content;
    private String role;
    private Map<String, Object> metadata;
    private String id;
    private String timestamp;

    public Msg(String name, String content, String role) {
        this.name = name;
        this.content = content;
        this.role = role;
        this.metadata = new HashMap<>();
        this.id = UUID.randomUUID().toString();
        this.timestamp = LocalDateTime.now().toString();
    }
}
```

### 继承与多态

```python
# Python 继承（类似 Java extends）
class Student(Person):
    def __init__(self, name: str, age: int, grade: int) -> None:
        super().__init__(name, age)  # 调用父类构造器
        self.grade = grade

    # 方法重写（类似 Java @Override）
    def greet(self) -> str:
        return f"Hi, I'm {self.name} in grade {self.grade}"

# 多态 - Python 天然支持
def introduce(person: Person) -> None:
    print(person.greet())

student = Student("Bob", 15, 9)
introduce(student)  # "Hi, I'm Bob in grade 9"
```

**Java 对照**：

```java
public class Student extends Person {
    private int grade;

    public Student(String name, int age, int grade) {
        super(name, age);
        this.grade = grade;
    }

    @Override
    public String greet() {
        return "Hi, I'm " + name + " in grade " + grade;
    }
}

// Java 多态
public static void introduce(Person p) {
    System.out.println(p.greet());
}
```

### 抽象类与接口

```python
from abc import ABC, abstractmethod

# Python 抽象类（类似 Java 抽象类）
class Animal(ABC):
    @abstractmethod
    def speak(self) -> str:
        """抽象方法 - 子类必须实现"""
        pass

    def common_method(self) -> str:
        """具体方法 - 子类可选重写"""
        return "I am an animal"

# Python 没有接口关键字，用抽象类代替（类似 Java 接口）
class Flyable(ABC):
    @abstractmethod
    def fly(self) -> None:
        pass

class Bird(Animal, Flyable):
    def speak(self) -> str:
        return "Tweet"

    def fly(self) -> None:
        print("Flying...")

# 使用
bird = Bird()
print(bird.speak())     # "Tweet"
print(bird.common_method())  # "I am an animal"
bird.fly()              # "Flying..."
```

**Java 对照**：

```java
// Java 抽象类
abstract class Animal {
    abstract String speak();
    String commonMethod() { return "I am an animal"; }
}

// Java 接口
interface Flyable {
    void fly();
}

// Java 多实现
class Bird extends Animal implements Flyable {
    @Override
    String speak() { return "Tweet"; }

    @Override
    public void fly() { System.out.println("Flying..."); }
}
```

## 实例属性 vs 类属性

```python
class AgentBase:
    # 类属性 - 所有实例共享（类似 Java static）
    supported_hook_types: list[str] = [
        "pre_reply",     # 回复前
        "post_reply",    # 回复后
        "pre_print",     # 打印前
        "post_print",    # 打印后
        "pre_observe",   # 观察前
        "post_observe",  # 观察后
    ]

    def __init__(self) -> None:
        # 实例属性 - 每个实例独立（类似 Java 实例字段）
        # 注：此处省略了 hook 字典、订阅者等属性，完整版见 04_type_hints.md
        self.id = shortuuid.uuid()
        self._reply_task: Task | None = None
```

**访问对比**：

| Python | Java |
|--------|------|
| `cls.attr` | `ClassName.staticAttr` |
| `self.attr` | `this.instanceAttr` |

## 方法类型

```python
class MyClass:
    def __init__(self, value: int = 0) -> None:
        self.value = value

    # 实例方法 - 第一个参数是 self
    def instance_method(self, x: int) -> int:
        return x * 2

    # 类方法 - 第一个参数是 cls（类似 Java static 但能访问类）
    @classmethod
    def from_string(cls, s: str) -> "MyClass":
        return cls(int(s))

    # 静态方法 - 没有任何隐式参数（纯函数）
    @staticmethod
    def helper(x: int) -> int:
        return x + 1
```

**Java 对照**：

```java
public class MyClass {
    // 实例方法
    public int instanceMethod(int x) { return x * 2; }

    // 静态方法（Python @staticmethod 对应）
    public static int helper(int x) { return x + 1; }

    // 工厂方法（Python @classmethod 对应）
    public static MyClass fromString(String s) {
        return new MyClass(Integer.parseInt(s));
    }
}
```

## @property（Pythonic 的 Getter/Setter）

```python
class AgentBase:
    def __init__(self) -> None:
        self._disable_console_output = False

    # Pythonic 的 getter - Java 用 getXxx()
    @property
    def disable_console_output(self) -> bool:
        return self._disable_console_output

    # Pythonic 的 setter - Java 用 setXxx()
    @disable_console_output.setter
    def disable_console_output(self, value: bool) -> None:
        self._disable_console_output = value

# 使用 - 像访问属性一样
agent = AgentBase()
if agent.disable_console_output:  # 自动调用 @property
    agent.disable_console_output = True  # 自动调用 setter
```

**Java 对照**：

```java
public class AgentBase {
    private boolean disableConsoleOutput = false;

    public boolean getDisableConsoleOutput() {
        return disableConsoleOutput;
    }

    public void setDisableConsoleOutput(boolean value) {
        this.disableConsoleOutput = value;
    }
}

// 使用
AgentBase agent = new AgentBase();
if (agent.getDisableConsoleOutput()) {
    agent.setDisableConsoleOutput(true);
}
```

## 特殊方法（dunder methods）

Python 的 `__init__`、`__repr__` 等称为 "dunder"（double underscore）方法：

```python
class Msg:
    def __repr__(self) -> str:
        """类似 Java 的 toString()"""
        return (
            f"Msg(id='{self.id}', "
            f"name='{self.name}', "
            f"content={repr(self.content)}, "
            f"role='{self.role}')"
        )

# 打印对象时自动调用 __repr__
msg = Msg("Alice", "Hello", "user")
print(msg)
# Msg(id='xxx', name='Alice', content='Hello', role='user')
```

### __repr__ vs __str__

```python
# __repr__: 开发者调试用（默认 fallback）
# __str__:  用户显示用（print()、str() 优先调用）
# Java 没有区分，都对应 toString()

class Msg:
    def __repr__(self) -> str:
        return f"Msg(id='{self.id}', name='{self.name}')"

    def __str__(self) -> str:
        return f"[{self.name}]: {self.content}"

msg = Msg("Alice", "Hello", "user")
print(repr(msg))  # Msg(id='xxx', name='Alice') — 调试信息
print(str(msg))   # [Alice]: Hello — 用户显示
# 如果只定义 __repr__，print() 也会使用它
```

**常用特殊方法对照**：

| Python | Java | 用途 |
|--------|------|------|
| `__init__` | constructor | 初始化 |
| `__repr__` | toString() | 字符串表示 |
| `__str__` | toString() | 用户友好字符串 |
| `__eq__` | equals() | 相等比较 |
| `__hash__` | hashCode() | 哈希值 |
| `__len__` | `size()` / `length()` | 长度（`len(obj)`） |
| `__getitem__` | `list.get()` / `map.get()` | 索引/键访问 |
| `__call__` | `Callable.call()` | 可调用对象 |

### __call__ 特殊方法

> **提示**：以下示例使用了 `async`/`await` 语法，将在 [02 - 异步编程](02_async_await.md) 中详细讲解。此处只需关注 `__call__` 使对象可像函数一样调用的概念。

```python
class AgentBase:
    """AgentBase 的 __call__ 方法使其可像函数一样调用"""

    async def __call__(self, *args: Any, **kwargs: Any) -> Msg:
        """可调用对象 - 触发 reply 并处理异常"""
        self._reply_id = shortuuid.uuid()

        reply_msg: Msg | None = None
        try:
            self._reply_task = asyncio.current_task()
            reply_msg = await self.reply(*args, **kwargs)
        except asyncio.CancelledError:
            reply_msg = await self.handle_interrupt(*args, **kwargs)
        finally:
            if reply_msg:
                await self._broadcast_to_subscribers(reply_msg)
            self._reply_task = None

        return reply_msg

# 使用 - 类似 Java 的 Callable.call()
# await agent() 相当于 await agent.__call__()
# 注意：因为 __call__ 是 async，调用时需要 await
# async/await 机制详见 02_async_await.md
```

**Java 对照**：

```java
// Java 没有直接对应的语法，但类似 Functional Interface
@FunctionalInterface
public interface Supplier<T> {
    T get();
}

// 或使用 Callable
public class AgentBase implements Callable<Msg> {
    @Override
    public Msg call() {
        return reply();
    }
}
```

## 数据类（dataclass）

```python
from dataclasses import dataclass, field
from datetime import datetime

# Python dataclass - 自动生成 __init__, __repr__, __eq__
@dataclass
class User:
    name: str
    email: str
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    tags: list[str] = field(default_factory=list)

# 自动生成：
# - __init__(name, email, created_at, tags)
# - __repr__()
# - __eq__()

user = User("Alice", "alice@example.com")
print(user)
# User(name='Alice', email='alice@example.com', created_at='...', tags=[])
```

**Java 对照**：

```java
// Java 16+ record（最接近 Python dataclass）
public record User(
    String name,
    String email,
    String createdAt,
    List<String> tags
) {
    // 自动生成：构造器、getter、toString、equals、hashCode
}

// 或使用 Lombok
@Data
@NoArgsConstructor
@AllArgsConstructor
public class User {
    private String name;
    private String email;
    private String createdAt;
    private List<String> tags;
}
```

## 练习题

1. **创建类**：参考 `Msg` 类，创建一个 `Agent` 类，包含 `name` 和 `model` 属性

2. **添加方法**：为 `Agent` 类添加 `__repr__` 方法

3. **继承**：创建一个 `ReActAgent` 类继承 `Agent`，添加 `tools` 属性

4. **@property**：将以下 Java 转为 Python：
   ```java
   public String getName() { return name; }
   public void setName(String name) { this.name = name; }
   ```

5. **dataclass**：用 `@dataclass` 重写以下类：
   ```python
   class Point:
       def __init__(self, x: float, y: float) -> None:
           self.x = x
           self.y = y
   ```

6. **类型注解**：为以下 Python 代码添加类型注解：
   ```python
   def process_message(msg, config):
       return msg
   ```

---

**答案**：

```python
# 1.
class Agent:
    def __init__(self, name: str, model: str) -> None:
        self.name = name
        self.model = model

# 2.
class Agent:
    def __repr__(self) -> str:
        return f"Agent(name='{self.name}', model='{self.model}')"

# 3.
class ReActAgent(Agent):
    def __init__(self, name: str, model: str, tools: list) -> None:
        super().__init__(name, model)
        self.tools = tools

# 4.
class MyClass:
    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value

# 5.
from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float

# 6.
def process_message(msg: Msg, config: dict) -> Msg:
    return msg
```

## 附录：本文关键字简写对照表

| 简写 | 全称 | 说明 |
|------|------|------|
| `def` | **def**ine | 定义函数/方法 |
| `self` | 实例自身 | 类似 Java 的 `this` |
| `cls` | **cls**s | 类方法中的类引用，类似 `ClassName` |
| `__init__` | **init**ialize | 初始化方法（非真正构造器） |
| `__new__` | **new** | 真正的构造器（创建实例） |
| `__repr__` | **rep**resentation | 开发者友好的字符串表示 |
| `__str__` | **str**ing | 用户友好的字符串表示 |
| `__eq__` | **eq**ual | 相等比较 (`==`) |
| `__hash__` | **hash** | 哈希值（用于 dict/set） |
| `__len__` | **len**gth | 长度 (`len(obj)`) |
| `__getitem__` | **get item** | 索引访问 (`obj[key]`) |
| `__call__` | **call** | 可调用 (`obj()`) |
| `__name__` | **name** | 函数/类的名称 |
| `__doc__` | **doc**ument | 文档字符串 |
| `str` | **str**ing | 字符串类型 |
| `int` | **int**eger | 整数类型 |
| `bool` | **bool**ean | 布尔类型 |
| `dict` | **dict**ionary | 字典类型 |
| `super()` | 父类引用 | 调用父类方法 |
| `@property` | **prop**erty | 属性装饰器 |
| `@dataclass` | **data class** | 数据类装饰器 |
