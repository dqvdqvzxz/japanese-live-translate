<!--
The brief this project started from, kept verbatim for reference.

What was actually built — including everything that changed once the API,
the hardware and real meetings had a say — is in prompt.md and the rest of
docs/. Where the two disagree, this file is the older one.
-->

You are a Senior Software Architect and Senior Full-Stack Engineer.

Your task is to design and implement a production-oriented
macOS application named:

Japanese Live Translator

===============================================
1. PROJECT OBJECTIVE
===============================================

Build a lightweight macOS desktop application that:

1. Captures Microsoft Teams audio.
2. Streams audio to OpenAI API.
3. Displays Japanese transcripts in real time.
4. Translates finalized Japanese speech into Vietnamese.
5. Displays Japanese and Vietnamese subtitles together.
6. Runs locally without requiring local AI models.
7. Supports long-running meetings.

The application must support:

- Microsoft Teams Desktop
- Microsoft Teams Web
- macOS
- Low-resource computers

IMPORTANT:

Do NOT implement local Whisper.
Do NOT implement local LLM inference.
Do NOT integrate DeepSeek.

All AI inference must use OpenAI API.

===============================================
2. TRANSLATION STRATEGY
===============================================

Implement DUAL-SPEED TRANSLATION.

Fast Lane:

Audio
 -> OpenAI Realtime STT
 -> Japanese Partial Transcript
 -> UI immediately

Accurate Lane:

Japanese Final Transcript
 -> Segment Manager
 -> OpenAI Translation API
 -> Vietnamese Translation
 -> UI

IMPORTANT:

Japanese partial transcripts should appear as
soon as supported by the streaming API.

Vietnamese translation should be generated from
finalized, meaningful Japanese segments.

Do not trigger translation on every STT delta.

===============================================
3. TECHNOLOGY STACK
===============================================

Desktop:
- Tauri 2
- React
- TypeScript
- Vite

Audio Capture:
- Swift
- Apple ScreenCaptureKit

Backend:
- Python 3.12+
- FastAPI
- asyncio
- WebSocket

Speech-to-Text:
- OpenAI gpt-live-transcribe

Translation:
- OpenAI gpt-5.4-mini
- OpenAI Responses API

Storage:
- SQLite, optional
- In-memory session history by default

Secrets:
- macOS Keychain

Testing:
- Pytest
- Vitest
- ESLint
- Ruff

===============================================
4. IMPLEMENTATION REQUIREMENTS
===============================================

The implementation must include:

A. Audio Capture

- Capture Teams audio using macOS APIs.
- Support Teams Desktop and browser.
- Support headphones.
- Exclude microphone audio by default.
- Convert audio to the format required by
  the selected OpenAI endpoint.
- Stream audio without blocking.
- Release resources when stopped.

B. OpenAI Realtime STT

- Persistent realtime connection.
- Japanese language preference.
- Handle partial transcript events.
- Handle finalized transcript events.
- Reconnect after network interruption.
- Prevent duplicate segments.
- Preserve segment ordering.

C. Segment Manager

- Handle partial and final states.
- Merge short finalized segments when needed.
- Avoid translating incomplete utterances.
- Avoid duplicated translation jobs.
- Preserve source-to-translation mapping.

D. Translation Engine

- Translate Japanese into Vietnamese.
- Preserve negation and technical meaning.
- Preserve ticket IDs and identifiers.
- Support configurable IT glossary.
- Support context from previous sentences.
- Avoid translating previous sentences again.
- Use asynchronous translation requests.
- Handle errors, retries, and timeouts.
- Preserve output display order.

E. Desktop UI

- Show Japanese partial text immediately.
- Show finalized Japanese text.
- Show Vietnamese translation below Japanese.
- Provide Start, Stop, Pause, Resume.
- Provide Always on Top.
- Provide session history.
- Provide settings.
- Provide connection status.
- Support Dark Mode.
- Support configurable font size.

F. Security

- Never hardcode API keys.
- Store API key using macOS Keychain.
- Never expose API key to React.
- Bind local backend only to localhost.
- Protect local WebSocket/HTTP endpoints.
- Do not log raw meeting content.
- Do not save audio by default.
- Do not persist transcripts by default.
- Make capture status explicit.
- Do not start capturing without user action.

===============================================
5. OPENAI API VERIFICATION
===============================================

Before implementing OpenAI integration:

1. Read the latest official OpenAI documentation.
2. Verify the requested model names.
3. Verify the endpoint and transport.
4. Verify audio encoding requirements.
5. Verify realtime transcript event schemas.
6. Verify finalization behavior.
7. Verify Responses API request parameters.
8. Verify supported reasoning configuration.
9. Verify pricing and usage reporting.
10. Document any API limitations.

Official documentation:

https://developers.openai.com/api/docs/guides/realtime-transcription

https://developers.openai.com/api/docs/guides/transcription

https://developers.openai.com/api/docs/guides/text

https://developers.openai.com/api/docs/pricing

Do not invent endpoint names, event types,
request fields, or SDK methods.

If the specified model is unavailable in the
configured API project, report the limitation
and propose a verified OpenAI alternative.

===============================================
6. ARCHITECTURE
===============================================

Use a modular architecture.

Required components:

AudioCaptureService
AudioStreamManager
OpenAIRealtimeSTTProvider
TranscriptManager
SegmentManager
TranslationQueue
OpenAITranslationProvider
SessionManager
EventDispatcher
WebSocketGateway
SettingsManager
HistoryManager

Use dependency injection where appropriate.

Separate business logic from transport and UI.

Define clear interfaces between components.

Use typed data models.

===============================================
7. IMPLEMENTATION PHASES
===============================================

PHASE 0: Project Setup

- Initialize monorepo.
- Setup Python backend.
- Setup React/Tauri.
- Setup Swift package.
- Configure lint and tests.
- Define shared event schemas.
- Verify OpenAI API compatibility.

PHASE 1: Audio Capture

- Implement Swift ScreenCaptureKit.
- Capture Teams audio.
- Convert to PCM.
- Implement audio streaming IPC.
- Test audio quality.
- Test 30-minute continuous capture.

PHASE 2: Japanese STT

- Implement OpenAI Realtime connection.
- Send captured audio.
- Receive transcript delta events.
- Receive final transcript events.
- Normalize transcript events.
- Add reconnection handling.
- Add unit tests.

PHASE 3: Translation

- Implement Segment Manager.
- Implement translation queue.
- Implement OpenAI Responses translation.
- Implement context and glossary.
- Add translation tests.
- Measure latency.

PHASE 4: Desktop UI

- Implement subtitle interface.
- Implement session controls.
- Implement settings.
- Implement history.
- Implement Always on Top.
- Implement connection status.

PHASE 5: Integration

- Integrate Swift, Python, and React.
- Implement session lifecycle.
- Implement error handling.
- Implement security.
- Implement process cleanup.

PHASE 6: Testing and Release

- Test on actual Microsoft Teams.
- Test browser audio.
- Test with headphones.
- Test network interruption.
- Benchmark latency.
- Test 60-minute meetings.
- Package macOS application.
- Document installation and usage.

===============================================
8. TESTING REQUIREMENTS
===============================================

Write unit tests and integration tests.

Use mocks for OpenAI calls in automated tests.

Test at least:

- Partial transcript handling.
- Final transcript handling.
- Duplicate transcript prevention.
- Translation job creation.
- Translation ordering.
- Translation timeout.
- Network disconnection.
- Reconnection.
- Pause and Resume.
- Session Stop.
- Resource cleanup.
- API error handling.

Provide a golden translation test dataset
with Japanese IT meeting sentences.

Prioritize Japanese negation, conditions,
numbers, ticket IDs, and technical terminology.

===============================================
9. PERFORMANCE TARGETS
===============================================

Target:

Japanese partial latency:
P50 under 1.5 seconds.

Vietnamese final translation:
P50 under 3 seconds after an utterance ends.

End-to-end P95:
Under 8 seconds.

Stable continuous operation:
At least 60 minutes.

These are TARGETS, not guaranteed performance.

Measure actual values.

Do not fake benchmark results.

===============================================
10. DEVELOPMENT RULES
===============================================

Follow these rules:

1. Implement incrementally.
2. Keep modules small and testable.
3. Avoid unnecessary dependencies.
4. Use async processing.
5. Avoid blocking the audio thread.
6. Implement bounded queues.
7. Handle cancellation correctly.
8. Prevent memory leaks.
9. Use structured logging.
10. Avoid logging sensitive content.
11. Document important design decisions.
12. Verify actual compilation and test results.
13. Never claim unexecuted tests passed.
14. Never claim Teams compatibility without
    testing against the actual application.
15. Preserve source-to-translation relationships.

===============================================
11. WORKING METHOD
===============================================

First inspect the existing repository.

If the repository is empty, initialize it.

Before writing implementation code:

1. Create docs/requirements.md
2. Create docs/architecture.md
3. Create docs/api-design.md
4. Create docs/development-plan.md
5. Create docs/security.md
6. Create CLAUDE.md

Then implement Phase 0 and Phase 1 first.

Do not start by implementing the full UI.

Audio capture is the highest technical risk
and must be validated before proceeding.

After each phase:

- Run relevant tests.
- Run lint and type checks.
- Fix introduced errors.
- Summarize implemented features.
- Report unresolved issues.
- Update development-plan.md.
- Record verification evidence.
- Identify the next phase.

Proceed to the next phase only when the
previous phase's acceptance criteria are met.

For checks requiring actual Teams audio,
API credentials, macOS permissions, or hardware,
report them as pending manual verification
when they cannot be performed automatically.

Never fabricate verification results.

===============================================
12. EXPECTED DELIVERABLES
===============================================

Deliver:

1. Complete source code.
2. Architecture documentation.
3. API documentation.
4. Setup instructions.
5. Environment configuration example.
6. Unit tests.
7. Integration tests.
8. macOS build instructions.
9. Security documentation.
10. Performance benchmark report.

The end result should be a functional
Japanese-to-Vietnamese live subtitle application
for Microsoft Teams using OpenAI API only.

Begin with architecture verification,
project setup, and the audio capture PoC.