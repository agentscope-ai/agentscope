# Connect AlibabaCloud API MCP Server Example

## What This Example Demonstrates

This use case shows how to use OAuth login in agentscope to connect to the Alibaba Cloud API MCP server.

Alibaba Cloud is a world-leading cloud computing and artificial intelligence technology company, committed to providing one-stop cloud computing services and middleware for enterprises and developers.

Alibaba Cloud API MCP Server provides MCP-based access to nearly all of Alibaba Cloud's OpenAPIs. You can create and optimize them without coding at <https://api.aliyun.com/mcp>.

For example, you can add the ECS service's price query interfaces DescribePrice and CreateInstance, DescribeImages to a custom MCP service. This allows you to obtain a remote MCP address without any code configuration. Using the agent scope, you can query prices and place orders from the agent.In addition to supporting atomic OpenAPI, it also supports encapsulating Terraform HCL as a remote tool to achieve deterministic orchestration.

After adding the sample MCP, you can use queries similar to the following:
1. Find the lowest-priced ECS instance in the Hangzhou region;
2. Create an instance with the lowest price and lowest specifications in Hangzhou.


## Prerequisites

- Python 3.10 or higher
- Python package asyncio, webbrowser
- Node.js and npm (for the MCP server)
- AlibabaCloud API MCP Server connect address [Alibaba Cloud API MCP Server console](https://api.aliyun.com/mcp)

## How to Run This Example

**Edit main.py**

```python
# openai base
# read from .env
load_dotenv()

server_url = "https://openapi-mcp.cn-hangzhou.aliyuncs.com/accounts/14******/custom/****/id/KXy******/mcp"
```


You need to create your own MCP SERVER from https://api.aliyun.com/mcp and replace the link here. Please choose an address that uses the streamable HTTP protocol.


**Run the script**:
```bash
python main.py
```

## Video example

<https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250911/otcfsk/AgentScope+%E9%9B%86%E6%88%90+OpenAPI+MCP+Server%28%E8%87%AA%E7%84%B6%E8%AF%AD%E8%A8%80%E5%88%9B%E5%BB%BA+ECS%29.mp4>

This video demonstrates how to complete the configuration in the agent scope using the Alibaba Cloud API MCP SERVER service. After logging in through OAuth, users can create an ECS instance using natural language.