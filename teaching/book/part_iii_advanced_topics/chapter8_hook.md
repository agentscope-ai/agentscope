# 第8章 Hook机制

> **目标**：理解Hook如何拦截和扩展Agent行为

---

## 🎯 学习目标

学完之后，你能：
- 理解Hook的拦截机制
- 使用Hook扩展Agent行为
- 编写自定义Hook
- 理解AgentScope的Hook系统

---

## 🚀 先跑起来

```python
from agentscope.hook import Hook

class LoggingHook(Hook):
    def before_reasoning(self, agent, msg):
        print(f"开始思考: {msg.content}")
    
    def after_reasoning(self, agent, thought):
        print(f"思考结果: {thought}")

# 注册Hook
agent.add_hook(LoggingHook())
```

---

## 🔍 Hook是什么

### 拦截器模式

Hook类似Java的拦截器（Interceptor）或AOP切面：

```
Agent执行流程：
  输入 → [Hook1] → [Hook2] → ... → 输出
              ↑
         可以在此处拦截
```

### Hook调用点

| 阶段 | Hook方法 | 用途 |
|------|----------|------|
| 思考前 | `before_reasoning` | 日志、验证 |
| 思考后 | `after_reasoning` | 记录思考过程 |
| 行动前 | `before_acting` | 权限检查 |
| 行动后 | `after_acting` | 结果处理 |
| 回复前 | `before_response` | 修改回复 |

---

## 💡 Java开发者注意

Hook类似Java的拦截器：

```python
# Python Hook
class AuthHook(Hook):
    def before_acting(self, agent, action):
        if not user.has_permission(action):
            raise PermissionError()
```

```java
// Java Interceptor
@AroundInvoke
public Object intercept(InvocationContext ctx) {
    if (!user.hasPermission(ctx.getMethod())) {
        throw new SecurityException();
    }
    return ctx.proceed();
}
```

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Hook和装饰器有什么区别？**
   - 装饰器修改类/函数定义
   - Hook在运行时拦截调用
   - Hook可以动态添加/移除

2. **Hook适合什么场景？**
   - 日志记录
   - 性能监控
   - 权限检查
   - 结果缓存

</details>

---

★ **Insight** ─────────────────────────────────────
- **Hook = 拦截器**，在关键点插入处理逻辑
- **运行时拦截**，比装饰器更灵活
- **AOP思想**，横切关注点分离
─────────────────────────────────────────────────
