# Multi-Agent Realtime Voice Interaction Example

This example demonstrates how to use AgentScope's `ChatRoom` class to create a multi-agent real-time voice interaction system where two AI agents can have autonomous conversations without user input.

## Features

- 🗣️ **Real-time Voice Interaction**: Two agents communicate through voice in real-time
- 🤖 **Autonomous Conversation**: Agents converse with each other without user intervention
- ⚙️ **Customizable Configuration**: Configure agent names and instructions through the web interface
- 🎨 **Modern UI**: Clean, shadcn-inspired interface for easy interaction
- 📊 **Live Transcript**: See the conversation transcripts in real-time

## Architecture

The example uses:
- **Backend**: FastAPI server with WebSocket support
- **Frontend**: HTML5 with Web Audio API for audio playback
- **AgentScope Components**:
  - `ChatRoom`: Manages multiple `RealtimeAgent` instances
  - `RealtimeAgent`: Handles real-time voice interaction with AI models
  - `DashScopeRealtimeModel`: DashScope's Qwen3-Omni realtime model

## Prerequisites

1. **Python Dependencies**:
   ```bash
   pip install agentscope[dashscope]
   pip install fastapi uvicorn
   ```

2. **DashScope API Key**:
   - Set your DashScope API key as an environment variable:
     ```bash
     export DASHSCOPE_API_KEY="your-api-key-here"
     ```

## Usage

1. **Start the Server**:
   ```bash
   python run_server.py
   ```

2. **Open the Web Interface**:
   - Navigate to `http://localhost:8000` in your web browser

3. **Configure Agents**:
   - Set names and instructions for both Agent 1 and Agent 2
   - Example configurations:
     - **Agent 1 (Alice)**: "You are Alice, a cheerful and optimistic person who loves to share stories and ask questions. Keep your responses brief and conversational."
     - **Agent 2 (Bob)**: "You are Bob, a thoughtful and analytical person who enjoys deep conversations. Keep your responses brief and conversational."

4. **Start the Conversation**:
   - Click the "▶️ Start Conversation" button
   - The agents will begin conversing autonomously
   - You'll see transcripts and system messages in the message panel
   - Audio playback will stream in real-time

5. **Stop the Conversation**:
   - Click the "⏹️ Stop Conversation" button when you want to end the session

## How It Works

### Backend Flow

1. **WebSocket Connection**: Client connects via WebSocket to `/ws/{user_id}/{session_id}`
2. **Session Creation**:
   - Client sends `client_session_create` event with agent configurations
   - Server creates two `RealtimeAgent` instances with specified names and instructions
   - Server creates a `ChatRoom` with both agents
   - Server starts the chat room and returns `session_created` event
3. **Message Broadcasting**:
   - `ChatRoom` automatically broadcasts messages between agents
   - All events (audio, transcripts, etc.) are forwarded to the frontend
4. **Session End**: Client sends `client_session_end` event to stop the conversation

### Frontend Flow

1. **WebSocket Setup**: Establishes connection and waits for server events
2. **Session Management**: Sends configuration and manages conversation state
3. **Audio Playback**:
   - Receives base64-encoded PCM16 audio chunks
   - Decodes and queues audio data
   - Uses Web Audio API `ScriptProcessorNode` for streaming playback at 24kHz
4. **Transcript Display**: Shows real-time transcripts from both agents

## Key Components

### ChatRoom

The `ChatRoom` class manages multiple `RealtimeAgent` instances:
- Establishes connections for all agents
- Broadcasts messages between agents automatically
- Forwards events to the frontend
- Handles lifecycle management (start/stop)

### RealtimeAgent

Each `RealtimeAgent`:
- Connects to the DashScope realtime API
- Processes audio input from other agents
- Generates voice responses
- Emits events for transcripts, audio, and status updates

## Customization

### Changing the Model

To use a different model, modify the `DashScopeRealtimeModel` configuration in `run_server.py`:

```python
model=DashScopeRealtimeModel(
    model_name="your-model-name",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
)
```

### Adding More Agents

To add more agents, modify the agent creation section in `run_server.py`:

```python
agent3 = RealtimeAgent(
    name=agent3_name,
    sys_prompt=agent3_instructions,
    model=DashScopeRealtimeModel(
        model_name="qwen3-omni-flash-realtime",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    ),
)

chat_room = ChatRoom(agents=[agent1, agent2, agent3])
```

And update the frontend to include configuration fields for the additional agents.

## Troubleshooting

### No Audio Playback
- Ensure your browser supports Web Audio API
- Check browser console for audio-related errors
- Verify the audio format matches the expected PCM16 at 24kHz

### Connection Issues
- Verify your DashScope API key is set correctly
- Check that port 8000 is not blocked by firewall
- Review server logs for error messages

### Agents Not Responding
- Ensure both agent configurations have valid instructions
- Check that the instructions encourage conversational behavior
- Review the console logs for API errors

## References

- [AgentScope Documentation](https://modelscope.github.io/agentscope/)
- [DashScope API Documentation](https://help.aliyun.com/zh/model-studio/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API)

