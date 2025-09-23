# Voice Agent

> This is experimental functionality in AgentScope.

This example demonstrates how to create a voice agent using AgentScope with Qwen-Omni model, featuring both text and audio output capabilities.

> **Note**:
>  - Qwen-Omni may not generate tool calls when the audio output is enabled.
>  - This example supports DashScope `Qwen-Omni` and OpenAI `GPT-4o Audio` models. You can change model by modifying the `model` parameter in `main.py`.
>  - We haven't tested vLLM yet. Contributions are welcome!

## Quick Start

Ensure you have installed agentscope and set ``DASHSCOPE_API_KEY`` in your environment variables.

Run the following commands to set up and run the example:

```bash
python main.py
```
