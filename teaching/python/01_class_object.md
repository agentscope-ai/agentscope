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
        self.name = name      # self 类似 this
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

**文件**: `src/agentscope/message/_message_base.py`

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

### 类型注解

```python
# Python 类型注解（类似 Java 泛型但更灵活）
class Msg:
    def __init__(
        self,
        name: str,                              # → String name
        content: str | Sequence[ContentBlock],   # → String 或 List<ContentBlock>
        role: Literal["user", "assistant"],     # → 枚举限制
        metadata: dict[str, JSONSerializableObject] | None = None,  # → Map<String, Object>
        timestamp: str | None = None,
    ) -> None:  # → void（Python 用 None 表示无返回值）
        ...
```

| Python | Java |
|--------|------|
| `str` | `String` |
| `int` | `int` / `Integer` |
| `list[T]` | `List<T>` |
| `dict[K, V]` | `Map<K, V>` |
| `T \| None` | `@Nullable T` |
| `-> None` | `void` |

## 实例属性 vs 类属性

```python
class AgentBase:
    # 类属性 - 所有实例共享（类似 Java static）
    supported_hook_types: list[str] = [
        "pre_reply",
        "post_reply",
        "pre_print",
    ]

    def __init__(self) -> None:
        # 实例属性 - 每个实例独立（类似 Java 实例字段）
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
    # 实例方法 - 第一个参数是 self
    def instance_method(self, x: int) -> int:
        return x * 2

    # 类方法 - 第一个参数是 cls（类似 Java static 但能访问类）
    @classmethod
    def from_string(cls, s: str) -> "MyClass":
        return cls(s)

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
        return new MyClass(s);
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

| Python | Java | 用途 |
|--------|------|------|
| `__init__` | constructor | 初始化 |
| `__repr__` | toString() | 字符串表示 |
| `__str__` | toString() | 用户友好字符串 |
| `__eq__` | equals() | 相等比较 |
| `__hash__` | hashCode() | 哈希值 |

## 练习题

1. **创建类**：参考 `Msg` 类，创建一个 `Agent` 类，包含 `name` 和 `model` 属性

2. **添加方法**：为 `Agent` 类添加 `__repr__` 方法

3. **类型注解**：为以下 Python 代码添加类型注解：
   ```python
   def process_message(msg, config):
       return msg
   ```

4. **@property**：将以下 Java 转为 Python：
   ```java
   public String getName() { return name; }
   public void setName(String name) { this.name = name; }
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
def process_message(msg: Msg, config: dict) -> Msg:
    return msg

# 4.
class MyClass:
    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value
```
