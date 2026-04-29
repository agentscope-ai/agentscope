# 03 - 装饰器

## 什么是装饰器？

装饰器是 Python 的 AOP（面向切面编程）机制。类似 Java 的：
- 拦截器（Interceptor）
- AOP 通知（@Before, @After）
- 代理模式

```python
# 装饰器语法
@my_decorator
def my_function():
    pass

# 等价于
my_function = my_decorator(my_function)
```

`★ Insight ─────────────────────────────────────`
- 装饰器本质是一个**高阶函数**：接收函数，返回新函数
- `@decorator` 只是语法糖，让代码更简洁
- 可以叠加多个装饰器，执行顺序从下到上
`─────────────────────────────────────────────────`

## 基础装饰器

### 无参数装饰器

```python
# 定义装饰器
def log_calls(func):
    """记录函数调用"""
    def wrapper(*args, **kwargs):  # 接收任意参数
        print(f"Calling {func.__name__}")
        result = func(*args, **kwargs)  # 调用原函数
        print(f"{func.__name__} returned {result}")
        return result
    return wrapper

# 使用
@log_calls
def add(a, b):
    return a + b

# 等价于: add = log_calls(add)

add(1, 2)
# 输出:
# Calling add
# add returned 3
```

**Java 对照理解**：

```java
// Java 拦截器（简化版）
@Aspect
@Component
public class LoggingAspect {
    @Around("execution(* add(..))")
    public Object log(ProceedingJoinPoint pjp) throws Throwable {
        System.out.println("Calling " + pjp.getSignature().getName());
        Object result = pjp.proceed();
        System.out.println("returned " + result);
        return result;
    }
}
```

### 带参数的装饰器

```python
def repeat(times: int):
    """重复执行指定次数"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            results = []
            for _ in range(times):
                results.append(func(*args, **kwargs))
            return results
        return wrapper
    return decorator

@repeat(times=3)
def greet(name):
    return f"Hello, {name}"

greet("Alice")  # ["Hello, Alice", "Hello, Alice", "Hello, Alice"]
```

### 带 self 参数的装饰器（方法装饰）

```python
from functools import wraps

def require_auth(func):
    """验证用户权限的装饰器"""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if not getattr(self, 'is_authenticated', False):
            raise PermissionError("User not authenticated")
        return func(self, *args, **kwargs)
    return wrapper

class SecureAgent:
    def __init__(self) -> None:
        self.is_authenticated = False

    @require_auth
    def send_message(self, msg: str) -> str:
        return f"Sent: {msg}"

# 使用
agent = SecureAgent()
# agent.send_message("Hello")  # PermissionError
agent.is_authenticated = True
agent.send_message("Hello")  # "Sent: Hello"
```

**Java 对照**：

```java
// Java 方法拦截（需要反射或 AOP 框架）
@Aspect
@Component
public class AuthAspect {
    @Around("execution(* SecureAgent.sendMessage(..))")
    public Object checkAuth(ProceedingJoinPoint pjp) throws Throwable {
        SecureAgent agent = (SecureAgent) pjp.getThis();
        if (!agent.isAuthenticated()) {
            throw new PermissionException("User not authenticated");
        }
        return pjp.proceed();
    }
}
```

## AgentScope 源码示例

**文件**: `src/agentscope/agent/_agent_base.py:591-620`

```python
@classmethod
def register_class_hook(
    cls,
    hook_type: AgentHookTypes,
    hook_name: str,
    hook: Callable,
) -> None:
    """注册类级别钩子的类方法（注意：不是装饰器）

    这个方法用于注册一个钩子函数到类级别，对所有实例生效。
    类似 Java 中通过类名注册全局拦截器的概念。

    Args:
        hook_type: 钩子类型，如 "pre_reply"、"post_reply" 等
        hook_name: 钩子名称（可覆盖已有钩子）
        hook: 钩子函数
    """

    assert (
        hook_type in cls.supported_hook_types
    ), f"Invalid hook type: {hook_type}"

    hooks = getattr(cls, f"_class_{hook_type}_hooks")
    hooks[hook_name] = hook
```

**Java 对照理解**：

```java
// Java 中类似的全局拦截器注册概念
public class AgentBase {
    // 类级别拦截器注册表
    private static Map<String, Function<AgentContext, AgentContext>> preReplyHooks =
        new HashMap<>();

    // 注册拦截器（类似 Python 的 register_class_hook）
    public static void registerPreReplyHook(
        String name,
        Function<AgentContext, AgentContext> hook
    ) {
        preReplyHooks.put(name, hook);
    }
}

// 使用
AgentBase.registerPreReplyHook("myHook", ctx -> {
    // 前置处理逻辑
    return ctx;
});
```

## @staticmethod 和 @classmethod

```python
class MyClass:
    def instance_method(self, x):
        """实例方法 - 需要 self"""
        return x * 2

    @staticmethod
    def static_method(x):
        """静态方法 - 不需要 self，类似 Java static"""
        return x + 1

    @classmethod
    def class_method(cls, x):
        """类方法 - 自动接收 cls，类似工厂方法"""
        return cls(x * 10)  # 可以创建实例

# 调用
MyClass.static_method(5)      # 6 - 直接通过类调用
MyClass.class_method(5)       # MyClass(50) - 工厂方法
obj = MyClass()
obj.instance_method(5)        # 10 - 通过实例调用
```

**Java 对照**：

```java
public class MyClass {
    public int instanceMethod(int x) { return x * 2; }

    public static int staticMethod(int x) { return x + 1; }

    public static MyClass classMethod(int x) { return new MyClass(x * 10); }
}
```

## functools.wraps

```python
# 问题：不使用 wraps，原函数的元信息会丢失
def bad_decorator(func):
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

@bad_decorator
def my_func():
    """My docstring"""
    pass

print(my_func.__name__)  # "wrapper" - 错误！
print(my_func.__doc__)   # None - 元信息丢失

# 解决：使用 wraps 保留元信息
from functools import wraps

def good_decorator(func):
    @wraps(func)  # 保留原函数的 __name__, __doc__ 等
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

@good_decorator
def my_func():
    """My docstring"""
    pass

print(my_func.__name__)  # "my_func" - 正确！
print(my_func.__doc__)   # "My docstring" - 正确！
```

## 类装饰器

```python
# 类装饰器 - 类似于 Java 的注解处理器
def add_greeting(cls):
    """为类添加问候方法"""
    def greet(self):
        return f"Hello, I'm {self.name}"
    cls.greet = greet
    return cls

@add_greeting
class Person:
    def __init__(self, name: str):
        self.name = name

p = Person("Alice")
print(p.greet())  # "Hello, I'm Alice"
```

## 装饰器叠加

```python
# 多个装饰器 - 执行顺序从下到上
@decorator1  # 第2步执行
@decorator2  # 第1步执行
def my_func():
    pass

# 等价于: my_func = decorator1(decorator2(my_func))
```

```python
from functools import wraps

def decorator1(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        print("1 start")
        result = func(*args, **kwargs)
        print("1 end")
        return result
    return wrapper

def decorator2(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        print("2 start")
        result = func(*args, **kwargs)
        print("2 end")
        return result
    return wrapper

@decorator1
@decorator2
def hello():
    print("Hello!")

hello()
# 输出:
# 1 start
# 2 start
# Hello!
# 2 end
# 1 end
```

## 实用装饰器示例

### 1. 计时装饰器

```python
import time
from functools import wraps

def timing(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        print(f"{func.__name__} took {elapsed:.3f}s")
        return result
    return wrapper

# 异步版本
def async_timing(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        import time
        import asyncio
        start = time.time()
        result = await func(*args, **kwargs)
        elapsed = time.time() - start
        print(f"{func.__name__} took {elapsed:.3f}s")
        return result
    return wrapper

@async_timing
async def slow_async_function():
    await asyncio.sleep(1)
    return "Done"
```

### 2. 重试装饰器

```python
from functools import wraps
import time
import asyncio

def retry(max_attempts: int = 3, delay: float = 1.0):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator

# 异步版本
def async_retry(max_attempts: int = 3, delay: float = 1.0):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(delay)
            raise last_exception
        return wrapper
    return decorator

@async_retry(max_attempts=3, delay=0.5)
async def unstable_operation():
    # 可能失败的操作
    pass
```

### 3. 缓存装饰器（类似 Spring @Cacheable）

```python
from functools import wraps

def memoize(func):
    cache = {}
    @wraps(func)
    def wrapper(*args, **kwargs):
        key = str(args) + str(kwargs)
        if key not in cache:
            cache[key] = func(*args, **kwargs)
        return cache[key]
    return wrapper

@memoize
def expensive_computation(n):
    # 耗时的计算
    return n * n

# 注意：memoize 不适合有副作用或依赖外部状态的函数
```

### 4. 单例装饰器

```python
from functools import wraps

def singleton(cls):
    """确保类只有一个实例"""
    instances = {}
    @wraps(cls)
    def get_instance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]
    return get_instance

@singleton
class Config:
    def __init__(self):
        self.settings = {}

# 全局只有一个 Config 实例
config1 = Config()
config2 = Config()
assert config1 is config2  # 同一个对象

# 注意：isinstance(config1, Config) 会报错！
# 因为 Config 被替换为函数，不再是类
# isinstance(config1, Config.__wrapped__) 可以工作
```

**Java 对照**：

```java
// Java 单例模式
public class Config {
    private static Config instance;

    private Config() {}

    public static synchronized Config getInstance() {
        if (instance == null) {
            instance = new Config();
        }
        return instance;
    }
}
```

## 装饰器与类型注解

```python
from functools import wraps
from typing import TypeVar, Callable

F = TypeVar('F', bound=Callable)

def debug(func: F) -> F:
    """带完整类型注解的装饰器"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        print(f"Calling {func.__name__}")
        result = func(*args, **kwargs)
        print(f"{func.__name__} returned {result}")
        return result
    return wrapper  # type: ignore

@debug
def add(a: int, b: int) -> int:
    return a + b
```

## 练习题

1. **创建装饰器**：创建一个 `@deprecated` 装饰器，当调用被装饰的函数时打印警告

2. **修复代码**：以下代码有什么问题？
   ```python
   def log(func):
       print(f"Calling {func.__name__}")
       return func()

   @log
   def hello():
       return "Hello"
   ```

3. **装饰器叠加**：写出以下代码的输出：
   ```python
   def a(func): return lambda: print("A") or func()
   def b(func): return lambda: print("B") or func()

   @a
   @b
   def c(): print("C")
   ```

4. **带参数装饰器**：创建一个 `@repeat(n)` 装饰器，将函数执行 n 次并返回结果列表

5. **类方法装饰器**：创建一个 `@logged` 装饰器，用于类方法，记录方法被调用时的参数和返回值

---

**答案**：

```python
# 1. @deprecated 装饰器
from functools import wraps
import warnings

def deprecated(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        warnings.warn(f"{func.__name__} is deprecated",
                     DeprecationWarning, stacklevel=2)
        return func(*args, **kwargs)
    return wrapper

@deprecated
def old_function():
    return "old"

# 2. 问题：装饰器立即执行了函数，而不是包装它
# 正确写法应该是：
def log(func):
    def wrapper(*args, **kwargs):
        print(f"Calling {func.__name__}")
        return func(*args, **kwargs)
    return wrapper

# 3. 输出:
# A
# B
# C
# 因为装饰从下到上执行：先 c = b(c)，再 c = a(c)
# 调用时：先执行 a 的 wrapper（打印 A），然后调用原 c（已变成 b(c) 的结果）
# 原 c 被 b 包装，所以先执行 b 的 wrapper（打印 B），然后执行原 c（打印 C）

# 4. @repeat(n) 装饰器
def repeat(n: int):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            results = []
            for _ in range(n):
                results.append(func(*args, **kwargs))
            return results
        return wrapper
    return decorator

@repeat(3)
def greet(name):
    return f"Hello, {name}"

print(greet("Alice"))  # ["Hello, Alice", "Hello, Alice", "Hello, Alice"]

# 5. @logged 类方法装饰器
def logged(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        print(f"Calling {func.__name__} with args={args}, kwargs={kwargs}")
        result = func(self, *args, **kwargs)
        print(f"{func.__name__} returned {result}")
        return result
    return wrapper

class MyClass:
    @logged
    def compute(self, x, y):
        return x + y

obj = MyClass()
obj.compute(1, 2)
# Calling compute with args=(1, 2), kwargs={}
# compute returned 3
```
