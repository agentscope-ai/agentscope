# Browser Agent Example

This example demonstrates how to use AgentScope's BrowserAgent for web automation tasks. The BrowserAgent leverages the Model Context Protocol (MCP) to interact with browser tools powered by Playwright, enabling sophisticated web navigation, data extraction, and automation.


## Prerequisites

- Python 3.10 or higher
- Node.js and npm (for the MCP server)
- DashScope API key from Alibaba Cloud

## Installation

### Install AgentScope

```bash
# Install from source
cd {PATH_TO_AGENTSCOPE}
pip install -e .
```

## Setup

### 1. Environment Configuration

Set up your DashScope API key:

```bash
export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
```

You can obtain a DashScope API key from [Alibaba Cloud DashScope Console](https://dashscope.console.aliyun.com/).

### 2. About PlayWright MCP Server

Before running the browser agent, you can test whether you can start the Playwright MCP server:

```bash
npx @playwright/mcp@latest
```

## Usage

### Basic Example
You can start running the browser agent in your terminal with the following command
```bash
cd examples/agent/browser_agent
python main.py
```
