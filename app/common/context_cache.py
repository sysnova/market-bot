"""Typed mappings into the common immutable analytical-context cache."""

from collections import defaultdict
from collections.abc import ItemsView, Iterator, MutableMapping, ValuesView
from typing import Any, Protocol
from weakref import finalize

from pydantic import BaseModel


class ContextCacheClient(Protocol):
    def call(self, operation: str, *args: object) -> Any: ...  # noqa: ANN401
    def view(self, capacity: int = 2_000) -> str: ...
    def close_view(self, view: str) -> None: ...


_backend: ContextCacheClient | None = None


def set_context_cache(client: ContextCacheClient) -> None:
    global _backend
    _backend = client


class CachedContexts[Key: str, Model: BaseModel](MutableMapping[Key, Model]):
    def __init__(self, model: type[Model], client: ContextCacheClient, key_type: type[Key]) -> None:
        self._model = model
        self._client = client
        self._key_type = key_type
        self._view = client.view()
        cleanup = finalize(self, client.close_view, self._view)
        cleanup.atexit = False  # The process lease handles interpreter shutdown.

    def __getitem__(self, key: Key) -> Model:
        value = self._client.call("get", self._view, key)
        if value is None:
            raise KeyError(key)
        return self._model.model_validate_json(value)

    def __setitem__(self, key: Key, value: Model) -> None:
        self._client.call("put", self._view, key, value.model_dump_json())

    def __delitem__(self, key: Key) -> None:
        if key not in self:
            raise KeyError(key)
        self._client.call("remove", self._view, key)

    def __iter__(self) -> Iterator[Key]:
        return (self._key_type(key) for key in self._client.call("keys", self._view))

    def __len__(self) -> int:
        return len(self._client.call("keys", self._view))

    def _snapshot(self) -> dict[Key, Model]:
        return {
            self._key_type(key): self._model.model_validate_json(value)
            for key, value in self._client.call("snapshot", self._view).items()
        }

    def items(self) -> ItemsView[Key, Model]:
        return self._snapshot().items()

    def values(self) -> ValuesView[Model]:
        return self._snapshot().values()


def context_store[Model: BaseModel](model: type[Model]) -> MutableMapping[str, Model]:
    client = _backend
    return CachedContexts(model, client, str) if client is not None else {}


def grouped_context_store[Key: str, Model: BaseModel](
    key_type: type[Key], model: type[Model]
) -> defaultdict[str, MutableMapping[Key, Model]]:
    # Outer keys contain only lightweight mapping handles. Values live centrally.
    def factory() -> MutableMapping[Key, Model]:
        return CachedContexts(model, _backend, key_type) if _backend is not None else {}

    class Groups(defaultdict[str, MutableMapping[Key, Model]]):
        def setdefault(
            self, key: str, default: MutableMapping[Key, Model] | None = None
        ) -> MutableMapping[Key, Model]:
            if key not in self:
                self[key] = factory()
                if default:
                    self[key].update(default)
            return self[key]

    return Groups(factory)
