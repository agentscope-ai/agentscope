#!/usr/bin/env python3
"""
自适应学习路径生成器
根据用户背景和目标生成个性化学习路径，支持拓扑排序
"""

from __future__ import annotations

from typing import Literal


class LearningPathGenerator:
    """生成个性化学习路径"""

    COURSE_GRAPH = {
        # ── Python 基础 ──
        "python/01_class_object": {
            "title": "Python 类与对象",
            "prerequisites": [],
            "java_equivalent": "Java 类与 OOP",
            "duration": "30分钟",
            "category": "python",
        },
        "python/02_async_await": {
            "title": "Python 异步编程",
            "prerequisites": ["python/01_class_object"],
            "java_equivalent": "CompletableFuture",
            "duration": "45分钟",
            "category": "python",
        },
        "python/03_decorator": {
            "title": "Python 装饰器",
            "prerequisites": ["python/01_class_object"],
            "java_equivalent": "AOP / 拦截器",
            "duration": "30分钟",
            "category": "python",
        },
        "python/04_type_hints": {
            "title": "Python 类型提示",
            "prerequisites": ["python/01_class_object"],
            "java_equivalent": "Java Generics",
            "duration": "35分钟",
            "category": "python",
        },
        "python/05_dataclass": {
            "title": "Python 数据类",
            "prerequisites": ["python/01_class_object"],
            "java_equivalent": "Lombok @Data",
            "duration": "25分钟",
            "category": "python",
        },
        "python/06_context_manager": {
            "title": "Python 上下文管理器",
            "prerequisites": ["python/02_async_await"],
            "java_equivalent": "try-with-resources",
            "duration": "30分钟",
            "category": "python",
        },
        "python/07_inheritance": {
            "title": "Python 继承与多态",
            "prerequisites": ["python/01_class_object"],
            "java_equivalent": "extends / implements",
            "duration": "35分钟",
            "category": "python",
        },
        "python/08_metaclass": {
            "title": "Python 元类",
            "prerequisites": ["python/03_decorator", "python/07_inheritance"],
            "java_equivalent": "注解处理器",
            "duration": "40分钟",
            "category": "python",
        },
        # ── AgentScope 基础 ──
        "01_project_overview": {
            "title": "AgentScope 项目概述",
            "prerequisites": [],
            "java_equivalent": "框架介绍",
            "duration": "10分钟",
            "category": "basics",
        },
        "02_installation": {
            "title": "环境搭建",
            "prerequisites": [],
            "java_equivalent": "Maven / Gradle 配置",
            "duration": "15分钟",
            "category": "basics",
        },
        "03_quickstart": {
            "title": "快速入门",
            "prerequisites": ["01_project_overview", "02_installation"],
            "java_equivalent": "Hello World",
            "duration": "20分钟",
            "category": "basics",
        },
        "04_core_concepts": {
            "title": "核心概念",
            "prerequisites": ["01_project_overview", "python/01_class_object"],
            "java_equivalent": "核心 API",
            "duration": "30分钟",
            "category": "basics",
        },
        "05_architecture": {
            "title": "架构设计",
            "prerequisites": ["04_core_concepts"],
            "java_equivalent": "Spring 分层架构",
            "duration": "25分钟",
            "category": "basics",
        },
        "06_development_guide": {
            "title": "开发指南",
            "prerequisites": ["03_quickstart"],
            "java_equivalent": "开发规范",
            "duration": "20分钟",
            "category": "basics",
        },
        "07_java_comparison": {
            "title": "Java 开发者视角",
            "prerequisites": ["04_core_concepts"],
            "java_equivalent": "对照表",
            "duration": "15分钟",
            "category": "basics",
        },
        # ── 深度模块：核心层 ──
        "module_agent_deep": {
            "title": "Agent 模块深度分析",
            "prerequisites": ["04_core_concepts", "python/02_async_await"],
            "java_equivalent": "Agent 模式",
            "duration": "45分钟",
            "category": "deep_core",
        },
        "module_model_deep": {
            "title": "Model 模块深度分析",
            "prerequisites": ["04_core_concepts"],
            "java_equivalent": "LLM 集成",
            "duration": "40分钟",
            "category": "deep_core",
        },
        "module_tool_mcp_deep": {
            "title": "Tool/MCP 模块深度分析",
            "prerequisites": ["04_core_concepts", "python/03_decorator"],
            "java_equivalent": "工具调用",
            "duration": "35分钟",
            "category": "deep_core",
        },
        "module_memory_rag_deep": {
            "title": "Memory/RAG 模块深度分析",
            "prerequisites": ["04_core_concepts"],
            "java_equivalent": "缓存 / 搜索",
            "duration": "40分钟",
            "category": "deep_core",
        },
        "module_pipeline_infra_deep": {
            "title": "Pipeline/基础设施深度分析",
            "prerequisites": ["04_core_concepts", "python/02_async_await"],
            "java_equivalent": "工作流引擎",
            "duration": "35分钟",
            "category": "deep_core",
        },
        # ── 深度模块：基础设施层 ──
        "module_config_deep": {
            "title": "Config 配置系统深度分析",
            "prerequisites": ["04_core_concepts"],
            "java_equivalent": "Spring Config",
            "duration": "25分钟",
            "category": "deep_infra",
        },
        "module_dispatcher_deep": {
            "title": "Dispatcher 调度器深度分析",
            "prerequisites": ["module_pipeline_infra_deep"],
            "java_equivalent": "EventBus",
            "duration": "35分钟",
            "category": "deep_infra",
        },
        "module_message_deep": {
            "title": "Message 消息系统深度分析",
            "prerequisites": ["04_core_concepts"],
            "java_equivalent": "DTO / VO",
            "duration": "30分钟",
            "category": "deep_infra",
        },
        "module_runtime_deep": {
            "title": "Runtime 运行时深度分析",
            "prerequisites": ["module_pipeline_infra_deep"],
            "java_equivalent": "线程池 / 调度器",
            "duration": "35分钟",
            "category": "deep_infra",
        },
        # ── 深度模块：支撑层 ──
        "module_file_deep": {
            "title": "File 文件操作深度分析",
            "prerequisites": ["04_core_concepts"],
            "java_equivalent": "File I/O",
            "duration": "25分钟",
            "category": "deep_support",
        },
        "module_utils_deep": {
            "title": "Utils 工具模块深度分析",
            "prerequisites": ["04_core_concepts"],
            "java_equivalent": "Apache Commons",
            "duration": "30分钟",
            "category": "deep_support",
        },
        "module_state_deep": {
            "title": "StateModule 状态管理深度分析",
            "prerequisites": ["python/07_inheritance"],
            "java_equivalent": "Serializable",
            "duration": "20分钟",
            "category": "deep_support",
        },
        "module_formatter_deep": {
            "title": "Formatter 消息格式化深度分析",
            "prerequisites": ["module_model_deep"],
            "java_equivalent": "MessageConverter",
            "duration": "35分钟",
            "category": "deep_support",
        },
        "module_embedding_token_deep": {
            "title": "Embedding/Token 计数深度分析",
            "prerequisites": ["module_model_deep"],
            "java_equivalent": "Tokenizer",
            "duration": "30分钟",
            "category": "deep_support",
        },
        # ── 深度模块：扩展层 ──
        "module_plan_deep": {
            "title": "Plan 计划系统深度分析",
            "prerequisites": ["module_agent_deep"],
            "java_equivalent": "Task Scheduler",
            "duration": "40分钟",
            "category": "deep_extended",
        },
        "module_session_deep": {
            "title": "Session 会话持久化深度分析",
            "prerequisites": ["module_state_deep"],
            "java_equivalent": "Session 管理",
            "duration": "25分钟",
            "category": "deep_extended",
        },
        "module_tracing_deep": {
            "title": "Tracing 链路追踪深度分析",
            "prerequisites": ["05_architecture"],
            "java_equivalent": "Micrometer / Jaeger",
            "duration": "35分钟",
            "category": "deep_extended",
        },
        "module_evaluate_deep": {
            "title": "Evaluate 评估框架深度分析",
            "prerequisites": ["module_agent_deep"],
            "java_equivalent": "Benchmark",
            "duration": "25分钟",
            "category": "deep_extended",
        },
        "module_tuner_deep": {
            "title": "Tuner 智能体调优深度分析",
            "prerequisites": ["module_model_deep"],
            "java_equivalent": "超参调优",
            "duration": "30分钟",
            "category": "deep_extended",
        },
    }

    def _topological_sort(self, course_ids: set[str]) -> list[str]:
        """拓扑排序：按依赖关系排列课程"""
        visited: set[str] = set()
        result: list[str] = []

        def visit(cid: str) -> None:
            if cid in visited or cid not in self.COURSE_GRAPH:
                return
            visited.add(cid)
            for prereq in self.COURSE_GRAPH[cid].get("prerequisites", []):
                if prereq in course_ids:
                    visit(prereq)
            result.append(cid)

        for cid in course_ids:
            visit(cid)

        return result

    def _select_courses(
        self,
        background: str,
        goal: str,
    ) -> set[str]:
        """根据背景和目标选择课程集合"""
        selected: set[str] = set()

        # 基础课程：所有人都需要
        basics = {
            "01_project_overview",
            "02_installation",
            "03_quickstart",
            "04_core_concepts",
        }
        selected |= basics

        # Python 基础：java_only 和 both 需要
        if background in ("java_only", "both"):
            python_courses = {
                cid for cid, info in self.COURSE_GRAPH.items()
                if info["category"] == "python"
            }
            # python_only 可以跳过部分
            if background == "java_only":
                selected |= python_courses
            else:
                # both: 只选进阶的（假设已懂基础）
                selected |= {"python/02_async_await", "python/03_decorator",
                             "python/08_metaclass"}

        # 根据目标添加深度课程
        if goal == "quick_start":
            selected |= {"module_agent_deep"}
        elif goal == "full_stack":
            selected |= {
                cid for cid, info in self.COURSE_GRAPH.items()
                if info["category"] == "deep_core"
            }
        else:  # expert
            selected |= set(self.COURSE_GRAPH.keys())

        # 补全缺失的先修课程
        changed = True
        while changed:
            changed = False
            for cid in list(selected):
                for prereq in self.COURSE_GRAPH[cid].get("prerequisites", []):
                    if prereq in self.COURSE_GRAPH and prereq not in selected:
                        selected.add(prereq)
                        changed = True

        return selected

    def generate_path(
        self,
        background: Literal["java_only", "python_only", "both"],
        goal: Literal["quick_start", "full_stack", "expert"],
        time_per_day: int = 2,
    ) -> dict:
        """生成学习路径"""
        selected = self._select_courses(background, goal)
        sorted_ids = self._topological_sort(selected)

        courses = []
        total_minutes = 0
        for cid in sorted_ids:
            info = self.COURSE_GRAPH[cid]
            minutes = int(info["duration"].replace("分钟", ""))
            total_minutes += minutes
            courses.append({
                "id": cid,
                "title": info["title"],
                "duration": info["duration"],
                "java_equivalent": info["java_equivalent"],
                "prerequisites": info["prerequisites"],
                "category": info["category"],
            })

        total_hours = total_minutes / 60
        return {
            "courses": courses,
            "total_hours": round(total_hours, 1),
            "total_minutes": total_minutes,
            "days_estimate": max(1, round(total_hours / time_per_day)),
            "background": background,
            "goal": goal,
        }

    def print_path(self, result: dict) -> None:
        """打印学习路径"""
        print("=" * 70)
        print(f"个性化学习路径 - 背景: {result['background']} | 目标: {result['goal']}")
        print("=" * 70)
        print(f"总时长: {result['total_hours']}h ({result['total_minutes']}min) | "
              f"预计 {result['days_estimate']} 天\n")

        current_category = ""
        for i, course in enumerate(result["courses"], 1):
            cat = course["category"]
            if cat != current_category:
                labels = {
                    "python": "Python 基础",
                    "basics": "AgentScope 基础",
                    "deep_core": "深度模块 - 核心层",
                    "deep_infra": "深度模块 - 基础设施层",
                    "deep_support": "深度模块 - 支撑层",
                    "deep_extended": "深度模块 - 扩展层",
                }
                print(f"\n--- {labels.get(cat, cat)} ---")
                current_category = cat

            prereq_str = ""
            if course["prerequisites"]:
                prereq_str = f" | 先修: {len(course['prerequisites'])}项"
            print(f"  {i}. {course['title']} ({course['duration']}){prereq_str}")


if __name__ == "__main__":
    generator = LearningPathGenerator()

    # Java 开发者 → 全栈目标
    print(">>> 场景: Java 开发者，全栈目标\n")
    path = generator.generate_path(background="java_only", goal="full_stack")
    generator.print_path(path)

    print("\n" + "=" * 70)

    # 已有 Python 基础 → 快速上手
    print("\n>>> 场景: 有 Python 基础，快速上手\n")
    path2 = generator.generate_path(background="python_only", goal="quick_start")
    generator.print_path(path2)

    print("\n" + "=" * 70)

    # 专家路线
    print("\n>>> 场景: Java 开发者，专家目标\n")
    path3 = generator.generate_path(background="java_only", goal="expert")
    generator.print_path(path3)
