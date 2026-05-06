# 附录B：Python语法速查卡（Java开发者专属）

## 基本语法

| Java代码 | Python写法 | 常见错误 |
|----------|------------|----------|
| `int x = 1;` | `x = 1` | ❌ 不写类型 ❌ |
| `String s = "hello";` | `s = "hello"` | ✅ 双引号都可以 |
| `List<String> list = new ArrayList<>();` | `list = []` | ❌ 不需要new ❌ |
| `Map<String, Integer> map = new HashMap<>();` | `map = {}` | ❌ 用{}不是() ❌ |
| `boolean flag = true;` | `flag = True` | ❌ 首字母大写 ❌ |
| `null` | `None` | ❌ 不是null ❌ |

## 控制流

| Java代码 | Python写法 | 常见错误 |
|----------|------------|----------|
| `if (x > 0) {}` | `if x > 0:` | ❌ 没有括号❌ |
| `else if (x < 0) {}` | `elif x < 0:` | ❌ 不是else if ❌ |
| `for (int i = 0; i < 10; i++) {}` | `for i in range(10):` | ❌ 没有i++ ❌ |
| `for (String item : list) {}` | `for item in list:` | ❌ 不是: ❌ |
| `while (true) {}` | `while True:` | ❌ True大写 ❌ |
| `break;` | `break` | ✅ 不用分号 |
| `continue;` | `continue` | ✅ 不用分号 |

## 函数

| Java代码 | Python写法 | 常见错误 |
|----------|------------|----------|
| `public int add(int a, int b) {}` | `def add(a: int, b: int) -> int:` | ❌ 用:结尾 ❌ |
| `return;` | `return` 或 `return None` | ❌ 要明确返回None ❌ |
| `void` | `-> None` | ❌ 不能省略 ❌ |

## 异常

| Java代码 | Python写法 | 常见错误 |
|----------|------------|----------|
| `try {} catch (Exception e) {}` | `try: ... except Exception:` | ❌ 没有catch ❌ |
| `throw new Exception();` | `raise Exception()` | ❌ 不是throw ❌ |
| `finally {}` | `finally:` | ❌ 没有大括号 ❌ |

## 面向对象

| Java代码 | Python写法 | 常见错误 |
|----------|------------|----------|
| `class Foo {}` | `class Foo:` | ❌ 用:结尾 ❌ |
| `this.name = name;` | `self.name = name` | ❌ 必须写self ❌ |
| `public void foo() {}` | `def foo(self):` | ❌ 第一个参数是self ❌ |
| `super(args);` | `super().__init__(args)` | ❌ 不是直接调用 ❌ |
| `obj instanceof String` | `isinstance(obj, str)` | ❌ 不是.instanceof ❌ |

## 常用操作

| Java代码 | Python写法 | 说明 |
|----------|------------|------|
| `list.add(item)` | `list.append(item)` | 添加元素 |
| `list.get(i)` | `list[i]` | 获取元素 |
| `list.size()` | `len(list)` | 获取长度 |
| `map.get(key)` | `map[key]` | 获取值 |
| `map.containsKey(key)` | `key in map` | 检查键 |
| `String.format("%s%d", a, b)` | `f"{a}{b}"` | 字符串格式化 |
| `Arrays.asList(1,2,3)` | `[1, 2, 3]` | 创建列表 |
