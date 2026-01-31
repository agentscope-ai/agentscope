# -*- coding: utf-8 -*-
"""A test server"""
import asyncio
import os
from dataclasses import asdict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from agentscope.agent import RealtimeAgentBase
from agentscope.realtime import DashScopeRealtimeModel  # GeminiRealtimeModel

# 简单的 HTML 测试页面
html = """
<!DOCTYPE html>
<html>
<head>
    <title>语音交流 Demo</title>
    <meta charset="UTF-8">
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 50px auto;
            padding: 20px;
        }
        #messages {
            border: 1px solid #ccc;
            height: 300px;
            overflow-y: scroll;
            padding: 10px;
            margin: 20px 0;
            background: #f9f9f9;
        }
        input[type="text"] {
            width: 70%;
            padding: 10px;
            margin: 5px;
        }
        button {
            padding: 10px 20px;
            margin: 5px;
            cursor: pointer;
        }
        .message {
            margin: 5px 0;
            padding: 5px;
            background: #fff;
            border-radius: 3px;
            border-left: 3px solid #4CAF50;
        }
        .recording {
            background-color: #ff4444 !important;
            animation: pulse 1s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .controls {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin: 20px 0;
        }
        .status {
            padding: 10px;
            background: #e3f2fd;
            border-radius: 5px;
            margin: 10px 0;
        }
    </style>
</head>
<body>
    <h1>🎤 语音交流 Demo</h1>

    <div class="status">
        <strong>状态：</strong><span id="status">未连接</span>
    </div>

    <div>
        <input type="text" id="userId" placeholder="输入你的名字" value="User1" />
    </div>

    <div class="controls">
        <button id="voiceBtn" onclick="toggleVoice()">🎤 开始语音</button>
        <button onclick="stopVoice()">⏹️ 停止语音</button>
        <button onclick="disconnect()">❌ 断开连接</button>
    </div>

    <div>
        <input type="text" id="messageText" placeholder="或输入文字消息..." />
        <button onclick="sendTextMessage()">📤 发送文字</button>
    </div>

    <div id="messages"></div>

    <script>
        let ws = null;
        let audioContext = null;  // 用于录音，16kHz
        let playbackAudioContext = null;  // 用于播放，24kHz
        let mediaStream = null;
        let audioWorkletNode = null;
        let isRecording = false;
        let audioQueue = [];
        let isPlaying = false;
        let audioPlaybackNode = null;
        let audioPlaybackQueue = [];  // 存储解码后的 Float32Array
        let audioPlaybackIndex = 0;

        // 用于累积转录文本
        let currentTranscript = "";
        let currentTranscriptElement = null;

        async function connect() {
            const userId = document.getElementById("userId").value;
            ws = new WebSocket(`ws://localhost:8000/ws/${userId}/session1`);

            ws.onopen = function(event) {
                updateStatus("已连接");
                addMessage("系统", "✅ 已连接到服务器，可以开始语音对话");
            };

            ws.onmessage = async function(event) {
                try {
                    const data = JSON.parse(event.data);
                    console.log("收到消息:", data);

                    if (data.type === "audio_delta") {
                        // 接收音频数据，加入播放队列
                        queueAudioChunk(data.audio);
                    } else if (data.type === "text") {
                        // 累积转录文本而不是创建新消息
                        appendTranscript("AI", data.text || "");
                    } else if (data.type === "transcript_done") {
                        // 完成当前转录消息
                        finishTranscript();
                    } else {
                        addMessage("系统", JSON.stringify(data));
                    }
                } catch (e) {
                    console.error("处理消息错误:", e);
                }
            };

            ws.onclose = function(event) {
                updateStatus("已断开");
                addMessage("系统", "❌ 已断开连接");
                stopVoice();
            };

            ws.onerror = function(error) {
                updateStatus("连接错误");
                addMessage("系统", "⚠️ 连接错误");
            };
        }

        async function toggleVoice() {
            if (!isRecording) {
                await startVoice();
            } else {
                stopVoice();
            }
        }

        async function startVoice() {
            try {
                if (!audioContext) {
                    audioContext = new (
                    window.AudioContext || window.webkitAudioContext)({
                        sampleRate: 16000  // DashScope 要求 16kHz
                    });
                }

                mediaStream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        sampleRate: 16000
                    }
                });

                const source = audioContext.createMediaStreamSource(
                mediaStream);

                // 使用 ScriptProcessorNode 处理音频
                const processor = audioContext.createScriptProcessor(4096,
                1, 1);

                let audioChunkCount = 0;
                processor.onaudioprocess = function(e) {
                    if (!isRecording) return;

                    const inputData = e.inputBuffer.getChannelData(0);
                    const pcmData = convertToPCM16(inputData);
                    const base64Audio = arrayBufferToBase64(pcmData);

                    if (ws && ws.readyState === WebSocket.OPEN) {
                        audioChunkCount++;
                        if (audioChunkCount % 10 === 0) {
                        }
                        ws.send(JSON.stringify({
                            type: "input_audio",
                            audio: base64Audio
                        }));
                    }
                };

                source.connect(processor);
                const dummyGain = audioContext.createGain();
                dummyGain.gain.value = 0;  // 静音，避免反馈
                processor.connect(dummyGain);
                dummyGain.connect(audioContext.destination);

                isRecording = true;
                document.getElementById("voiceBtn").classList.add("recording");
                document.getElementById("voiceBtn").innerText = "🔴 录音中...";
                updateStatus("录音中");
                addMessage("系统", "🎤 开始录音...");

            } catch (err) {
                console.error("启动录音失败:", err);
                addMessage("系统", "⚠️ 无法访问麦克风: " + err.message);
            }
        }

        function stopVoice() {
            isRecording = false;

            if (mediaStream) {
                mediaStream.getTracks().forEach(track => track.stop());
                mediaStream = null;
            }

            // 通知服务器录音已停止
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({
                    type: "input_audio_done"
                }));
            }

            document.getElementById("voiceBtn").classList.remove("recording");
            document.getElementById("voiceBtn").innerText = "🎤 开始语音";
            updateStatus("已连接");
            addMessage("系统", "⏹️ 停止录音");
        }

        function convertToPCM16(float32Array) {
            const int16Array = new Int16Array(float32Array.length);
            for (let i = 0; i < float32Array.length; i++) {
                const s = Math.max(-1, Math.min(1, float32Array[i]));
                int16Array[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
            }
            return int16Array.buffer;
        }

        function arrayBufferToBase64(buffer) {
            const bytes = new Uint8Array(buffer);
            let binary = '';
            for (let i = 0; i < bytes.byteLength; i++) {
                binary += String.fromCharCode(bytes[i]);
            }
            return btoa(binary);
        }

        function queueAudioChunk(base64Audio) {
            try {
                // 解码 base64 音频数据并转换为 Float32Array
                const binaryString = atob(base64Audio);
                const bytes = new Uint8Array(binaryString.length);
                for (let i = 0; i < binaryString.length; i++) {
                    bytes[i] = binaryString.charCodeAt(i);
                }

                // 转换为 Int16Array (PCM16)，然后转为 Float32Array
                const int16Array = new Int16Array(bytes.buffer);
                const float32Array = new Float32Array(int16Array.length);

                for (let i = 0; i < int16Array.length; i++) {
                    float32Array[i] = int16Array[i] / 32768.0;
                }

                // 将解码后的音频数据加入队列
                audioPlaybackQueue.push(float32Array);

                // 如果还没有开始播放，启动播放器
                if (!isPlaying) {
                    startAudioPlayback();
                }
            } catch (err) {
                console.error("解码音频块失败:", err);
            }
        }

        function startAudioPlayback() {
            if (isPlaying) return;

            try {
                // 为播放创建独立的 AudioContext，使用 24kHz 采样率
                if (!playbackAudioContext) {
                    playbackAudioContext = new (window.AudioContext ||
                    window.webkitAudioContext)({
                        sampleRate: 24000  // DashScope 输出 24kHz
                    });
                }

                // 如果 AudioContext 被暂停（浏览器策略），恢复它
                if (playbackAudioContext.state === 'suspended') {
                    playbackAudioContext.resume();
                }

                isPlaying = true;
                audioPlaybackIndex = 0;

                // 使用 ScriptProcessorNode 进行流式播放
                const bufferSize = 4096;
                const processor =
                playbackAudioContext.createScriptProcessor(bufferSize, 0, 1);

                processor.onaudioprocess = function(e) {
                    const output = e.outputBuffer.getChannelData(0);
                    const samplesNeeded = output.length;
                    let samplesWritten = 0;

                    // 从队列中获取音频数据并填充输出缓冲区
                    while (samplesWritten < samplesNeeded &&
                    audioPlaybackQueue.length > 0) {
                        const chunk = audioPlaybackQueue[0];

                        // 计算需要从当前块中读取的样本数
                        const samplesToRead = Math.min(
                            samplesNeeded - samplesWritten,
                            chunk.length - audioPlaybackIndex
                        );

                        // 直接复制 Float32 数据到输出
                        for (let i = 0; i < samplesToRead; i++) {
                            output[samplesWritten + i] = chunk[
                            audioPlaybackIndex + i];
                        }

                        samplesWritten += samplesToRead;
                        audioPlaybackIndex += samplesToRead;

                        // 如果当前块已读完，移除它并重置索引
                        if (audioPlaybackIndex >= chunk.length) {
                            audioPlaybackQueue.shift();
                            audioPlaybackIndex = 0;
                        }
                    }

                    // 如果队列为空且没有更多数据，填充静音
                    if (samplesWritten < samplesNeeded) {
                        for (let i = samplesWritten; i < samplesNeeded; i++) {
                            output[i] = 0;
                        }

                        // 如果队列持续为空一段时间，停止播放
                        if (audioPlaybackQueue.length === 0) {
                            setTimeout(() => {
                                if (audioPlaybackQueue.length === 0) {
                                    stopAudioPlayback();
                                }
                            }, 100);
                        }
                    }
                };

                processor.connect(playbackAudioContext.destination);
                audioPlaybackNode = processor;

            } catch (err) {
                console.error("启动音频播放失败:", err);
                isPlaying = false;
            }
        }

        function stopAudioPlayback() {
            if (audioPlaybackNode) {
                audioPlaybackNode.disconnect();
                audioPlaybackNode = null;
            }
            isPlaying = false;
            audioPlaybackQueue = [];
            audioPlaybackIndex = 0;
        }

        function sendTextMessage() {
            const input = document.getElementById("messageText");
            if (ws && input.value) {
                ws.send(JSON.stringify({
                    type: "input_text",
                    text: input.value
                }));
                addMessage("你", input.value);
                input.value = '';
            }
        }

        function disconnect() {
            stopVoice();
            stopAudioPlayback();
            if (ws) {
                ws.close();
            }
        }

        function updateStatus(text) {
            document.getElementById("status").innerText = text;
        }

        function addMessage(sender, message) {
            const messagesDiv = document.getElementById("messages");
            const messageDiv = document.createElement("div");
            messageDiv.className = "message";
            const time = new Date().toLocaleTimeString();
            messageDiv.innerHTML = `<strong>[${time}] ${sender}:</strong>
            ${message}`;
            messagesDiv.appendChild(messageDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }

        function appendTranscript(sender, text) {
            const messagesDiv = document.getElementById("messages");

            // 如果还没有当前消息元素，创建一个新的
            if (!currentTranscriptElement) {
                currentTranscript = "";
                currentTranscriptElement = document.createElement("div");
                currentTranscriptElement.className = "message";
                const time = new Date().toLocaleTimeString();
                currentTranscriptElement.innerHTML = `<strong>[${time}] ${
                sender}:</strong> <span class="transcript-content"></span>`;
                messagesDiv.appendChild(currentTranscriptElement);
            }

            // 累积文本
            currentTranscript += text;

            // 更新显示的内容
            const contentSpan = currentTranscriptElement.querySelector(
            '.transcript-content');
            if (contentSpan) {
                contentSpan.textContent = currentTranscript;
            }

            // 滚动到底部
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }

        function finishTranscript() {
            // 完成当前转录消息，准备下一条
            currentTranscript = "";
            currentTranscriptElement = null;
        }

        // 回车发送消息
        document.getElementById("messageText").addEventListener("keypress",
         function(event) {
            if (event.key === "Enter") {
                sendTextMessage();
            }
        });

        // 页面加载时自动连接
        window.onload = connect;
    </script>
</body>
</html>
"""

app = FastAPI()


@app.get("/")
async def get() -> HTMLResponse:
    """Serve the HTML test page."""
    return HTMLResponse(html)


# pylint: disable=too-many-statements, too-many-branches, unused-argument
@app.websocket("/ws/{user_id}/{session_id}")
async def single_agent_endpoint(
    websocket: WebSocket,
    user_id: str,
    session_id: str,
) -> None:
    """WebSocket endpoint for a single realtime agent."""
    try:
        await websocket.accept()

        # 发往前端的消息队列
        frontend_queue = asyncio.Queue()

        async def frontend_receive() -> None:
            try:
                while True:
                    msg = await frontend_queue.get()

                    # 将 dataclass 对象转换为字典
                    if hasattr(msg, "__dataclass_fields__"):
                        msg_dict = asdict(msg)
                    else:
                        msg_dict = msg

                    msg_type = msg_dict.get("type", "unknown")

                    # 处理音频数据
                    if msg_type == "response_audio_delta":
                        # 转换为前端期望的格式
                        await websocket.send_json(
                            {
                                "type": "audio_delta",
                                "audio": msg_dict.get("delta", ""),
                            },
                        )
                    elif msg_type == "response_audio_transcript_delta":
                        # 发送转录文本增量
                        text = msg_dict.get("delta", "")
                        await websocket.send_json(
                            {
                                "type": "text",
                                "text": text,
                            },
                        )
                    elif msg_type == "response_audio_transcript_done":
                        # 转录完成，通知前端可以开始新的消息
                        await websocket.send_json(
                            {
                                "type": "transcript_done",
                            },
                        )
                    elif msg_type == "input_transcription_done":
                        transcript = msg_dict.get("transcript", "")
                        await websocket.send_json(
                            {
                                "type": "input_transcription",
                                "text": transcript,
                            },
                        )
                    elif msg_type == "error":
                        error_type = msg_dict.get("error_type", "unknown")
                        error_code = msg_dict.get("code", "unknown")
                        error_msg = msg_dict.get("message", "Unknown error")
                        print(
                            f"[ERROR] ⚠️⚠️⚠️ Model error: type={error_type}, "
                            f"code={error_code}, message={error_msg}",
                        )
                        await websocket.send_json(
                            {
                                "type": "error",
                                "message": error_msg,
                            },
                        )
                    elif msg_type == "session_created":
                        await websocket.send_json(msg_dict)
                    elif msg_type == "session_ended":
                        await websocket.send_json(msg_dict)
                    else:
                        await websocket.send_json(msg_dict)
            except Exception as e:
                print(f"[ERROR] frontend_receive error: {e}")
                import traceback

                traceback.print_exc()

        asyncio.create_task(frontend_receive())

        api_key = os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError(
                "DASHSCOPE_API_KEY environment variable is not set!",
            )

        model = DashScopeRealtimeModel(
            model_name="qwen3-omni-flash-realtime",
            api_key=api_key,
        )
        # model = GeminiRealtimeModel(
        #     model_name="gemini-2.5-flash-native-audio-preview-12-2025",
        #     api_key=os.getenv("GEMINI_API_KEY"),
        # )

        agent = RealtimeAgentBase(
            name="Friday",
            sys_prompt="You are a helpful assistant.",
            model=model,
        )

        # or
        # chatroom = ChatRoom(agents=[agent, ...])

        try:
            await agent.start(frontend_queue)
        except Exception as e:
            print(f"[ERROR] Failed to start agent: {e}")
            import traceback

            traceback.print_exc()
            raise
        # or
        # await chatroom.start(frontend_queue)

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type", "")

                if msg_type == "input_audio":
                    # 处理音频输入 - 通过 agent.model 发送
                    audio_base64 = data.get("audio", "")
                    if audio_base64:
                        try:
                            # 通过 agent.model 发送音频数据
                            await agent.model.send(
                                {
                                    "type": "audio",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "audio/pcm",
                                        "data": audio_base64,
                                    },
                                },
                            )
                        except Exception as e:
                            print(
                                f"[ERROR] Failed to send audio to model: {e}",
                            )

                elif msg_type == "input_text":
                    # 处理文本输入 - 通过 agent.model 发送
                    text = data.get("text", "")
                    if text:
                        await agent.model.send(
                            {
                                "type": "text",
                                "text": text,
                            },
                        )

                elif msg_type == "input_audio_done":
                    # 音频输入完成，DashScope 会自动检测并处理
                    pass

                else:
                    # 处理其他类型的消息
                    await agent.handle_input(data)
                # or
                # await chatroom.handle_frontend_event(data)

        except WebSocketDisconnect:
            # 不需要再次关闭，连接已经断开
            pass
        finally:
            # 清理资源
            try:
                await agent.stop()
            except Exception as e:
                print(f"[ERROR] Error stopping agent: {e}")

    except Exception as e:
        print(f"[ERROR] WebSocket endpoint error: {e}")
        import traceback

        traceback.print_exc()
        raise


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "run_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="debug",
    )
