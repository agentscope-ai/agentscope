# 1-0 Python基础速览

> **目标**：45分钟内掌握Python核心语法，为后续学习AgentScope打下基础

---

## 🎯 这一章的目标

学完之后，你能：
- 理解Python的基本语法与Java的区别
- 写出会动的Python代码（不用等到第2章）
- 掌握Python特有的缩进、None、动态类型

---

## 🚀 先跑起来

```python showLineNumbers
# Python的Hello World - 比Java简单多了！
print("Hello, AgentScope!")

# Java需要这样写：
# public class Hello {
#     public static void main(String[] args) {
#         System.out.println("Hello, AgentScope!");
#     }
# }

# Python变量不需要声明类型
message = "你好，Agent！"  # 这就是一个变量
print(message)
```

💡 **Java开发者注意**：Python不需要`int x = 1`这种类型声明，直接`x = 1`就行！

---

## 🔍 Python vs Java：核心差异

### 1. 缩进代替大括号

```python
# Python - 用缩进表示代码块
if message:
    print("有消息")
    print("这是消息内容")
else:
    print("没有消息")

# Java 用大括号
# if (message != null) {
#     System.out.println("有消息");
#     System.out.println("这是消息内容");
# } else {
#     System.out.println("没有消息");
# }
```

⚠️ **坑预警**：Python缩进最容易出错！
- 用4个空格，不要混用Tab
- 建议在IDE中设置"用空格代替Tab"

### 2. None代替null

```python
# Python
result = None
if result is None:
    print("没有结果")

# Java
# Object result = null;
# if (result == null) {
#     System.out.println("没有结果");
# }
```

💡 **Java开发者注意**：判断None要用`is None`，不是`== None`

### 3. 动态类型

```python
# Python - 变量类型可以随时变
x = 1          # x是int
x = "hello"    # x变成str了！
x = [1, 2, 3] # x又变成list了

# Java - 类型是固定的
# int x = 1;
# x = "hello";  // 编译错误！
```

---

## 📖 变量和数据类型

### 基本数据类型

| Python | Java | 说明 |
|--------|------|------|
| `int` | `int` | 整数 |
| `float` | `double` | 浮点数 |
| `str` | `String` | 字符串 |
| `bool` | `boolean` | 布尔值 |
| `None` | `null` | 空值 |

```python
# 整数
age = 25

# 浮点数
temperature = 36.6

# 字符串 - 单引号双引号都可以
name = "Alice"
greeting = 'Hello'

# 布尔值 - 注意大小写
is_active = True   # Python首字母大写
is_empty = False

# None
result = None
```

### 容器类型

| Python | Java | 说明 |
|--------|------|------|
| `list` | `List` | 列表（有序可变） |
| `dict` | `Map` | 字典（键值对） |
| `set` | `Set` | 集合（无序不重复） |
| `tuple` | 不常用 | 元组（不可变） |

```python
# 列表 - 就像Java的ArrayList
fruits = ["苹果", "香蕉", "橙子"]
print(fruits[0])  # 索引从0开始
fruits.append("葡萄")  # 添加元素

# 字典 - 就像Java的HashMap
person = {"name": "Alice", "age": 25}
print(person["name"])  # 通过key访问
person["city"] = "北京"  # 添加新键值对

# 集合 - 无序不重复
colors = {"红色", "绿色", "蓝色"}
colors.add("黄色")  # 添加
colors.add("红色")  # 重复添加被忽略
```

💡 **Java开发者注意**：
- Python的`list`用方括号`[]`，不是`new ArrayList<>()`
- Python的`dict`用大括号`{}`，和Java的`{}`不一样

---

## 📖 控制流

### if语句

```python
# Python用elif，Java用else if
age = 18

if age < 0:
    print("无效年龄")
elif age < 18:
    print("未成年")
elif age < 65:
    print("成年人")
else:
    print("老年人")
```

### for循环

```python
# Python的for是遍历，不是计数器
fruits = ["苹果", "香蕉", "橙子"]

# 遍历列表
for fruit in fruits:
    print(fruit)

# Java的for-each是这样：
# for (String fruit : fruits) {
#     System.out.println(fruit);
# }

# 如果需要索引，用enumerate
for i, fruit in enumerate(fruits):
    print(f"{i}: {fruit}")
```

⚠️ **坑预警**：Python没有`i++`！
```python
# ❌ 错误
# for i in range(10):
#     print(i++)
# ✅ 正确
for i in range(10):
    print(i)
    i += 1  # 如果需要手动递增
```

### while循环

```python
count = 0
while count < 5:
    print(count)
    count += 1
```

---

## 📖 函数

### 定义函数

```python
# Python用def定义函数
def greet(name: str) -> str:
    """这是函数的文档字符串
    
    Args:
        name: 打招呼的名字
    
    Returns:
        问候语
    """
    return f"你好，{name}！"

# 调用函数
message = greet("Alice")
print(message)

# Java对比：
# public String greet(String name) {
#     return "你好，" + name + "！";
# }
```

💡 **Java开发者注意**：
- Python不需要public/private
- 返回类型写在参数后面`: str`，不是前面
- `-> str`表示返回str类型，不写默认返回None

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

print(sum(1, 2, 3))      # 6
print(sum(1, 2, 3, 4, 5)) # 15

# **kwargs 接收任意数量的关键字参数
def print_info(**info):
    for key, value in info.items():
        print(f"{key}: {value}")

print_info(name="Alice", age=25)
# name: Alice
# age: 25
```

---

## 📖 类和对象（速览）

### 定义类

```python
class Person:
    """一个人的类"""
    
    def __init__(self, name: str, age: int):
        """构造器 - Java的constructor"""
        self.name = name      # self就是Python的this
        self.age = age
    
    def greet(self) -> str:
        """打招呼"""
        return f"你好，我是{self.name}"

# 创建对象 - 不需要new！
person = Person("Alice", 25)
print(person.greet())

# Java对比：
# public class Person {
#     private String name;
#     private int age;
#     
#     public Person(String name, int age) {
#         this.name = name;
#         this.age = age;
#     }
#     
#     public String greet() {
#         return "你好，我是" + this.name;
#     }
# }
```

💡 **Java开发者注意**：
- `__init__`不是构造器！真正的构造器是`__new__`（很少用）
- `self`必须显式声明，Java的this是隐式的

### @property（Pythonic的getter/setter）

```python
class Person:
    def __init__(self, name: str):
        self._name = name  # 约定私有，用下划线开头
    
    @property
    def name(self) -> str:
        """getter - Pythonic的方式"""
        return self._name
    
    @name.setter
    def name(self, value: str) -> None:
        """setter"""
        self._name = value

# 使用 - 像访问属性一样
person = Person("Alice")
print(person.name)      # 自动调用getter
person.name = "Bob"      # 自动调用setter

# Java需要这样：
# person.getName();
# person.setName("Bob");
```

---

## 🎯 思考题

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

---

★ **Insight** ─────────────────────────────────────
- Python用缩进表示代码块，**缩进就是语法**
- `None`是Python的"空"，判断用`is None`
- Python是**动态类型**语言，变量类型可以随时变
- `self`必须显式声明，不像Java的`this`是隐式的
─────────────────────────────────────────────────

---

## 📖 附录：Python基础速查表

| 场景 | Python | Java |
|------|--------|------|
| 声明变量 | `x = 1` | `int x = 1;` |
| 字符串 | `"hello"` 或 `'hello'` | `"hello"` |
| 空值 | `None` | `null` |
| 布尔值 | `True` / `False` | `true` / `false` |
| 列表 | `[1, 2, 3]` | `new ArrayList<>()` |
| 字典 | `{"a": 1}` | `new HashMap<>()` |
| 条件 | `if x > 0:` | `if (x > 0) {` |
| 循环 | `for i in range(10):` | `for (int i = 0; i < 10; i++)` |
| 函数 | `def foo(x: int) -> int:` | `public int foo(int x)` |
| 类 | `class Person:` | `public class Person` |
| 构造器 | `def __init__(self):` | `public Person()` |
| 方法self | `def foo(self):` | `void foo()`（隐式this） |
| 注释 | `# 注释` | `// 注释` 或 `/* 注释 */` |
