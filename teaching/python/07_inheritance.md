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

## Python 继承

```python
# Python 类继承 - 语法类似但更简洁
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
Animal a = new Dog();
a.speak()  # "Woof!"
```

`★ Insight ─────────────────────────────────────`
- Python 继承语法：`class Child(Parent):`
- 没有 `extends` 关键字，直接在类名后加括号
- 方法重写不需要 `@Override` 注解（但可以有）
`─────────────────────────────────────────────────`

## AgentScope 源码示例

**文件**: `src/agentscope/agent/_agent_base.py`

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
    # Python 支持多继承，类似 Java 的多接口实现

    supported_hook_types: list[str] = [
        "pre_reply",
        "post_reply",
    ]

    def __init__(self) -> None:
        super().__init__()  # 调用父类构造器
        self.id = shortuuid.uuid()
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

public class AgentBase extends StateModule {
    public static List<String> supportedHookTypes = Arrays.asList(
        "pre_reply", "post_reply"
    );

    public AgentBase() {
        super();  // 调用父类构造器
        this.id = UUID.randomUUID().toString();
    }
}
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
        return f"{super().speak()} Woof!"  # 调用父类方法

d = Dog("Rex", "Labrador")
print(d.name)   # "Rex"
print(d.breed)  # "Labrador"
print(d.speak())  # "... Woof!"
```

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

**MRO（方法解析顺序）**：

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
# agent = AbstractAgent()

# ✅ 正确 - 必须实现所有抽象方法
class ConcreteAgent(AbstractAgent):
    async def reply(self, *args, **kwargs) -> "Msg":
        return Msg("assistant", "Hello", "assistant")

    async def observe(self, msg: "Msg") -> None:
        print(f"Observed: {msg}")
```

| Python | Java | 说明 |
|--------|------|------|
| `class A(ABC)` | `abstract class A` | 抽象类 |
| `@abstractmethod` | `abstract void foo()` | 抽象方法 |
| `class B(A)` | `class B extends A` | 继承 |
| `class C(A, B)` | 不支持 | 多继承 |

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
print(isinstance(dog, Dog))    # True
print(isinstance(dog, Animal))  # True
print(isinstance(dog, Cat))     # False

# issubclass - 检查类继承关系
print(issubclass(Dog, Animal))  # True
print(issubclass(Animal, Dog))  # False
```

## Protocol（结构化子类型）

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

# 类似 Java：任何实现了 fly() 和 swim() 的类都满足 Flyable & Swimmable
def process(obj: Flyable & Swimmable) -> None:
    print(obj.fly(), obj.swim())

process(Duck())  # OK - Duck 实现了两个方法
```

## 练习题

1. **创建继承关系**：创建 `Vehicle` 基类，有 `speed` 属性和 `move()` 方法，然后创建 `Car` 和 `Bike` 子类

2. **super() 使用**：为 `Car` 添加 `__init__`，调用父类并添加 `wheels` 属性

3. **多态实现**：创建函数 `make_speak(animals: list[Animal])` 让所有动物说话

4. **判断输出**：
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
# MRO: D -> B -> C -> A
# 从左到右找，先找 B，没找到 who，再找 C，找到了 "C"
```
