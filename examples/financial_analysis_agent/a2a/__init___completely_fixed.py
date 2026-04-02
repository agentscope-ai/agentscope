"""
A2A (Agent-to-Agent) 通信模块 - 完全修复版本

实现智能体之间的协作通信，支持财务分析工作流中的多智能体协作
所有管理类都正确继承自AgentBase基类
"""

import asyncio
import json
import uuid
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime

try:
    from agentscope.message import Msg
    from agentscope.agent import AgentBase
    # 检查真实的AgentBase构造函数
    import inspect
    print(f"AgentBase signature: {inspect.signature(AgentBase.__init__)}")
except ImportError:
    # Fallback for local development
    class Msg:
        def __init__(self, name, content, role="assistant"):
            self.name = name
            self.content = content
            self.role = role
    
    class AgentBase:
        def __init__(self, **kwargs):
            self.name = kwargs.get('name', '')
            self.sys_prompt = kwargs.get('sys_prompt', '')
        
        def reply(self, message: Any) -> Any:
            return f"Response from {self.name}: {message}"


class A2AMessage:
    """
    A2A消息类 - 简化版本，避免嵌套JSON结构
    """

    def __init__(
        self,
        sender: str,
        receiver: str,
        message_type: str,
        content: Any,
        message_id: Optional[str] = None,
        timestamp: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
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

    def to_msg(self) -> Msg:
        """转换为AgentScope消息格式"""
        return Msg(
            name=self.sender,
            content=f"[{self.message_type}] {self.content}",
            role="assistant"
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "A2AMessage":
        """从字典创建消息"""
        return cls(
            sender=data.get("sender", "unknown"),
            receiver=data.get("receiver", "unknown"),
            message_type=data.get("message_type", "text"),
            content=data.get("content", ""),
            message_id=data.get("message_id"),
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {})
        )


class A2ARegistry(AgentBase):
    """
    A2A智能体注册表 - 继承自AgentBase
    """

    def __init__(self, name: str = "A2ARegistry", **kwargs):
        super().__init__()  # AgentBase不接受参数
        # 手动设置属性
        self.name = name
        self.sys_prompt = """你是A2A智能体注册表，负责：
1. 管理智能体注册和注销
2. 维护智能体能力目录
3. 支持智能体发现和匹配
4. 提供智能体组管理功能"""
        
        self.agents: Dict[str, AgentBase] = {}
        self.capabilities: Dict[str, List[str]] = {}
        self.groups: Dict[str, List[str]] = {}

    def register_agent(
        self,
        agent_id: str,
        agent: AgentBase,
        capabilities: Optional[List[str]] = None,
        group: Optional[str] = None
    ):
        """注册智能体"""
        self.agents[agent_id] = agent
        
        if capabilities:
            self.capabilities[agent_id] = capabilities
        
        if group:
            if group not in self.groups:
                self.groups[group] = []
            self.groups[group].append(agent_id)
        
        print(f"智能体 {agent_id} 注册成功，能力: {capabilities}")

    def unregister_agent(self, agent_id: str) -> bool:
        """注销智能体"""
        if agent_id in self.agents:
            # 从能力记录中移除
            if agent_id in self.capabilities:
                del self.capabilities[agent_id]
            
            # 从组中移除
            for group, agents_in_group in self.groups.items():
                if agent_id in agents_in_group:
                    agents_in_group.remove(agent_id)
            
            # 从注册表移除
            del self.agents[agent_id]
            
            print(f"智能体 {agent_id} 注销成功")
            return True
        
        return False

    def find_agents_by_capability(self, capability: str) -> List[str]:
        """根据能力查找智能体"""
        return [
            agent_id 
            for agent_id, capabilities in self.capabilities.items()
            if capability in capabilities
        ]

    def get_agent(self, agent_id: str) -> Optional[AgentBase]:
        """获取智能体实例"""
        return self.agents.get(agent_id)

    def get_agents_in_group(self, group: str) -> List[str]:
        """获取组内智能体"""
        return self.groups.get(group, [])

    def list_all_agents(self) -> List[str]:
        """列出所有智能体"""
        return list(self.agents.keys())

    def get_agent_capabilities(self, agent_id: str) -> List[str]:
        """获取智能体能力"""
        return self.capabilities.get(agent_id, [])


class A2ACommunicationManager(AgentBase):
    """
    A2A通信管理器 - 继承自AgentBase
    """

    def __init__(self, registry: Optional[A2ARegistry] = None, name: str = "A2ACommunicationManager", **kwargs):
        super().__init__()  # AgentBase不接受参数
        # 手动设置属性
        self.name = name
        self.sys_prompt = """你是A2A通信管理器，负责：
1. 智能体间消息路由和传递
2. 消息处理和分发
3. 通信规则和策略管理
4. 消息历史记录和追踪"""
        
        self.registry = registry or A2ARegistry()
        self.message_handlers: Dict[str, Callable] = {}
        self.message_history: List[A2AMessage] = []
        self.routing_rules: Dict[str, Dict[str, Any]] = {}

    def register_handler(self, message_type: str, handler: Callable[[A2AMessage], Any]):
        """注册消息处理器"""
        self.message_handlers[message_type] = handler

    def add_routing_rule(self, rule_name: str, condition: Callable[[A2AMessage], bool], target: str):
        """添加路由规则"""
        self.routing_rules[rule_name] = {
            "condition": condition,
            "target": target
        }

    async def send_message(self, message: A2AMessage) -> bool:
        """发送消息"""
        try:
            # 记录消息历史
            self.message_history.append(message)
            
            # 检查路由规则
            for rule_name, rule in self.routing_rules.items():
                if rule["condition"](message):
                    message.receiver = rule["target"]
                    break
            
            # 获取接收者智能体
            receiver_agent = self.registry.get_agent(message.receiver)
            if not receiver_agent:
                print(f"接收者 {message.receiver} 不存在")
                return False
            
            # 转换为AgentScope消息格式
            as_msg = message.to_msg()
            
            # 使用AgentBase的reply方法处理消息
            response = receiver_agent.reply(as_msg.content)
            
            print(f"消息从 {message.sender} 发送到 {message.receiver}: {message.content}")
            print(f"响应: {response}")
            
            return True
            
        except Exception as e:
            print(f"发送消息失败: {e}")
            return False

    async def broadcast_message(self, sender: str, message_type: str, content: Any, group: Optional[str] = None) -> int:
        """广播消息"""
        if group:
            recipients = self.registry.get_agents_in_group(group)
        else:
            recipients = self.registry.list_all_agents()
        
        # 移除发送者自己
        recipients = [r for r in recipients if r != sender]
        
        success_count = 0
        for recipient in recipients:
            message = A2AMessage(sender, recipient, message_type, content)
            if await self.send_message(message):
                success_count += 1
        
        return success_count

    def get_message_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取消息历史"""
        return [msg.to_dict() for msg in self.message_history[-limit:]]

    def clear_history(self):
        """清空消息历史"""
        self.message_history.clear()


class FinancialAnalysisWorkflow(AgentBase):
    """
    财务分析工作流 - 继承自AgentBase
    """

    def __init__(self, communication_manager: Optional[A2ACommunicationManager] = None, 
                 name: str = "FinancialAnalysisWorkflow", **kwargs):
        super().__init__()  # AgentBase不接受参数
        # 手动设置属性
        self.name = name
        self.sys_prompt = """你是财务分析工作流，负责：
1. 协调多个智能体完成财务分析任务
2. 管理数据收集、分析和报告生成流程
3. 处理工作流步骤间的依赖关系
4. 监控任务执行质量和进度"""
        
        self.comm_manager = communication_manager or A2ACommunicationManager()
        self.workflow_steps: List[Dict[str, Any]] = []
        self.current_step = 0

    def add_step(self, step_name: str, agent_id: str, task_data: Dict[str, Any], 
                 dependencies: Optional[List[str]] = None):
        """添加工作流步骤"""
        step = {
            "name": step_name,
            "agent_id": agent_id,
            "task_data": task_data,
            "dependencies": dependencies or [],
            "completed": False,
            "result": None
        }
        self.workflow_steps.append(step)
        print(f"添加工作流步骤: {step_name}")

    async def execute_workflow(self, workflow_id: Optional[str] = None) -> Dict[str, Any]:
        """执行工作流"""
        workflow_id = workflow_id or str(uuid.uuid4())
        
        try:
            results = {}
            
            # 按顺序执行步骤（考虑依赖关系）
            for step in self.workflow_steps:
                # 检查依赖是否完成
                dependencies_met = True
                for dep in step["dependencies"]:
                    if dep not in results or not results[dep].get("success", False):
                        dependencies_met = False
                        break
                
                if not dependencies_met:
                    print(f"步骤 {step['name']} 的依赖未满足，跳过")
                    continue
                
                # 执行步骤
                print(f"执行步骤: {step['name']}")
                result = await self._execute_step(step)
                results[step["name"]] = result
                
                if not result.get("success", False):
                    print(f"步骤 {step['name']} 执行失败，中止工作流")
                    break
            
            # 生成工作流报告
            final_report = self._generate_workflow_report(results)
            
            return {
                "success": True,
                "workflow_id": workflow_id,
                "steps": results,
                "report": final_report,
                "completed_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                "success": False,
                "workflow_id": workflow_id,
                "error": str(e),
                "completed_at": datetime.now().isoformat()
            }

    async def _execute_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个工作流步骤"""
        try:
            agent_id = step["agent_id"]
            task_data = step["task_data"]
            
            # 创建任务消息
            message = A2AMessage(
                sender="workflow_engine",
                receiver=agent_id,
                message_type="task",
                content=task_data
            )
            
            # 发送任务
            success = await self.comm_manager.send_message(message)
            
            return {
                "success": success,
                "step_name": step["name"],
                "agent_id": agent_id,
                "result": "任务已发送" if success else "任务发送失败",
                "timestamp": datetime.now().isoformat()
            }
            
        except Exception as e:
            return {
                "success": False,
                "step_name": step["name"],
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def _generate_workflow_report(self, results: Dict[str, Any]) -> str:
        """生成工作流报告"""
        total_steps = len(self.workflow_steps)
        completed_steps = sum(1 for r in results.values() if r.get("success", False))
        
        report = f"""工作流执行报告
==================
总步骤数: {total_steps}
成功步骤: {completed_steps}
成功率: {completed_steps/total_steps*100:.1f}%

步骤详情:
"""
        
        for step_name, result in results.items():
            status = "✅ 成功" if result.get("success", False) else "❌ 失败"
            report += f"- {step_name}: {status}\n"
        
        return report

    def reset_workflow(self):
        """重置工作流"""
        self.workflow_steps.clear()
        self.current_step = 0


# 便利函数
def create_a2a_registry(name: str = "A2ARegistry") -> A2ARegistry:
    """创建A2A注册表"""
    return A2ARegistry(name=name)


def create_a2a_communication_manager(registry: Optional[A2ARegistry] = None) -> A2ACommunicationManager:
    """创建A2A通信管理器"""
    return A2ACommunicationManager(registry=registry)


def create_financial_analysis_workflow(communication_manager: Optional[A2ACommunicationManager] = None) -> FinancialAnalysisWorkflow:
    """创建财务分析工作流"""
    return FinancialAnalysisWorkflow(communication_manager=communication_manager)


# 全局实例（单例模式）
_a2a_registry: Optional[A2ARegistry] = None
_a2a_communication_manager: Optional[A2ACommunicationManager] = None


def get_a2a_registry() -> A2ARegistry:
    """获取全局A2A注册表"""
    global _a2a_registry
    if _a2a_registry is None:
        _a2a_registry = A2ARegistry()
    return _a2a_registry


def get_a2a_communication_manager() -> A2ACommunicationManager:
    """获取全局A2A通信管理器"""
    global _a2a_communication_manager
    if _a2a_communication_manager is None:
        _a2a_communication_manager = A2ACommunicationManager()
    return _a2a_communication_manager


if __name__ == "__main__":
    print("测试A2A通信模块")
    
    # 创建注册表和通信管理器
    registry = A2ARegistry()
    comm_manager = A2ACommunicationManager(registry)
    
    # 创建测试智能体
    test_agent = AgentBase()
    test_agent.name = "test_agent"
    test_agent.sys_prompt = "测试智能体"
    
    # 注册智能体
    registry.register_agent("test_agent", test_agent, ["analysis", "reporting"], "analysts")
    
    # 创建工作流
    workflow = FinancialAnalysisWorkflow(comm_manager)
    
    # 添加步骤
    workflow.add_step("data_collection", "test_agent", {"task": "collect_stock_data", "symbol": "AAPL"})
    workflow.add_step("analysis", "test_agent", {"task": "analyze_data"}, ["data_collection"])
    
    print("A2A模块测试完成")