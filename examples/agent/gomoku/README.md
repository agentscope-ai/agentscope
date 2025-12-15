# ♟️ Gomoku Agent (Desktop)

A standalone **Gomoku (Five-in-a-Row)** game where you play against an AgentScope-powered AI in a native window.

## Features

- **Interactive GUI**: Built with Pygame for a smooth, native experience.
- **AI Opponent**: An intelligent agent (powered by DeepSeek) that analyzes the board and plays strategically.
- **Visuals**: Classic wood-style board with clear stone rendering.

## Prerequisites

- Python 3.10+
- `agentscope`
- `pygame`

```bash
pip install pygame
```

## How to Play

1. **Set API Key** (if not set globally):

   ```powershell
   $env:DASHSCOPE_API_KEY = "your_key_here"
   ```

2. **Run the Game**:

   ```bash
   python main.py
   ```

3. **Controls**:
   - **Click** on an empty intersection to place your Black stone (`●`).
   - Wait for the AI (White `○`) to think and move.
   - First to 5 in a row wins!
