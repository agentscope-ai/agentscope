# 🗣️ Voice Agent Demo (Jarvis)

This is a voice-enabled agent that listens to your microphone and replies with synthesized speech.

## Features
- **ASR (Automatic Speech Recognition)**: Uses Google Speech Recognition (via `speech_recognition`).
- **TTS (Text-to-Speech)**: Uses Microsoft Edge TTS (via `edge_tts`) for high-quality natural voices.
- **LLM**: Powered by DeepSeek V3 (or compatible OpenAI models).

## Prerequisites

1. **Install Dependencies**:
   ```bash
   pip install SpeechRecognition edge-tts pygame pyaudio
   ```
   *(Note: `pyaudio` might require system-level dependencies like `portaudio` on Linux/Mac)*

2. **API Key**:
   You need a valid DeepSeek API Key with sufficient balance.
   
   **Option A (Environment Variable)**:
   ```bash
   export DEEPSEEK_API_KEY="sk-..."
   # On Windows PowerShell:
   # $env:DEEPSEEK_API_KEY="sk-..."
   ```
   
   **Option B (Edit File)**:
   Open `main.py` and modify `API_KEY` directly.

## Running

```bash
python main.py
```

1. Wait for `[System] Listening...`
2. Speak clearly into your microphone.
3. The agent will reply elegantly.

## Troubleshooting

- **Error 402 (Insufficient Balance)**: Your API key is out of credits. Please recharge your DeepSeek account or switch to a different provider.
- **Could not understand audio**: 
    - Check if your microphone is set as the system default.
    - Speak closer to the mic.
    - Check `speech.py` to switch languages (`language="zh-CN"` for Chinese).
