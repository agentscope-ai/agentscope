# 07 - 继承与多态

## Java 继承回顾

```java
// Java 类继承
public class Animal {
    protected String name;

    public void speak() {
        System.out.println("...");
    }
}

public class Dog extends Animal {
    @Override
    public void speak() {
        System.out.println("Woof!");
    }
}

public class Cat extends Animal {
    @Override
    public void speak() {
        System.out.println("Meow!");
    }
}

// 多态
Animal a = new Dog();  // 父类引用指向子类对象
a.speak();  // "Woof!"
```

`★ Insight ─────────────────────────────────────`
- Python 继承语法：`class Child(Parent):` - 没有 `extends` 关键字
- 方法重写不需要 `@Override` 注解（Python 不强制，但可以有）
- Python 支持多继承，Java 不支持（只支持单继承 + 多接口）
`─────────────────────────────────────────────────`

## Python 继承基础

```python
# Python 类继承 - 语法比 Java 更简洁
class Animal:
    def __init__(self, name: str):
        self.name = name

    def speak(self) -> str:
        return "..."

class Dog(Animal):  # 继承
    def speak(self) -> str:  # 重写
        return "Woof!"

class Cat(Animal):
    def speak(self) -> str:
        return "Meow!"

# 多态
animals: list[Animal] = [Dog("Rex"), Cat("Whiskers")]
for animal in animals:
    print(animal.speak())  # "Woof!", "Meow!"

# 类似 Java
animal: Animal = Dog("Rex")
animal.speak()  # "Woof!" - 运行时动态分派
```

## super() 调用父类方法

```python
class Animal:
    def __init__(self, name: str):
        self.name = name

    def speak(self) -> str:
        return "..."


class Dog(Animal):
    def __init__(self, name: str, breed: str):
        super().__init__(name)  # 调用父类 __init__
        self.breed = breed

    def speak(self) -> str:
        parent_result = super().speak()  # 调用父类方法
        return f"{parent_result} Woof!"


class Labrador(Dog):
    def __init__(self, name: str):
        super().__init__(name, "Labrador")  # 调用父类


d = Dog("Rex", "Labrador")
print(d.name)   # "Rex"
print(d.breed)  # "Labrador"
print(d.speak())  # "... Woof!"
```

### super() 的多种写法

```python
class Child(Parent):
    def __init__(self, name, age):
        # 方式1：显式父类名（Python 2 风格）
        Parent.__init__(self, name)

        # 方式2：super() - 推荐
        super().__init__(name)
        self.age = age

        # 方式3：super(Child, self) - 显式指定类
        super(Child, self).__init__(name)
```

## AgentScope 源码示例

**文件**: `src/agentscope/agent/_agent_base.py:30`

```python
class StateModule:
    """状态模块基类 - 类似 Java 的 Serializable"""
    def __init__(self) -> None:
        self._state: dict[str, Any] = {}

    def state_dict(self) -> dict[str, Any]:
        """序列化状态"""
        return self._state.copy()

    def load_state_dict(self, state: dict[str, Any]) -> None:
        """反序列化状态"""
        self._state.update(state)


class AgentBase(StateModule, metaclass=_AgentMeta):
    """Agent 基类 - 继承自 StateModule"""
    # 注意：这里 metaclass=_AgentMeta 是元类参数，不是基类！
    # 这不是多继承，而是：单继承 + 元类

    supported_hook_types: list[str] = [
        "pre_reply",     # 回复前
        "post_reply",    # 回复后
        "pre_print",     # 打印前
        "post_print",    # 打印后
        "pre_observe",   # 观察前
        "post_observe",  # 观察后
    ]

    def __init__(self) -> None:
        super().__init__()  # 调用父类构造器
        self.id = shortuuid.uuid()
```

**文件**: `src/agentscope/agent/_react_agent_base.py:12`

```python
class ReActAgentBase(AgentBase, metaclass=_ReActAgentMeta):
    """ReAct Agent - 继承自 AgentBase"""
    # 两层继承 + 元类
    pass
```

**Java 对照理解**：

```java
// 大致的 Java 对应
public class StateModule {
    protected Map<String, Object> _state = new HashMap<>();

    public Map<String, Object> stateDict() {
        return new HashMap<>(_state);
    }

    public void loadStateDict(Map<String, Object> state) {
        _state.putAll(state);
    }
}

// Java 只支持单继承，AgentBase 只能 extends 一个类
public class AgentBase extends StateModule {
    public static List<String> supportedHookTypes = Arrays.asList(
        "pre_reply", "post_reply"
    );

    public AgentBase() {
        super();
        this.id = UUID.randomUUID().toString();
    }
}
```

**重要澄清**：`class AgentBase(StateModule, metaclass=_AgentMeta)` 不是多继承！
- `StateModule` 是父类（单继承）
- `metaclass=_AgentMeta` 是元类参数，用于控制类的创建行为
- Python 语法：`class Child(Parent1, Parent2)` 才是多继承

## 多继承

Python 支持多继承（Java 不支持）：

```python
class Flyable:
    def fly(self) -> str:
        return "Flying"


class Swimmable:
    def swim(self) -> str:
        return "Swimming"


class Duck(Animal, Flyable, Swimmable):
    """鸭子 - 多继承"""
    pass


duck = Duck("Donald")
print(duck.speak())  # "..."
print(duck.fly())     # "Flying"
print(duck.swim())    # "Swimming"
```

### MRO（方法解析顺序）

```python
class A:
    def who(self): return "A"

class B(A):
    pass

class C(A):
    def who(self): return "C"

class D(B, C):  # D 的 MRO: D -> B -> C -> A
    pass

print(D().who())  # "C" - C 比 A 先找到

# 查看完整 MRO
print(D.__mro__)
# (<class 'D'>, <class 'B'>, <class 'C'>, <class 'A'>, <class 'object'>)
```

### 多继承的 super 链

```python
class A:
    def __init__(self):
        print("A init")
        super().__init__()

class B(A):
    def __init__(self):
        print("B init")
        super().__init__()

class C(A):
    def __init__(self):
        print("C init")
        super().__init__()

class D(B, C):
    def __init__(self):
        print("D init")
        super().__init__()

D()
# 输出:
# D init
# B init
# C init
# A init
# super() 按 MRO 顺序调用
```

## 抽象基类

```python
from abc import ABC, abstractmethod

# 类似 Java 的抽象类
class AbstractAgent(ABC):
    @abstractmethod
    async def reply(self, *args, **kwargs) -> "Msg":
        """必须实现"""
        pass

    @abstractmethod
    async def observe(self, msg: "Msg") -> None:
        """必须实现"""
        pass

    def common_method(self) -> str:
        """可以提供默认实现"""
        return "Common"


# ❌ 错误 - 不能实例化抽象类
# agent = AbstractAgent()  # TypeError

# ✅ 正确 - 必须实现所有抽象方法
class ConcreteAgent(AbstractAgent):
    async def reply(self, *args, **kwargs) -> "Msg":
        return Msg("assistant", "Hello", "assistant")

    async def observe(self, msg: "Msg") -> None:
        print(f"Observed: {msg}")
```

### Java 对照

```java
// Java 抽象类
public abstract class AbstractAgent {
    public abstract void reply();

    public String commonMethod() {
        return "Common";
    }
}

// Java 也有接口
public interface Agent {
    void reply();
}

// Java 17+ 支持 sealed class
public abstract sealed class Shape permits Circle, Square {}
```

| Python | Java | 说明 |
|--------|------|------|
| `class A(ABC)` | `abstract class A` | 抽象类 |
| `@abstractmethod` | `abstract void foo()` | 抽象方法 |
| `class B(A)` | `class B extends A` | 继承 |
| `class C(A, B)` | 不支持 | 多继承 |
| `class D(Protocol)` | `interface D` | 结构化类型 |

## isinstance 和 issubclass

```python
class Animal:
    pass

class Dog(Animal):
    pass

class Cat(Animal):
    pass

# isinstance - 类似 Java instanceof
dog = Dog()
print(isinstance(dog, Dog))      # True
print(isinstance(dog, Animal))  # True
print(isinstance(dog, Cat))     # False
print(isinstance(dog, (Dog, Cat)))  # True - 元组形式

# issubclass - 检查类继承关系
print(issubclass(Dog, Animal))  # True
print(issubclass(Animal, Dog))  # False
print(issubclass(Dog, (Dog, Cat)))  # True
```

## Protocol（结构化子类型 - 静态 duck typing）

```python
from typing import Protocol

class Flyable(Protocol):
    def fly(self) -> str: ...

class Swimmable(Protocol):
    def swim(self) -> str: ...

class Duck:
    def fly(self) -> str:
        return "Flying"
    def swim(self) -> str:
        return "Swimming"

# 方式1：创建组合 Protocol
class FlyableAndSwimmable(Flyable, Swimmable, Protocol):
    """组合多个 Protocol - 类似 Java 接口多继承"""
    pass

def process(obj: FlyableAndSwimmable) -> None:
    print(obj.fly(), obj.swim())

process(Duck())  # OK - Duck 同时实现了 fly() 和 swim()

# 方式2：分别接受（更灵活）
def process_both(flyer: Flyable, swimmer: Swimmable) -> None:
    print(flyer.fly(), swimmer.swim())

# Duck 同时满足 Flyable 和 Swimmable
duck = Duck()
process_both(duck, duck)  # OK
```

### Protocol vs 继承

```python
from typing import Protocol

# Protocol - 声明式接口（推荐用于插件架构）
class Serializer(Protocol):
    def serialize(self) -> bytes: ...

# 继承 - 强耦合
class BaseSerializer:
    def serialize(self) -> bytes: ...

# Protocol 只需要方法签名匹配，不要求继承关系
class CustomSerializer:
    def serialize(self) -> bytes:
        return b"data"

def save(serializer: Serializer) -> None:
    data = serializer.serialize()

save(CustomSerializer())  # OK - 结构化类型检查
```

## Mixin 模式

```python
# Mixin - 提供可选功能的类（不实例化）
class LogMixin:
    def log(self, msg: str) -> None:
        print(f"[{self.__class__.__name__}] {msg}")

class SerializableMixin:
    def to_dict(self) -> dict:
        return vars(self)

# 使用 Mixin
class User(LogMixin, SerializableMixin):
    def __init__(self, name: str):
        self.name = name

user = User("Alice")
user.log("User created")  # [User] User created
print(user.to_dict())     # {'name': 'Alice'}
```

## 练习题

1. **创建继承关系**：创建 `Vehicle` 基类，有 `speed` 属性和 `move()` 方法，然后创建 `Car` 和 `Bike` 子类

2. **super() 使用**：为 `Car` 添加 `__init__`，调用父类并添加 `wheels` 属性

3. **多态实现**：创建函数 `make_speak(animals: list[Animal])` 让所有动物说话

4. **MRO 理解**：
   ```python
   class A:
       def who(self): return "A"

   class B(A):
       pass

   class C(A):
       def who(self): return "C"

   class D(B, C):
       pass

   print(D().who())
   ```

5. **Protocol 使用**：创建一个 `Readable` Protocol，然后实现一个 `FileReader` 类

6. **判断输出**：
   ```python
   class A:
       def __init__(self):
           print("A")
           super().__init__()

   class B(A):
       def __init__(self):
           print("B")
           super().__init__()

   class C(A):
       def __init__(self):
           print("C")
           super().__init__()

   class D(B, C):
       def __init__(self):
           print("D")
           super().__init__()

   D()
   ```

---

**答案**：

```python
# 1.
class Vehicle:
    def __init__(self, speed: int):
        self.speed = speed

    def move(self) -> str:
        return f"Moving at {self.speed} km/h"

class Car(Vehicle):
    def __init__(self, speed: int, wheels: int = 4):
        super().__init__(speed)
        self.wheels = wheels

class Bike(Vehicle):
    pass

# 2. 已在上面实现

# 3.
def make_speak(animals: list[Animal]) -> None:
    for animal in animals:
        print(animal.speak())

make_speak([Dog("Rex"), Cat("Whiskers")])

# 4. 输出 "C"
# MRO: D -> B -> C -> A -> object
# 按 MRO 顺序查找 who()：D 没有 → B 没有 → C 有！返回 "C"
# C.who() 在 MRO 中排在 A.who() 前面，所以覆盖了 A 的版本

# 5.
from typing import Protocol

class Readable(Protocol):
    def read(self) -> str: ...

class FileReader:
    def __init__(self, path: str):
        self.path = path

    def read(self) -> str:
        return open(self.path).read()

# 6.
# D
# B
# C
# A
# super() 按 MRO: D -> B -> C -> A 顺序调用
```
