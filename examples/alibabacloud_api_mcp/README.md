# Connect AlibabaCloud API MCP Server Example

## What This Example Demonstrates

This use case shows how to use OAuth login in agentscope to connect to the Alibaba Cloud API MCP server.

Alibaba Cloud API MCP Server provides MCP-based access to nearly all of Alibaba Cloud's OpenAPIs. You can create and optimize them without coding at <https://api.aliyun.com/mcp>.

## Prerequisites

- Python 3.10 or higher
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

<video src="https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250911/otcfsk/AgentScope+%E9%9B%86%E6%88%90+OpenAPI+MCP+Server%28%E8%87%AA%E7%84%B6%E8%AF%AD%E8%A8%80%E5%88%9B%E5%BB%BA+ECS%29.mp4" controls width="600">
  https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250911/otcfsk/AgentScope+%E9%9B%86%E6%88%90+OpenAPI+MCP+Server%28%E8%87%AA%E7%84%B6%E8%AF%AD%E8%A8%80%E5%88%9B%E5%BB%BA+ECS%29.mp4
</video>
