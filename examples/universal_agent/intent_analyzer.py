# -*- coding: utf-8 -*-
"""Intent analyzer for UniversalAgent."""

import re
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from pydantic import BaseModel


class TaskType(str, Enum):
    """Task types for intent analysis."""
    CONVERSATION = "conversation"
    CODING = "coding"
    ANALYSIS = "analysis"
    RESEARCH = "research"
    FILE_OPERATION = "file_operation"
    MULTIMODAL = "multimodal"
    PLANNING = "planning"
    CALCULATION = "calculation"
    WEB_SEARCH = "web_search"
    DATA_PROCESSING = "data_processing"


class ComplexityLevel(str, Enum):
    """Complexity levels."""
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class IntentResult(BaseModel):
    """Intent analysis result."""
    task_type: TaskType
    complexity: ComplexityLevel
    required_tools: List[str]
    requires_knowledge: bool
    requires_planning: bool
    modality: str
    confidence: float
    estimated_time: int
    keywords: List[str]


class IntentAnalyzer:
    """Analyzes user messages to determine intent and requirements."""
    
    def __init__(self):
        self.task_keywords = {
            TaskType.CODING: [
                "code", "python", "javascript", "java", "cpp", "programming",
                "script", "function", "class", "algorithm", "debug", "implement",
                "write code", "create function", "develop", "编程", "代码"
            ],
            TaskType.ANALYSIS: [
                "analyze", "analysis", "examine", "review", "evaluate", "assess",
                "study", "investigate", "inspect", "check", "分析", "检查"
            ],
            TaskType.RESEARCH: [
                "research", "search", "find", "look up", "investigate", "explore",
                "study", "learn", "信息检索", "搜索", "研究"
            ],
            TaskType.FILE_OPERATION: [
                "file", "read", "write", "create", "delete", "open", "save",
                "edit", "modify", "文件", "读取", "写入", "创建"
            ],
            TaskType.MULTIMODAL: [
                "image", "picture", "photo", "audio", "video", "visual", "speech",
                "voice", "sound", "图像", "图片", "音频", "视频", "语音"
            ],
            TaskType.PLANNING: [
                "plan", "organize", "schedule", "design", "architect", "strategy",
                "roadmap", "outline", "structure", "计划", "规划", "设计"
            ],
            TaskType.CALCULATION: [
                "calculate", "compute", "math", "add", "subtract", "multiply",
                "divide", "formula", "计算", "数学", "公式"
            ],
            TaskType.WEB_SEARCH: [
                "web", "internet", "online", "website", "browse", "网上", "网站"
            ],
            TaskType.DATA_PROCESSING: [
                "data", "process", "transform", "convert", "parse", "filter",
                "aggregate", "statistics", "数据", "处理", "转换"
            ]
        }
        
        self.tool_keywords = {
            "python": ["python", "py"],
            "shell": ["shell", "command", "terminal", "bash", "cmd"],
            "file_ops": ["file", "read", "write", "edit", "create"],
            "web_search": ["search", "find", "lookup", "web", "internet"],
            "image_generation": ["generate", "create", "make", "draw", "image", "picture"],
            "image_analysis": ["analyze", "understand", "describe", "image", "picture"],
            "tts": ["speak", "voice", "audio", "speech", "say"],
            "stt": ["transcribe", "convert", "speech", "audio", "voice"],
            "calculator": ["calculate", "compute", "math", "formula"]
        }
        
        self.complexity_indicators = {
            ComplexityLevel.SIMPLE: [
                "simple", "basic", "quick", "easy", "直接", "简单", "快速"
            ],
            ComplexityLevel.MEDIUM: [
                "moderate", "several", "multiple", "步骤", "中等", "几个"
            ],
            ComplexityLevel.COMPLEX: [
                "complex", "detailed", "comprehensive", "advanced", "system",
                "architecture", "complex", "复杂", "详细", "系统", "架构"
            ]
        }
    
    async def analyze(self, message: str, context: Optional[Dict] = None) -> IntentResult:
        """Analyze the message to determine intent."""
        message_lower = message.lower()
        
        # Determine task type
        task_type, confidence = self._determine_task_type(message_lower)
        
        # Determine complexity
        complexity = self._determine_complexity(message_lower)
        
        # Determine required tools
        required_tools = self._determine_required_tools(message_lower, task_type)
        
        # Determine modality
        modality = self._determine_modality(message_lower, task_type)
        
        # Determine if knowledge is needed
        requires_knowledge = self._requires_knowledge(task_type, message_lower)
        
        # Determine if planning is needed
        requires_planning = self._requires_planning(task_type, complexity, message_lower)
        
        # Extract keywords
        keywords = self._extract_keywords(message_lower)
        
        # Estimate time
        estimated_time = self._estimate_time(task_type, complexity)
        
        return IntentResult(
            task_type=task_type,
            complexity=complexity,
            required_tools=required_tools,
            requires_knowledge=requires_knowledge,
            requires_planning=requires_planning,
            modality=modality,
            confidence=confidence,
            estimated_time=estimated_time,
            keywords=keywords
        )
    
    def _determine_task_type(self, message: str) -> Tuple[TaskType, float]:
        """Determine the primary task type."""
        scores = {}
        
        for task_type, keywords in self.task_keywords.items():
            score = 0
            for keyword in keywords:
                if keyword in message:
                    score += 1
            scores[task_type] = score
        
        if not any(scores.values()):
            return TaskType.CONVERSATION, 0.5
        
        # Get the task type with highest score
        best_task = max(scores.items(), key=lambda x: x[1])
        confidence = min(best_task[1] / 3.0, 1.0)  # Normalize to 0-1
        
        return best_task[0], confidence
    
    def _determine_complexity(self, message: str) -> ComplexityLevel:
        """Determine the complexity level."""
        complexity_scores = {
            ComplexityLevel.SIMPLE: 0,
            ComplexityLevel.MEDIUM: 0,
            ComplexityLevel.COMPLEX: 0
        }
        
        for complexity, keywords in self.complexity_indicators.items():
            for keyword in keywords:
                if keyword in message:
                    complexity_scores[complexity] += 1
        
        # Count complexity indicators
        step_count = len(re.findall(r'\b(step|步骤|阶段|部分|部分)\b', message))
        if step_count > 5:
            complexity_scores[ComplexityLevel.COMPLEX] += 2
        elif step_count > 2:
            complexity_scores[ComplexityLevel.MEDIUM] += 1
        
        # Sentence length as complexity indicator
        sentence_count = len(re.findall(r'[.!?。！？]', message))
        word_count = len(message.split())
        if word_count > 100 or sentence_count > 5:
            complexity_scores[ComplexityLevel.COMPLEX] += 1
        elif word_count > 50 or sentence_count > 3:
            complexity_scores[ComplexityLevel.MEDIUM] += 1
        
        # Return complexity with highest score
        best_complexity = max(complexity_scores.items(), key=lambda x: x[1])
        return best_complexity[0]
    
    def _determine_required_tools(self, message: str, task_type: TaskType) -> List[str]:
        """Determine required tools based on message and task type."""
        required_tools = []
        
        # Check for tool-specific keywords
        for tool, keywords in self.tool_keywords.items():
            for keyword in keywords:
                if keyword in message:
                    required_tools.append(tool)
                    break
        
        # Add task-specific tools
        if task_type == TaskType.CODING:
            if "python" not in required_tools:
                required_tools.append("python")
            if "shell" not in required_tools:
                required_tools.append("shell")
        elif task_type == TaskType.FILE_OPERATION:
            if "file_ops" not in required_tools:
                required_tools.append("file_ops")
        elif task_type == TaskType.RESEARCH:
            if "web_search" not in required_tools:
                required_tools.append("web_search")
        elif task_type == TaskType.MULTIMODAL:
            if "image" in message or "picture" in message:
                required_tools.append("image_analysis")
                if "generate" in message or "create" in message:
                    required_tools.append("image_generation")
            if "audio" in message or "voice" in message or "speech" in message:
                if "speak" in message or "say" in message:
                    required_tools.append("tts")
                else:
                    required_tools.append("stt")
        elif task_type == TaskType.CALCULATION:
            if "calculator" not in required_tools:
                required_tools.append("calculator")
        
        return list(set(required_tools))  # Remove duplicates
    
    def _determine_modality(self, message: str, task_type: TaskType) -> str:
        """Determine the primary modality."""
        modality_keywords = {
            "text": ["text", "write", "read", "文字", "文本"],
            "image": ["image", "picture", "photo", "visual", "图像", "图片"],
            "audio": ["audio", "voice", "speech", "sound", "音频", "语音"],
            "video": ["video", "movie", "视频"],
            "multimodal": ["multimodal", "多模态", "综合"]
        }
        
        modality_scores = {}
        for modality, keywords in modality_keywords.items():
            score = sum(1 for keyword in keywords if keyword in message)
            modality_scores[modality] = score
        
        # Check task type default modality
        task_modality_defaults = {
            TaskType.MULTIMODAL: "multimodal",
            TaskType.CODING: "text",
            TaskType.CONVERSATION: "text",
            TaskType.ANALYSIS: "text",
            TaskType.RESEARCH: "text",
            TaskType.FILE_OPERATION: "text",
            TaskType.PLANNING: "text",
            TaskType.CALCULATION: "text",
            TaskType.WEB_SEARCH: "text",
            TaskType.DATA_PROCESSING: "text"
        }
        
        default_modality = task_modality_defaults.get(task_type, "text")
        
        # If no modality keywords found, use task default
        if not any(modality_scores.values()):
            return default_modality
        
        # Return modality with highest score
        best_modality = max(modality_scores.items(), key=lambda x: x[1])
        return best_modality[0]
    
    def _requires_knowledge(self, task_type: TaskType, message: str) -> bool:
        """Determine if knowledge retrieval is needed."""
        knowledge_keywords = [
            "research", "study", "learn", "information", "data", "facts",
            "statistics", "history", "background", "研究", "学习", "信息", "数据"
        ]
        
        # Certain task types typically require knowledge
        knowledge_requiring_tasks = {
            TaskType.RESEARCH,
            TaskType.ANALYSIS,
            TaskType.DATA_PROCESSING
        }
        
        if task_type in knowledge_requiring_tasks:
            return True
        
        # Check for knowledge keywords
        return any(keyword in message for keyword in knowledge_keywords)
    
    def _requires_planning(self, task_type: TaskType, complexity: ComplexityLevel, message: str) -> bool:
        """Determine if planning is needed."""
        # Complex tasks typically need planning
        if complexity == ComplexityLevel.COMPLEX:
            return True
        
        # Certain task types often need planning
        planning_requiring_tasks = {
            TaskType.PLANNING,
            TaskType.CODING,
            TaskType.RESEARCH
        }
        
        if task_type in planning_requiring_tasks and complexity in [ComplexityLevel.MEDIUM, ComplexityLevel.COMPLEX]:
            return True
        
        # Check for planning keywords
        planning_keywords = [
            "plan", "design", "architecture", "structure", "steps", "process",
            "workflow", "system", "计划", "设计", "架构", "步骤", "流程"
        ]
        
        return any(keyword in message for keyword in planning_keywords)
    
    def _extract_keywords(self, message: str) -> List[str]:
        """Extract important keywords from the message."""
        # Simple keyword extraction - can be enhanced with NLP
        all_keywords = []
        for keywords in self.task_keywords.values():
            all_keywords.extend(keywords)
        
        found_keywords = []
        for keyword in all_keywords:
            if keyword in message:
                found_keywords.append(keyword)
        
        return list(set(found_keywords))
    
    def _estimate_time(self, task_type: TaskType, complexity: ComplexityLevel) -> int:
        """Estimate execution time in seconds."""
        base_times = {
            TaskType.CONVERSATION: 10,
            TaskType.CODING: 30,
            TaskType.ANALYSIS: 45,
            TaskType.RESEARCH: 60,
            TaskType.FILE_OPERATION: 15,
            TaskType.MULTIMODAL: 40,
            TaskType.PLANNING: 50,
            TaskType.CALCULATION: 5,
            TaskType.WEB_SEARCH: 30,
            TaskType.DATA_PROCESSING: 35
        }
        
        complexity_multipliers = {
            ComplexityLevel.SIMPLE: 0.5,
            ComplexityLevel.MEDIUM: 1.0,
            ComplexityLevel.COMPLEX: 2.0
        }
        
        base_time = base_times.get(task_type, 30)
        multiplier = complexity_multipliers.get(complexity, 1.0)
        
        return int(base_time * multiplier)