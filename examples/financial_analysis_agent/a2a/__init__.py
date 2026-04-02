"""
A2A (Agent-to-Agent) 通信模块

实现智能体之间的协作通信，支持财务分析工作流中的多智能体协作
"""

import asyncio
import json
import uuid
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime
from agentscope.message import Msg
from agentscope.agent import AgentBase
from agentscope.models import ModelResponse


class A2AMessage:
    """
    A2A消息类
    
    定义智能体之间的消息格式和元数据
    """

    def __init__(
        self,
        sender: str,
        receiver: str,
        message_type: str,
        content: Any,
        message_id: str = None,
        timestamp: str = None,
        metadata: Dict[str, Any] = None
    ):
        """
        初始化A2A消息
        
        Args:
            sender: 发送者ID
            receiver: 接收者ID
            message_type: 消息类型
            content: 消息内容
            message_id: 消息ID
            timestamp: 时间戳
            metadata: 元数据
        """
        self.sender = sender
        self.receiver = receiver
        self.message_type = message_type
        self.content = content
        self.message_id = message_id or str(uuid.uuid4())
        self.timestamp = timestamp or datetime.now().isoformat()
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            "message_id": self.message_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "message_type": self.message_type,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'A2AMessage':
        """从字典创建A2A消息"""
        return cls(
            sender=data["sender"],
            receiver=data["receiver"],
            message_type=data["message_type"],
            content=data["content"],
            message_id=data.get("message_id"),
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {})
        )


class A2ARegistry:
    """
    A2A注册表
    
    管理智能体的注册和发现
    """

    def __init__(self):
        """初始化注册表"""
        self.agents = {}
        self.capabilities = {}
        self.groups = {}

    def register_agent(
        self,
        agent_id: str,
        agent: AgentBase,
        capabilities: List[str] = None,
        group: str = None
    ):
        """
        注册智能体
        
        Args:
            agent_id: 智能体ID
            agent: 智能体实例
            capabilities: 能力列表
            group: 智能体组
        """
        self.agents[agent_id] = agent
        
        if capabilities:
            self.capabilities[agent_id] = capabilities
        
        if group:
            if group not in self.groups:
                self.groups[group] = []
            self.groups[group].append(agent_id)

    def unregister_agent(self, agent_id: str):
        """注销智能体"""
        self.agents.pop(agent_id, None)
        self.capabilities.pop(agent_id, None)
        
        for group_agents in self.groups.values():
            if agent_id in group_agents:
                group_agents.remove(agent_id)

    def get_agent(self, agent_id: str) -> Optional[AgentBase]:
        """获取智能体实例"""
        return self.agents.get(agent_id)

    def find_agents_by_capability(self, capability: str) -> List[str]:
        """根据能力查找智能体"""
        agents_with_capability = []
        for agent_id, capabilities in self.capabilities.items():
            if capability in capabilities:
                agents_with_capability.append(agent_id)
        return agents_with_capability

    def get_group_agents(self, group: str) -> List[str]:
        """获取组内智能体"""
        return self.groups.get(group, [])

    def list_agents(self) -> Dict[str, Dict[str, Any]]:
        """列出所有智能体"""
        result = {}
        for agent_id, agent in self.agents.items():
            result[agent_id] = {
                "name": getattr(agent, 'name', agent_id),
                "capabilities": self.capabilities.get(agent_id, []),
                "type": type(agent).__name__
            }
        return result


class A2ACommunicationManager:
    """
    A2A通信管理器
    
    处理智能体之间的消息传递和路由
    """

    def __init__(self, registry: A2ARegistry = None):
        """
        初始化通信管理器
        
        Args:
            registry: A2A注册表
        """
        self.registry = registry or A2ARegistry()
        self.message_handlers = {}
        self.message_history = []
        self.routing_rules = {}

    def register_handler(
        self,
        message_type: str,
        handler: Callable[[A2AMessage], Any]
    ):
        """
        注册消息处理器
        
        Args:
            message_type: 消息类型
            handler: 处理器函数
        """
        self.message_handlers[message_type] = handler

    def add_routing_rule(
        self,
        rule_name: str,
        condition: Callable[[A2AMessage], bool],
        target: str
    ):
        """
        添加路由规则
        
        Args:
            rule_name: 规则名称
            condition: 条件函数
            target: 目标智能体ID
        """
        self.routing_rules[rule_name] = {
            "condition": condition,
            "target": target
        }

    async def send_message(self, message: A2AMessage) -> bool:
        """
        发送消息
        
        Args:
            message: A2A消息
            
        Returns:
            是否发送成功
        """
        try:
            # 记录消息历史
            self.message_history.append(message.to_dict())
            
            # 获取接收者智能体
            receiver_agent = self.registry.get_agent(message.receiver)
            if not receiver_agent:
                print(f"未找到接收者智能体: {message.receiver}")
                return False
            
            # 转换为AgentScope消息格式
            scope_msg = Msg(
                name=message.sender,
                content=json.dumps({
                    "a2a_message": message.to_dict()
                }, ensure_ascii=False),
                role="assistant"
            )
            
            # 发送消息
            response = await receiver_agent(scope_msg)
            
            # 记录响应
            response_message = A2AMessage(
                sender=message.receiver,
                receiver=message.sender,
                message_type="response",
                content=response.content if response else "",
                metadata={"original_message_id": message.message_id}
            )
            self.message_history.append(response_message.to_dict())
            
            return True
            
        except Exception as e:
            print(f"发送A2A消息失败: {str(e)}")
            return False

    async def broadcast_to_group(
        self,
        group: str,
        message_type: str,
        content: Any,
        sender: str = "system"
    ) -> List[bool]:
        """
        向组内广播消息
        
        Args:
            group: 组名
            message_type: 消息类型
            content: 消息内容
            sender: 发送者
            
        Returns:
            发送结果列表
        """
        group_agents = self.registry.get_group_agents(group)
        results = []
        
        for agent_id in group_agents:
            if agent_id != sender:  # 不发送给自己
                message = A2AMessage(
                    sender=sender,
                    receiver=agent_id,
                    message_type=message_type,
                    content=content
                )
                result = await self.send_message(message)
                results.append(result)
        
        return results

    def get_message_history(
        self,
        sender: str = None,
        receiver: str = None,
        message_type: str = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        获取消息历史
        
        Args:
            sender: 发送者过滤
            receiver: 接收者过滤
            message_type: 消息类型过滤
            limit: 限制数量
            
        Returns:
            消息历史列表
        """
        filtered = self.message_history
        
        if sender:
            filtered = [msg for msg in filtered if msg["sender"] == sender]
        if receiver:
            filtered = [msg for msg in filtered if msg["receiver"] == receiver]
        if message_type:
            filtered = [msg for msg in filtered if msg["message_type"] == message_type]
        
        return filtered[-limit:]


class FinancialAnalysisWorkflow:
    """
    财务分析工作流
    
    使用A2A通信协调多个智能体完成财务分析任务
    """

    def __init__(self, communication_manager: A2ACommunicationManager = None):
        """
        初始化工作流
        
        Args:
            communication_manager: 通信管理器
        """
        self.comm_manager = communication_manager or A2ACommunicationManager()
        self.workflow_steps = []
        self.current_step = 0

    async def execute_analysis_workflow(
        self,
        task_data: Dict[str, Any],
        coordinator_id: str = "financial_coordinator"
    ) -> Dict[str, Any]:
        """
        执行财务分析工作流
        
        Args:
            task_data: 任务数据
            coordinator_id: 协调者ID
            
        Returns:
            工作流执行结果
        """
        try:
            # 初始化工作流
            await self._initialize_workflow(task_data, coordinator_id)
            
            # 执行工作流步骤
            results = {}
            
            for step in self.workflow_steps:
                step_result = await self._execute_step(step, task_data)
                results[step["name"]] = step_result
                
                # 检查是否需要中止
                if not step_result.get("success", False):
                    break
            
            # 生成最终报告
            final_report = await self._generate_final_report(results, task_data)
            
            return {
                "success": True,
                "workflow_id": task_data.get("workflow_id", str(uuid.uuid4())),
                "steps": results,
                "final_report": final_report,
                "completed_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "completed_at": datetime.now().isoformat()
            }

    async def _initialize_workflow(self, task_data: Dict[str, Any], coordinator_id: str):
        """初始化工作流"""
        company_symbol = task_data.get("company_symbol")
        analysis_types = task_data.get("analysis_types", ["profitability", "solvency", "efficiency"])
        
        # 定义工作流步骤
        self.workflow_steps = [
            {
                "name": "data_collection",
                "type": "collect",
                "agent": "data_collector",
                "description": "收集财务数据",
                "parameters": {"symbol": company_symbol}
            },
            {
                "name": "profitability_analysis",
                "type": "analyze",
                "agent": "profitability_analyst",
                "description": "盈利能力分析",
                "parameters": {"analysis_type": "profitability"}
            },
            {
                "name": "solvency_analysis",
                "type": "analyze",
                "agent": "solvency_analyst", 
                "description": "偿债能力分析",
                "parameters": {"analysis_type": "solvency"}
            },
            {
                "name": "risk_assessment",
                "type": "assess",
                "agent": "risk_assessor",
                "description": "风险评估",
                "parameters": {"assessment_type": "financial"}
            },
            {
                "name": "report_generation",
                "type": "generate",
                "agent": "report_generator",
                "description": "生成分析报告",
                "parameters": {"format": "comprehensive"}
            }
        ]

    async def _execute_step(self, step: Dict[str, Any], task_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行工作流步骤"""
        step_name = step["name"]
        agent_id = step["agent"]
        
        # 发送任务消息
        task_message = A2AMessage(
            sender="workflow_coordinator",
            receiver=agent_id,
            message_type="task",
            content={
                "step_name": step_name,
                "task_data": task_data,
                "step_parameters": step.get("parameters", {})
            }
        )
        
        # 发送并等待响应
        success = await self.comm_manager.send_message(task_message)
        
        if not success:
            return {
                "success": False,
                "error": f"无法发送任务给智能体 {agent_id}",
                "step": step_name
            }
        
        # 等待响应（简化实现，实际应该有更复杂的同步机制）
        await asyncio.sleep(1)
        
        # 获取最近的响应消息
        recent_messages = self.comm_manager.get_message_history(
            sender=agent_id,
            receiver="workflow_coordinator",
            message_type="response",
            limit=5
        )
        
        if recent_messages:
            latest_response = recent_messages[-1]
            try:
                response_content = json.loads(latest_response["content"])
                return {
                    "success": True,
                    "result": response_content,
                    "step": step_name,
                    "agent": agent_id
                }
            except json.JSONDecodeError:
                return {
                    "success": False,
                    "error": "响应格式错误",
                    "step": step_name
                }
        else:
            return {
                "success": False,
                "error": "未收到响应",
                "step": step_name
            }

    async def _generate_final_report(self, results: Dict[str, Any], task_data: Dict[str, Any]) -> str:
        """生成最终报告"""
        report = f"""
# 财务分析报告

## 任务信息
- 公司代码: {task_data.get('company_symbol', 'N/A')}
- 分析类型: {', '.join(task_data.get('analysis_types', []))}
- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 分析结果

"""
        
        for step_name, step_result in results.items():
            if step_result.get("success", False):
                report += f"### {step_name}\n"
                report += f"状态: ✅ 成功\n"
                report += f"执行者: {step_result.get('agent', 'N/A')}\n\n"
            else:
                report += f"### {step_name}\n"
                report += f"状态: ❌ 失败\n"
                report += f"错误: {step_result.get('error', 'Unknown error')}\n\n"
        
        return report


# 全局A2A注册表和通信管理器
_a2a_registry = A2ARegistry()
_a2a_comm_manager = A2ACommunicationManager(_a2a_registry)


def get_a2a_registry() -> A2ARegistry:
    """获取全局A2A注册表"""
    return _a2a_registry


def get_a2a_communication_manager() -> A2ACommunicationManager:
    """获取全局A2A通信管理器"""
    return _a2a_comm_manager