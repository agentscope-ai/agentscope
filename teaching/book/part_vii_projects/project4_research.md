# 项目4：深度研究助手

> **难度**：⭐⭐⭐⭐（专家）
> **预计时间**：8小时

---

## 🎯 学习目标

- 多层Agent编排
- 并行搜索与结果聚合
- 迭代式研究流程

---

## 1. 需求分析

用户输入研究主题，系统自动进行多角度深度研究。

```
用户: 研究区块链在供应链中的应用
Agent:
  1. 搜索→整理相关论文
  2. 分析→行业应用案例
  3. 评估→优劣势分析
  4. 报告→生成综合报告
```

---

## 2. 系统设计

```
                    ┌─ 搜索Agent ──→ Web Search
User ──→ 规划Agent ─┼─ 分析Agent ──→ RAG Knowledge
                    └─ 评估Agent ──→ 分析模型
                              ↓
                         汇总Agent → 最终报告
```

---

## 3. 核心代码

```python
# 使用FanoutPipeline并行执行研究
research_agents = [
    SearchAgent(model, topic),
    AnalysisAgent(model, topic),
    EvaluationAgent(model, topic)
]

# FanoutPipeline并行分发
fanout = FanoutPipeline(agents=research_agents)
results = await fanout(topic)

# 汇总结果
summarizer = SummarizerAgent(model)
final_report = await summarizer(results)
```

---

★ **Insight** ─────────────────────────────────────
- **FanoutPipeline** = 并行分发，同时执行
- **层级编排** = 规划→执行→汇总
- **结果聚合** = 多视角综合
─────────────────────────────────────────────────
