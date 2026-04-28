#!/usr/bin/env python3
"""
自适应学习路径生成器
根据用户背景生成个性化学习路径
"""

from typing import Literal

class LearningPathGenerator:
    """生成个性化学习路径"""

    # 课程依赖图
    COURSE_GRAPH = {
        # Python 基础
        "python/01_class_object": {
            "title": "Python 类与对象",
            "prerequisites": [],
            "java_equivalent": "Java 类与OOP",
            "duration": "30分钟"
        },
        "python/02_async_await": {
            "title": "Python 异步编程",
            "prerequisites": ["python/01_class_object"],
            "java_equivalent": "CompletableFuture",
            "duration": "45分钟"
        },
        "python/03_decorator": {
            "title": "Python 装饰器",
            "prerequisites": ["python/01_class_object"],
            "java_equivalent": "AOP/拦截器",
            "duration": "30分钟"
        },
        # AgentScope 基础
        "01_project_overview": {
            "title": "AgentScope 项目概述",
            "prerequisites": [],
            "java_equivalent": "框架介绍",
            "duration": "10分钟"
        },
        "04_core_concepts": {
            "title": "核心概念",
            "prerequisites": ["01_project_overview", "python/01_class_object"],
            "java_equivalent": "核心API",
            "duration": "30分钟"
        },
        # 深度模块
        "module_agent_deep": {
            "title": "Agent 模块深度",
            "prerequisites": ["04_core_concepts", "python/02_async_await"],
            "java_equivalent": "Agent 模式",
            "duration": "45分钟"
        },
        "module_model_deep": {
            "title": "Model 模块深度",
            "prerequisites": ["04_core_concepts"],
            "java_equivalent": "LLM 集成",
            "duration": "40分钟"
        },
        "module_tool_mcp_deep": {
            "title": "Tool/MCP 模块深度",
            "prerequisites": ["04_core_concepts", "python/03_decorator"],
            "java_equivalent": "工具调用",
            "duration": "35分钟"
        },
    }

    def generate_path(
        self,
        background: Literal["java_only", "python_only", "both"],
        goal: Literal["quick_start", "full_stack", "expert"],
        time_per_day: int = 2
    ) -> list[dict]:
        """生成学习路径"""

        # 基础路径（所有Java开发者都需要）
        base_courses = [
            "python/01_class_object",  # Python基础
            "python/02_async_await",  # 异步编程
            "01_project_overview",      # 框架概述
            "04_core_concepts",        # 核心概念
        ]

        # 根据目标添加进阶课程
        if goal == "quick_start":
            advanced = ["03_quickstart", "module_agent_deep"]
        elif goal == "full_stack":
            advanced = [
                "module_agent_deep",
                "module_model_deep",
                "module_tool_mcp_deep",
                "module_memory_rag_deep",
                "module_pipeline_infra_deep",
            ]
        else:  # expert
            advanced = list(self.COURSE_GRAPH.keys())

        # 去重并保持顺序
        all_courses = []
        seen = set()
        for c in base_courses + advanced:
            if c not in seen and c in self.COURSE_GRAPH:
                all_courses.append(c)
                seen.add(c)

        # 构建输出
        path = []
        total_hours = 0
        for course_id in all_courses:
            info = self.COURSE_GRAPH[course_id]
            path.append({
                "id": course_id,
                "title": info["title"],
                "duration": info["duration"],
                "java_equivalent": info["java_equivalent"],
                "prerequisites": info["prerequisites"]
            })
            # 估算时间
            hours = int(info["duration"].split("分钟")[0]) / 60
            total_hours += hours

        return {
            "courses": path,
            "total_hours": total_hours,
            "days_estimate": round(total_hours / time_per_day),
            "goal": goal
        }

    def print_path(self, path: dict):
        """打印学习路径"""
        print("=" * 70)
        print(f"📚 个性化学习路径 - 目标: {path['goal']}")
        print("=" * 70)
        print(f"总时长: {path['total_hours']:.1f} 小时 | 预计 {path['days_estimate']} 天\n")

        for i, course in enumerate(path['courses'], 1):
            print(f"{i}. {course['title']}")
            print(f"   ⏱️  {course['duration']}")
            print(f"   📖 对应Java: {course['java_equivalent']}")
            if course['prerequisites']:
                print(f"   🔗 先修: {', '.join(course['prerequisites'])}")
            print()


if __name__ == "__main__":
    generator = LearningPathGenerator()

    # Java开发者 → 全栈目标
    path = generator.generate_path(
        background="java_only",
        goal="full_stack",
        time_per_day=2
    )
    generator.print_path(path)
