from __future__ import annotations

from fastapi.testclient import TestClient

from open_voice_runtime.app.asgi import create_asgi_app
from open_voice_runtime.app.server import RuntimeServer
from open_voice_runtime.session.manager import InMemorySessionManager
from open_voice_runtime.session.models import EngineSelection, SessionCreateRequest
from open_voice_runtime.transport.websocket.handler import RealtimeConnectionHandler
from open_voice_runtime.transport.websocket.session import RealtimeConversationSession


def test_list_sessions_returns_recent_first_with_summary_fields() -> None:
    session_manager = InMemorySessionManager()
    first = _create_session_with_turn(
        session_manager,
        user_text="first question",
        assistant_text="first answer",
    )
    second = _create_session_with_turn(
        session_manager,
        user_text="latest request about sdk",
        assistant_text="latest response",
    )

    app = create_asgi_app(_server_for_tests(session_manager))
    client = TestClient(app)

    response = client.get("/v1/sessions")
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 2

    assert payload[0]["session_id"] == second.session_id
    assert payload[0]["last_user_text"] == "latest request about sdk"
    assert payload[0]["last_assistant_text"] == "latest response"
    assert payload[0]["turn_count"] == 1
    assert payload[0]["completed_turn_count"] == 1

    assert payload[1]["session_id"] == first.session_id
    assert payload[1]["title"] == "first question"


def test_list_sessions_respects_limit_query_param() -> None:
    session_manager = InMemorySessionManager()
    _create_session_with_turn(session_manager, user_text="one")
    _create_session_with_turn(session_manager, user_text="two")
    _create_session_with_turn(session_manager, user_text="three")

    app = create_asgi_app(_server_for_tests(session_manager))
    client = TestClient(app)

    response = client.get("/v1/sessions", params={"limit": 2})
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["last_user_text"] == "three"
    assert payload[1]["last_user_text"] == "two"


def test_list_session_turns_returns_transcript_data() -> None:
    session_manager = InMemorySessionManager()
    session = _create_session_with_turn(
        session_manager,
        user_text="What is the weather?",
        assistant_text="It is sunny today.",
    )

    app = create_asgi_app(_server_for_tests(session_manager))
    client = TestClient(app)

    response = client.get(f"/v1/sessions/{session.session_id}/turns", params={"limit": 10})
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["user_text"] == "What is the weather?"
    assert payload[0]["assistant_text"] == "It is sunny today."
    assert isinstance(payload[0]["turn_id"], str)


def test_list_session_turns_returns_404_for_unknown_session() -> None:
    session_manager = InMemorySessionManager()
    app = create_asgi_app(_server_for_tests(session_manager))
    client = TestClient(app)

    response = client.get("/v1/sessions/missing/turns")
    assert response.status_code == 404


def _create_session_with_turn(
    session_manager: InMemorySessionManager,
    *,
    user_text: str,
    assistant_text: str | None = None,
):
    import asyncio

    async def run():
        state = await session_manager.create(
            SessionCreateRequest(engine_selection=EngineSelection(llm="test"))
        )
        state.begin_turn()
        state.complete_turn(user_text=user_text, assistant_text=assistant_text)
        return state

    return asyncio.run(run())


def _server_for_tests(session_manager: InMemorySessionManager) -> RuntimeServer:
    realtime_session = RealtimeConversationSession(session_manager)
    handler = RealtimeConnectionHandler(realtime_session)

    class _NoopTraceSink:
        enabled = False

        async def append_frontend_records(
            self, session_id: str, records: list[dict[str, object]]
        ) -> None:
            return None

    class _Deps:
        def __init__(self) -> None:
            self.session_manager = session_manager
            self.realtime_handler = handler
            self.trace_sink = _NoopTraceSink()
            self.engine_catalog = {
                "stt": [],
                "vad": [],
                "router": [],
                "llm": [],
                "tts": [],
            }

    return RuntimeServer(dependencies=_Deps())
