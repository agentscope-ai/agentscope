# 05 - 数据类 @dataclass

## Java POJO 对照

```java
// Java 传统 POJO - 需要大量模板代码
public class Person {
    private String name;
    private int age;
    private String email;

    public Person(String name, int age, String email) {
        this.name = name;
        this.age = age;
        this.email = email;
    }

    // Getters
    public String getName() { return name; }
    public int getAge() { return age; }
    public String getEmail() { return email; }

    // Setters
    public void setName(String name) { this.name = name; }
    public void setAge(int age) { this.age = age; }
    public void setEmail(String email) { this.email = email; }

    // equals/hashCode/toString - 还要手动实现
    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        Person person = (Person) o;
        return age == person.age
            && Objects.equals(name, person.name)
            && Objects.equals(email, person.email);
    }

    @Override
    public int hashCode() {
        return Objects.hash(name, age, email);
    }

    @Override
    public String toString() {
        return "Person{name=" + name + ", age=" + age + ", email=" + email + "}";
    }
}
```

Python `@dataclass` 一行搞定，自动生成所有方法！

## Python @dataclass 基础

```python
from dataclasses import dataclass

@dataclass
class Person:
    name: str
    age: int
    email: str = ""  # 带默认值

# 自动生成：__init__, __repr__, __eq__
p = Person("Alice", 25)
print(p)
# Person(name='Alice', age=25, email='')

p2 = Person("Bob", 30)
print(p == p2)  # False - 自动生成 __eq__

# 注意：默认 @dataclass 不生成 __hash__（因为可变对象不应可哈希）
# 要启用哈希，需要 frozen=True:
# @dataclass(frozen=True)
# class ImmutablePerson: ...
```

`★ Insight ─────────────────────────────────────`
- `@dataclass` 自动生成 `__init__`, `__repr__`, `__eq__`
- 默认**不生成** `__hash__`（因为可变对象哈希不安全），需要 `frozen=True` 才会生成
- 类似 Java 的 Lombok `@Data` 注解
- 字段可以是必选或可选（带默认值）
- Python 3.10+ 支持更简洁的字段语法
`─────────────────────────────────────────────────`

## AgentScope 源码示例

**文件**: `src/agentscope/rag/_document.py:35`

```python
@dataclass
class Document:
    """文档类 - 用于 RAG 系统的文档表示"""

    metadata: DocMetadata                    # 必选字段（无默认值）
    id: str = field(default_factory=shortuuid.uuid)  # 可选，默认生成UUID
    embedding: Embedding | None = field(default_factory=lambda: None)  # 可选
    score: float | None = None               # 可选，默认 None
```

**注意**：实际源码中 `Document` 没有 `content` 字段，内容存储在 `metadata.content` 中。

**文件**: `src/agentscope/module/_state_module.py:13`

```python
@dataclass
class _JSONSerializeFunction:
    """用于状态模块的序列化函数封装"""
    to_json: Optional[Callable[[Any], Any]] = None
    load_json: Optional[Callable[[Any], Any]] = None
```

**Java 对照**：

```java
// Java 等价实现 - 需要更多代码
public class JSONSerializeFunction {
    private Function<Object, Object> toJson;
    private Function<Object, Object> loadJson;

    public JSONSerializeFunction() {}

    public JSONSerializeFunction(Function<Object, Object> toJson,
                                 Function<Object, Object> loadJson) {
        this.toJson = toJson;
        this.loadJson = loadJson;
    }

    public Function<Object, Object> getToJson() { return toJson; }
    public void setToJson(Function<Object, Object> toJson) { this.toJson = toJson; }
    public Function<Object, Object> getLoadJson() { return loadJson; }
    public void setLoadJson(Function<Object, Object> loadJson) { this.loadJson = loadJson; }
}
```

## @dataclass 进阶用法

### field(default_factory=...) - 可变默认值

```python
from dataclasses import dataclass, field

@dataclass
class Config:
    name: str
    # list/dict 必须用 default_factory，否则所有实例共享同一对象！
    items: list[str] = field(default_factory=list)
    settings: dict[str, int] = field(default_factory=dict)

# 使用
c1 = Config("test")
c2 = Config("test")
c1.items.append("hello")

print(c1.items)  # ['hello']
print(c2.items)  # [] - 正确！每个实例有独立的 list

# ❌ 错误写法 - 共享可变默认值
@dataclass
class BadConfig:
    items: list[str] = []  # 所有实例共享同一个 list！

# ✅ 正确写法
@dataclass
class GoodConfig:
    items: list[str] = field(default_factory=list)
```

### __post_init__ - 构造后处理

```python
from dataclasses import dataclass

@dataclass
class Person:
    name: str
    age: int
    email: str = ""

    def __post_init__(self) -> None:
        """构造后自动调用 - 类似 Java 的 @PostConstruct"""
        if self.email == "":
            self.email = f"{self.name.lower()}@example.com"

        # 数据验证
        if self.age < 0:
            raise ValueError("Age cannot be negative")

p = Person("Alice", 25)
print(p.email)  # "alice@example.com" - 自动生成
```

### frozen=True - 不可变对象

```python
from dataclasses import dataclass

@dataclass(frozen=True)  # 类似 Java 的 final 字段
class ImmutablePerson:
    name: str
    age: int

p = ImmutablePerson("Alice", 25)
p.age = 30  # FrozenInstanceError! 无法修改

# Java 对照：
// public final class ImmutablePerson {
//     private final String name;
//     private final int age;
//     // 无 setter
// }
```

### slots=True - 内存优化

```python
from dataclasses import dataclass

@dataclass(slots=True)  # Python 3.10+ 使用 __slots__ 节省内存
class Person:
    name: str
    age: int

# 内存占用减少约 40%，但不能动态添加属性
```

## 与 @property 结合

```python
from dataclasses import dataclass

@dataclass
class Person:
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        """计算属性 - Java 可用方法实现"""
        return f"{self.first_name} {self.last_name}"

    @property
    def initials(self) -> str:
        return f"{self.first_name[0]}.{self.last_name[0]}."

p = Person("John", "Doe")
print(p.full_name)   # "John Doe"
print(p.initials)    # "J.D."
```

## dataclass vs dict vs TypedDict

```python
# 字典 - 无类型提示，IDE 不友好
user_dict = {"name": "Alice", "age": 25}
print(user_dict["name"])

# @dataclass - 有类型提示，IDE 自动补全
@dataclass
class User:
    name: str
    age: int

user = User("Alice", 25)
print(user.name)  # IDE 自动补全！

# typing.TypedDict - 有类型提示但不生成 __init__
from typing import TypedDict

class UserDict(TypedDict):
    name: str
    age: int

# TypedDict 实例化：直接构造字典或使用关键字参数（Python 3.9+）
user_td: UserDict = {"name": "Alice", "age": 25}  # 直接赋值
user_td2 = UserDict(name="Alice", age=25)  # 关键字构造（Python 3.9+）
# 注意：TypedDict 不做运行时验证，只是静态类型提示
```

| 特性 | dict | TypedDict | @dataclass |
|------|------|-----------|------------|
| 类型提示 | 无 | 有 | 有 |
| IDE 补全 | 无 | 部分 | 完整 |
| `__init__` | 无 | 无 | 自动生成 |
| `__eq__` | 无 | 无 | 自动生成 |
| 性能 | 快 | 快 | 稍慢 |
| 可继承 | 否 | 是 | 是 |

## 常见问题

### 1. 继承

```python
from dataclasses import dataclass

@dataclass
class Base:
    x: int

@dataclass
class Child(Base):  # 基类也必须是 dataclass
    y: int

c = Child(1, 2)
print(c)  # Child(x=1, y=2)
```

### 2. 类方法/工厂方法

```python
from dataclasses import dataclass

@dataclass
class Person:
    name: str
    age: int

    @classmethod
    def from_dict(cls, data: dict) -> "Person":
        """工厂方法 - 类似 Builder 模式"""
        return cls(data["name"], data["age"])

    @classmethod
    def teenage(cls, name: str) -> "Person":
        """特定场景工厂"""
        return cls(name, 15)

p = Person.from_dict({"name": "Alice", "age": 25})
p2 = Person.teenage("Bob")
```

### 3. 字段排序（field 的 compare 参数）

```python
from dataclasses import dataclass, field

@dataclass
class Config:
    name: str
    sensitive_data: str = field(default="", compare=False)  # 比较时忽略
    items: list = field(default_factory=list, compare=False)  # 列表不可比较

c1 = Config("test", "secret", [1, 2])
c2 = Config("test", "different", [3, 4])
print(c1 == c2)  # True - sensitive_data 和 items 被忽略
```

## AgentScope 更多 dataclass 示例

**文件**: `src/agentscope/evaluate/_task.py:12`

```python
@dataclass
class Task:
    """评估任务定义"""
    id: str
    input: JSONSerializableObject
    ground_truth: JSONSerializableObject
    metrics: list[MetricBase]
    tags: dict[str, str] | None = field(default_factory=lambda: None)
    metadata: dict[str, Any] | None = field(default_factory=lambda: None)

    async def evaluate(self, solution: SolutionOutput) -> list[MetricResult]:
        """评估给定的解决方案"""
        ...
```

**文件**: `src/agentscope/model/_model_response.py:20`

```python
@dataclass
class ChatResponse(DictMixin):
    """模型聊天响应封装"""
    content: Sequence[TextBlock | ToolUseBlock | ThinkingBlock | AudioBlock]
    id: str = field(default_factory=lambda: _get_timestamp(True))
    created_at: str = field(default_factory=_get_timestamp)
    type: Literal["chat"] = field(default_factory=lambda: "chat")
    usage: ChatUsage | None = field(default_factory=lambda: None)
    metadata: dict[str, JSONSerializableObject] | None = field(
        default_factory=lambda: None,
    )
```

## 练习题

1. **创建数据类**：用 `@dataclass` 创建一个 `Message` 类，包含 `sender`, `content`, `timestamp`，其中 `timestamp` 有默认值

2. **实现不可变配置**：创建一个 `Config` dataclass，包含 `host` 和 `port`，要求不可变

3. **修复错误**：
   ```python
   @dataclass
   class Config:
       items: list = []

   c1 = Config()
   c2 = Config()
   c1.items.append("a")
   print(c2.items)  # 期望 []，实际是？
   ```

4. **添加 post_init**：为 `Message` 类添加 `__post_init__`，验证 `sender` 不能为空

5. **比较两个 dataclass**：
   ```python
   @dataclass
   class Point:
       x: int
       y: int
       label: str = ""

   p1 = Point(1, 2)
   p2 = Point(1, 2)
   print(p1 == p2)  # 输出什么？
   print(hash(p1) == hash(p2))  # 输出什么？
   ```

---

**答案**：

```python
# 1.
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class Message:
    sender: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

# 2.
@dataclass(frozen=True)
class Config:
    host: str
    port: int

# 3.
# @dataclass 会直接报错！
# ValueError: mutable default <class 'list'> for field items is not allowed
# 这是 @dataclass 的保护机制

# 如果不用 @dataclass，确实会共享：
class BadConfig:
    items: list = []

c1 = BadConfig()
c2 = BadConfig()
c1.items.append("a")
print(c2.items)  # ['a'] — 所有实例共享同一个 list！

# @dataclass 的正确写法：
@dataclass
class Config:
    items: list = field(default_factory=list)

# 4.
@dataclass
class Message:
    sender: str
    content: str
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.sender:
            raise ValueError("sender cannot be empty")

# 5.
# True - __eq__ 自动生成，比较所有字段
# TypeError! - 默认 @dataclass 不生成 __hash__，对象不可哈希
# 如需哈希：使用 @dataclass(frozen=True) 或 @dataclass(unsafe_hash=True)
```

## 附录：本文关键字简写对照表

| 简写 | 全称 | 说明 |
|------|------|------|
| `@dataclass` | **data class** | 数据类（自动生成方法） |
| `field` | **field** | 数据类字段配置 |
| `default_factory` | **default factory** | 默认值工厂函数 |
| `__init__` | **init**ialize | 构造器 |
| `__repr__` | **rep**resentation | 字符串表示 |
| `__eq__` | **eq**ual | 相等比较 |
| `__hash__` | **hash** | 哈希值 |
| `__post_init__` | **post init**ialize | 构造后回调 |
| `__str__` | **str**ing | 用户字符串 |
| `__slots__` | **slots** | 内存优化属性槽 |
| `frozen` | **frozen** | 冻结（不可变） |
| `TypedDict` | **typed dict**ionary | 有类型提示的字典 |
| `POJO` | **P**lain **O**ld **J**ava **O**bject | 普通 Java 对象 |
| `record` | **record** | Java 16+ 不可变数据载体 |
| `@Data` | Lombok **data** | Lombok 数据类注解 |
