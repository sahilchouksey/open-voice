from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.util
import json
from dataclasses import dataclass
from typing import Any


def arch_router_backend_available() -> bool:
    return (
        importlib.util.find_spec("transformers") is not None
        and importlib.util.find_spec("torch") is not None
    )


@dataclass(frozen=True, slots=True)
class ArchRouteSpec:
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class ArchRouterConfig:
    model_name: str = "katanemo/Arch-Router-1.5B"
    max_new_tokens: int = 96


@dataclass(frozen=True, slots=True)
class ArchRouterResult:
    route_name: str
    confidence: float | None
    raw_response: str
    backend: str
    error: str | None = None


class ArchRouterClient:
    def __init__(self, config: ArchRouterConfig | None = None) -> None:
        self._config = config or ArchRouterConfig()
        self._model: Any | None = None
        self._tokenizer: Any | None = None
        self._lock = asyncio.Lock()
        self._last_error: str | None = None

    @property
    def available(self) -> bool:
        return arch_router_backend_available()

    @property
    def status(self) -> str:
        if not arch_router_backend_available():
            return "missing_dependency"
        return "ready"

    async def load(self) -> None:
        if not arch_router_backend_available():
            raise RuntimeError("Arch Router backend requires 'transformers' and 'torch'.")
        if self._model is not None and self._tokenizer is not None:
            return

        async with self._lock:
            if self._model is not None and self._tokenizer is not None:
                return

            transformers = importlib.import_module("transformers")
            loop = asyncio.get_running_loop()

            def _load() -> tuple[Any, Any]:
                tokenizer = transformers.AutoTokenizer.from_pretrained(self._config.model_name)
                model = transformers.AutoModelForCausalLM.from_pretrained(
                    self._config.model_name,
                    trust_remote_code=True,
                    torch_dtype="auto",
                    device_map="auto",
                )
                return tokenizer, model

            self._tokenizer, self._model = await loop.run_in_executor(None, _load)

    async def classify(self, text: str, routes: tuple[ArchRouteSpec, ...]) -> ArchRouterResult:
        if not arch_router_backend_available():
            raise RuntimeError("Arch Router backend requires 'transformers' and 'torch'.")

        try:
            await self.load()
            assert self._model is not None
            assert self._tokenizer is not None
            raw = await asyncio.to_thread(self._run_model, text, routes)
            route_name, confidence = _parse_route(raw)
            return ArchRouterResult(
                route_name=route_name,
                confidence=confidence,
                raw_response=raw,
                backend="arch-router-1.5b",
            )
        except Exception as exc:
            self._last_error = str(exc)
            raise

    def _run_model(self, text: str, routes: tuple[ArchRouteSpec, ...]) -> str:
        torch = importlib.import_module("torch")
        model = self._model
        tokenizer = self._tokenizer
        assert model is not None
        assert tokenizer is not None

        prompt = _build_prompt(text, routes)
        inputs = tokenizer(prompt, return_tensors="pt")

        if not hasattr(model, "hf_device_map"):
            model_device = next(model.parameters()).device
            inputs = {key: value.to(model_device) for key, value in inputs.items()}

        with torch.inference_mode():
            output = model.generate(
                **inputs,
                max_new_tokens=self._config.max_new_tokens,
                do_sample=False,
                temperature=0.0,
                pad_token_id=tokenizer.eos_token_id,
            )

        generated = output[0][inputs["input_ids"].shape[-1] :]
        return tokenizer.decode(generated, skip_special_tokens=True).strip()


def default_arch_routes() -> tuple[ArchRouteSpec, ...]:
    return (
        ArchRouteSpec("trivial_route", "Very short or greeting-like requests."),
        ArchRouteSpec("simple_route", "Basic questions needing little reasoning."),
        ArchRouteSpec("moderate_route", "Normal multi-step requests."),
        ArchRouteSpec("complex_route", "Complex architecture or coding reasoning."),
        ArchRouteSpec("expert_route", "Research-level or expert reasoning tasks."),
    )


def _build_prompt(text: str, routes: tuple[ArchRouteSpec, ...]) -> str:
    routes_obj = [{"name": route.name, "description": route.description} for route in routes]
    conversation_obj = [{"role": "user", "content": text}]
    return (
        "You are a strict JSON router. Choose exactly one route.\n"
        "Return only JSON with keys: route, confidence, task.\n"
        "task must be either 'coding' or 'general'.\n"
        f"<routes>{json.dumps(routes_obj)}</routes>\n"
        f"<conversation>{json.dumps(conversation_obj)}</conversation>\n"
        "Respond in JSON only."
    )


def _parse_route(raw: str) -> tuple[str, float | None]:
    def _from_obj(obj: object) -> tuple[str, float | None] | None:
        if not isinstance(obj, dict):
            return None
        route = str(obj.get("route", "moderate_route"))
        confidence = obj.get("confidence")
        if isinstance(confidence, (int, float)):
            return route, float(confidence)
        return route, None

    try:
        start = raw.find("{")
        end = raw.rfind("}")
        candidate = raw[start : end + 1] if start != -1 and end != -1 and end >= start else raw
        try:
            parsed_json = json.loads(candidate)
            result = _from_obj(parsed_json)
            if result is not None:
                return result
        except Exception:
            pass

        parsed_py = ast.literal_eval(candidate)
        result = _from_obj(parsed_py)
        if result is not None:
            return result
    except Exception:
        pass
    return "moderate_route", None
