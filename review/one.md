# AgentScope 리포지토리 분석 (1차)

## 프로젝트 개요

**AgentScope**는 LLM 기반 멀티 에이전트 애플리케이션을 구축하기 위한 **프로덕션 레디 파이썬 프레임워크**입니다.

- **버전**: 1.0.17
- **라이선스**: Apache 2.0
- **Python**: 3.10+
- **목적**: 에이전트 지향 프로그래밍(AOP) 패러다임으로 5분 안에 에이전트를 구축

---

## 전체 구조

```
/home/user/agentscope/
├── src/agentscope/          # 메인 소스 코드 (213개 파일, ~25K LOC)
├── tests/                   # 단위 테스트 (59개 파일)
├── examples/                # 예제 구현 (92개 파일)
├── docs/                    # 문서
├── .github/                 # GitHub 워크플로우 및 템플릿
├── pyproject.toml           # 파이썬 프로젝트 설정
├── README.md / README_zh.md # 영/중 이중 언어 문서
├── CONTRIBUTING.md          # 기여 가이드라인
└── LICENSE                  # Apache 2.0 라이선스
```

---

## 핵심 모듈 구조

| 모듈 | 역할 |
|------|------|
| `agent/` | 에이전트 구현 (ReAct, A2A, 실시간, 사용자 에이전트 등) |
| `model/` | 모델 통합 (OpenAI, Anthropic, Gemini, DashScope, Ollama) |
| `message/` | 멀티모달 메시지 처리 (Text, Audio, Image, Video, Tool 블록) |
| `formatter/` | 각 모델 API에 맞는 요청/응답 포맷 변환 레이어 |
| `tool/` | 툴 프레임워크 (함수 실행, 코딩 툴, 멀티모달 지원) |
| `memory/` | 단기/장기 메모리 시스템 (Redis, mem0ai, ReMe 지원) |
| `rag/` | RAG 지원 (PDF/DOCX/Excel/PPT 리더 + 벡터 DB 연동) |
| `mcp/` | Model Context Protocol 클라이언트 (stdio, HTTP) |
| `a2a/` | 에이전트 간 통신(A2A) 프로토콜 |
| `realtime/` | 실시간 음성 에이전트 지원 |
| `tts/` | Text-to-Speech (OpenAI, Gemini, DashScope) |
| `tracing/` | OpenTelemetry 기반 분산 추적 |
| `pipeline/` | 메시지 흐름 오케스트레이션 (채팅룸, 메시지 허브) |
| `session/` | 세션 관리 (JSON, Redis 기반 영속성) |
| `evaluate/` | 모델 평가 및 벤치마크 |
| `tuner/` | 모델 파인튜닝 (DSPy, datasets, litellm 연동) |
| `hooks/` | 이벤트 훅 시스템 (pre/post 훅) |
| `embedding/` | 임베딩 모델 (OpenAI, Gemini, DashScope, Ollama + 캐싱) |

---

## 주요 의존성

### 코어 의존성 (항상 필요)
- `anthropic`, `openai`, `dashscope` - 모델 API
- `mcp>=1.13` - Model Context Protocol
- `opentelemetry-*` - 분산 추적
- `tiktoken`, `numpy`, `json5`, `json_repair` - 데이터 처리
- `sounddevice`, `sqlalchemy` - 오디오/데이터베이스

### 선택적 기능 그룹
- **a2a**: `a2a-sdk`, `httpx`, `nacos-sdk-python`
- **realtime**: `websockets>=14.0`, `scipy`
- **models**: `ollama>=0.5.4`, `google-genai`
- **memory**: `redis`, `mem0ai`, `reme-ai>=0.2.0.3`
- **rag**: PDF/DOCX/Excel/PPT 리더 + 벡터 DB
- **vdbs**: Qdrant, Milvus, MongoDB, MySQL, OceanBase
- **evaluate**: `ray`
- **tuner**: `dspy>=3.1.0`, `datasets>=4.0.0`, `litellm[proxy]`

---

## 아키텍처 특징

1. **Hook 시스템**: `pre_reply`/`post_reply`, `pre_observe`/`post_observe` 등 이벤트 기반 확장 포인트
2. **Formatter 패턴**: 모델별 메시지 변환 레이어 분리 (OpenAI, Anthropic, Gemini, DashScope 등)
3. **비동기 우선 설계**: `asyncio` 기반 동시 에이전트 실행
4. **멀티모달 메시지**: TextBlock, AudioBlock, ImageBlock, VideoBlock, ToolUseBlock 지원
5. **스레드 안전성**: `ContextVar`를 통한 전역 설정 관리 (`run_id`, `project`, `name` 등)
6. **세션 영속성**: JSON 및 Redis 기반 세션 저장
7. **관찰성**: OpenTelemetry + Arize-Phoenix, Langfuse 연동

---

## 테스트 구조

**59개 테스트 파일** 커버리지:
- Formatter 테스트: Anthropic, DashScope, Gemini, Ollama, OpenAI, A2A
- 모델 통합 테스트: 각 API 제공자별
- 에이전트 테스트: ReActAgent, A2A Agent
- 메모리 테스트: 워킹 메모리, 장기 메모리, 압축, Redis/SQLite 백엔드
- RAG 테스트: 지식 베이스, 각종 리더, 벡터 스토어
- 실시간 테스트: OpenAI, Gemini, DashScope 실시간 에이전트
- TTS/훅/토큰/트레이싱/평가/플래닝/세션/MCP 테스트

---

## 예제 카테고리

| 카테고리 | 예제 |
|----------|------|
| 에이전트 | ReAct, 실시간 음성, 음성 대화, A2A, 브라우저, 딥리서치, 메타 플래너 |
| 메모리 | 단기/장기 메모리, 메모리 압축 |
| RAG | 지식 베이스 구축 및 검색 |
| 워크플로우 | 멀티 에이전트 대화, 토론, 동시 실행 |
| 평가/튜닝 | 벤치마킹(ACE-Bench), 모델 파인튜닝 |
| 게임 | 늑대인간 게임 시뮬레이션 |

---

## 최근 주요 업데이트

| 날짜 | 기능 |
|------|------|
| 2026.02 | 실시간 음성 에이전트 지원 |
| 2025.12 | A2A(에이전트 간) 프로토콜 |
| 2025.12 | TTS(텍스트-음성 변환) 지원 |
| 2025.11 | Anthropic Agent Skills |
| 2025.xx | Agentic RL (Trinity-RFT) |
| 2025.xx | ReMe 장기 메모리 강화 |

---

## 코드 품질 기준

- **타입 체킹**: MyPy (strict 모드)
- **코드 포맷**: Black (79자 줄 길이)
- **린팅**: Flake8 + Pylint
- **커밋 형식**: Conventional Commits (`feat`, `fix`, `docs`, `refactor`, `perf` 등)
- **CI/CD**: GitHub Actions 자동 테스트 및 품질 검사
