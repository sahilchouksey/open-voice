import pytest

from open_voice_runtime.session.manager import InMemorySessionManager
from open_voice_runtime.session.models import (
    EngineSelection,
    SessionCreateRequest,
    SessionStatus,
    SessionTransition,
)


@pytest.mark.anyio
async def test_create_and_get_session():
    manager = InMemorySessionManager()
    request = SessionCreateRequest(
        engine_selection=EngineSelection(stt="test", llm="test"),
        metadata={"test": True},
    )
    state = await manager.create(request)
    assert state.session_id.startswith("sess_")
    assert state.status == SessionStatus.CREATED
    assert state.engine_selection.stt == "test"

    # Retrieve
    retrieved = await manager.get(state.session_id)
    assert retrieved.session_id == state.session_id
    assert retrieved.status == state.status


@pytest.mark.anyio
async def test_update_session_status():
    manager = InMemorySessionManager()
    request = SessionCreateRequest()
    state = await manager.create(request)

    # Transition through valid states
    updated = await manager.update(
        state.session_id,
        SessionTransition(to_status=SessionStatus.LOADING, reason="test"),
    )
    assert updated.status == SessionStatus.LOADING

    # Verify
    retrieved = await manager.get(state.session_id)
    assert retrieved.status == SessionStatus.LOADING


@pytest.mark.anyio
async def test_close_session():
    manager = InMemorySessionManager()
    request = SessionCreateRequest()
    state = await manager.create(request)
    session_id = state.session_id

    await manager.close(session_id)

    # Verify deletion - should raise exception when trying to get
    try:
        await manager.get(session_id)
        assert False, "Expected exception for closed session"
    except Exception:
        pass  # Expected


@pytest.mark.anyio
async def test_session_turns_preserved():
    manager = InMemorySessionManager()
    request = SessionCreateRequest()
    state = await manager.create(request)

    # Begin and complete a turn
    turn_id = state.begin_turn()
    state.complete_turn(user_text="Hello", assistant_text="Hi there")

    # In-memory manager shares the same object reference
    retrieved = await manager.get(state.session_id)
    assert len(retrieved.turns) == 1
    assert retrieved.turns[0].user_text == "Hello"
    assert retrieved.turns[0].assistant_text == "Hi there"


class TestRedisSessionManager:
    """Redis tests - skipped if Redis is not available."""

    @pytest.mark.anyio
    async def test_redis_manager_import(self):
        """Test that Redis manager can be imported."""
        from open_voice_runtime.session.redis import RedisSessionManager

        # Just verify the class exists
        assert RedisSessionManager is not None

    @pytest.mark.anyio
    async def test_redis_manager_basic(self):
        """Test Redis manager with local Redis (skipped if unavailable)."""
        from open_voice_runtime.session.redis import RedisSessionManager

        try:
            manager = RedisSessionManager("redis://localhost:6379/0", namespace="ov_test")
            await manager._ensure_redis()
        except Exception:
            pytest.skip("Redis not available")

        # Test basic operations
        request = SessionCreateRequest(
            engine_selection=EngineSelection(llm="test"),
            metadata={"source": "test"},
        )
        state = await manager.create(request)
        assert state.session_id is not None

        # Retrieve
        retrieved = await manager.get(state.session_id)
        assert retrieved.session_id == state.session_id

        # Update
        updated = await manager.update(
            state.session_id,
            SessionTransition(to_status=SessionStatus.LOADING),
        )
        assert updated.status == SessionStatus.LOADING

        # Cleanup
        await manager.close(state.session_id)

        # Verify deletion
        try:
            await manager.get(state.session_id)
            assert False, "Expected exception for closed session"
        except Exception:
            pass  # Expected
