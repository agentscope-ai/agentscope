# 05 - 数据类 @dataclass

## Java POJO 对照

```java
// Java 传统 POJO
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

    // equals/hashCode/toString
    @Override
    public boolean equals(Object o) { ... }
    @Override
    public int hashCode() { ... }
    @Override
    public String toString() { return "Person{name=" + name + ..."; }
}
```

Python `@dataclass` 一行搞定！

## Python @dataclass

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
```

`★ Insight ─────────────────────────────────────`
- `@dataclass` 自动生成 `__init__`, `__repr__`, `__eq__`, `__hash__`
- 类似 Java 的 Lombok `@Data` 注解
- 字段可以是必选或可选（带默认值）
`─────────────────────────────────────────────────`

## AgentScope 源码示例

**文件**: `src/agentscope/rag/_document.py`

```python
from dataclasses import dataclass, field
from typing import Sequence

@dataclass
class Document:
    """文档类 - 用于 RAG 系统的文档表示"""
    content: str                                    # 必选字段
    metadata: dict[str, Any] = field(default_factory=dict)  # 可选，默认空字典
    embedding: list[float] | None = None           # 可选，嵌入向量
    id: str = ""                                   # 可选，默认空字符串

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "metadata": self.metadata,
            "id": self.id,
        }
```

**Java 对照**：

```java
// 粗略的 Java 等价
public class Document {
    private String content;
    private Map<String, Object> metadata;
    private List<Double> embedding;
    private String id;

    public Document(String content) {
        this(content, new HashMap<>(), null, "");
    }

    public Document(String content, Map<String, Object> metadata,
                    List<Double> embedding, String id) {
        this.content = content;
        this.metadata = metadata;
        this.embedding = embedding;
        this.id = id;
    }

    public String toString() {
        return String.format("Document(content='%s', ...)", content);
    }

    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        Document that = (Document) o;
        return Objects.equals(content, that.content);
    }
}
```

## @dataclass 进阶

### field(default_factory=...)

```python
from dataclasses import dataclass, field

@dataclass
class Config:
    name: str
    # 默认值：list/dict 必须用 default_factory
    items: list[str] = field(default_factory=list)
    settings: dict[str, int] = field(default_factory=dict)

# 使用
c = Config("test")
print(c.items)       # []
c.items.append("hello")  # 不会影响其他实例！

# ❌ 错误做法
@dataclass
class BadConfig:
    items: list[str] = []  # 所有实例共享同一个 list！

# ✅ 正确做法
@dataclass
class GoodConfig:
    items: list[str] = field(default_factory=list)
```

### __post_init__（构造后处理）

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

p = Person("Alice", 25)
print(p.email)  # "alice@example.com"
```

### frozen=True（不可变对象）

```python
from dataclasses import dataclass

@dataclass(frozen=True)  # 类似 Java 的 final
class ImmutablePerson:
    name: str
    age: int

p = ImmutablePerson("Alice", 25)
p.age = 30  # FrozenInstanceError!
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
        """计算属性 - Java 可以用方法实现"""
        return f"{self.first_name} {self.last_name}"

p = Person("John", "Doe")
print(p.full_name)  # "John Doe"
```

## dataclass vs 字典

```python
# 使用字典
user_dict = {"name": "Alice", "age": 25}
print(user_dict["name"])  # "Alice"
print(user_dict)  # {'name': 'Alice', 'age': 25}

# 使用 dataclass
@dataclass
class User:
    name: str
    age: int

user = User("Alice", 25)
print(user.name)  # "Alice" - IDE 自动补全！
print(user)  # User(name='Alice', age=25)
```

| 特性 | dict | @dataclass |
|------|------|------------|
| 类型提示 | 无 | 有 |
| IDE 补全 | 无 | 有 |
| 代码量 | 少 | 中 |
| 性能 | 快 | 稍慢 |

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

### 2. 类方法

```python
from dataclasses import dataclass

@dataclass
class Person:
    name: str
    age: int

    @classmethod
    def from_dict(cls, data: dict) -> "Person":
        """工厂方法"""
        return cls(data["name"], data["age"])

p = Person.from_dict({"name": "Alice", "age": 25})
```

## 练习题

1. **创建数据类**：用 `@dataclass` 创建一个 `Message` 类，包含 `sender`, `content`, `timestamp`

2. **添加默认值**：为 `Message` 的 `timestamp` 添加默认值（当前时间）

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

---

**答案**：

```python
# 1.
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Message:
    sender: str
    content: str
    timestamp: str

# 2.
from dataclasses import dataclass, field

@dataclass
class Message:
    sender: str
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

# 3.
# 实际输出是 ['a']，因为所有实例共享同一个 list 对象！
# 正确做法：
@dataclass
class Config:
    items: list = field(default_factory=list)
```
