from __future__ import annotations

from dataclasses import dataclass, field

from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.vad.contracts import VadEvent, VadEventKind
from open_voice_runtime.vad.engine import BaseVadStream


@dataclass(slots=True)
class EndpointDecision:
    vad_events: list[VadEvent] = field(default_factory=list)
    speech_started: bool = False
    speech_ended: bool = False
    endpoint_ready: bool = False
    speaking: bool | None = None
    probability: float | None = None
    reason: str | None = None


class EndpointDetector:
    def __init__(self, stream: BaseVadStream) -> None:
        self._stream = stream
        self.reset()

    async def push_audio(self, chunk: AudioChunk) -> EndpointDecision:
        result = await self._stream.push_audio(chunk)
        decision = EndpointDecision(vad_events=list(result.events))
        for item in result.events:
            if item.kind is VadEventKind.START_OF_SPEECH:
                self._speaking = True
                self._saw_speech = True
                decision.speech_started = True
                decision.speaking = True
            elif item.kind is VadEventKind.END_OF_SPEECH:
                self._speaking = False
                decision.speech_ended = True
                decision.endpoint_ready = self._saw_speech
                decision.speaking = False
                decision.reason = "vad_end"
            elif item.kind is VadEventKind.INFERENCE:
                decision.speaking = item.speaking
                decision.probability = item.probability
                if item.speaking is True and not self._speaking:
                    self._speaking = True
                    self._saw_speech = True
                    decision.speech_started = True
                elif item.speaking is False and self._speaking:
                    self._speaking = False
                    decision.speech_ended = True
                    decision.endpoint_ready = self._saw_speech
                    decision.reason = "vad_inference_end"
        return decision

    def force_commit(self) -> EndpointDecision:
        return EndpointDecision(
            speech_ended=self._saw_speech,
            endpoint_ready=self._saw_speech,
            speaking=False,
            reason="explicit_commit",
        )

    def reset(self) -> None:
        self._speaking = False
        self._saw_speech = False

    async def close(self) -> None:
        await self._stream.close()
