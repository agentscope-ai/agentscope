# Realtime Voice Agent Example

This example demonstrates how to build a **real-time voice conversation agent** using AgentScope's RealtimeAgent. The agent supports bidirectional voice streaming, enabling natural voice conversations with low latency and real-time audio transcription.

## Prerequisites

- Python 3.10 or higher
- Your DashScope API key in an environment variable `DASHSCOPE_API_KEY`

Install the required packages:

```bash
uv pip install agentscope fastapi uvicorn websockets
# or
# pip install agentscope
```

## Usage

### 1. Start the Server

Run the FastAPI server:

```bash
cd examples/agent/realtime_voice_agent
python run_server.py
```

The server will start on `http://localhost:8000` by default.

### 2. Open the Web Interface

Open your web browser and navigate to:

```
http://localhost:8000
```

You will see a web interface with:
- Configuration panel (instructions and user name)
- Voice control buttons (Start Recording, Stop Recording, Disconnect)
- Text input field
- Message display area

### 3. Start Conversation

1. **Configure the Agent** (optional):
   - Modify the "Instructions" to customize the agent's behavior
   - Enter your name in the "User Name" field

2. **Start Voice Recording**:
   - Click the "🎤 Start Recording" button
   - Allow microphone access when prompted by your browser
   - Speak naturally to the agent
   - The agent will respond with voice and text

3. **Stop Recording**:
   - Click "⏹️ Stop Recording" to pause voice input


## Switching Models

AgentScope supports multiple realtime voice models. By default, this example uses DashScope's `qwen3-omni-flash-realtime` model, but you can easily switch to other providers.

### Supported Models

- **GeminiRealtimeModel**
- **OpenAIRealtimeModel**

### How to Switch Models

Edit `run_server.py` and replace the model initialization code:

**For OpenAI:**

```python
from agentscope.realtime import OpenAIRealtimeModel

agent = RealtimeAgent(
    name="Friday",
    sys_prompt=sys_prompt,
    model=OpenAIRealtimeModel(
        model_name="gpt-4o-realtime-preview",
        api_key=os.getenv("OPENAI_API_KEY"),
        voice="alloy",  # Options: "alloy", "echo", "marin", "cedar"
    ),
)
```

**For Gemini:**

```python
from agentscope.realtime import GeminiRealtimeModel

agent = RealtimeAgent(
    name="Friday",
    sys_prompt=sys_prompt,
    model=GeminiRealtimeModel(
        model_name="gemini-2.5-flash-native-audio-preview-09-2025",
        api_key=os.getenv("GEMINI_API_KEY"),
        voice="Puck",  # Options: "Puck", "Charon", "Kore", "Fenrir"
    ),
)
```

Don't forget to set the corresponding API key environment variable before starting the server!

