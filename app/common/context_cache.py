"""Typed mappings into the common immutable analytical-context cache."""

from collections import defaultdict
from collections.abc import ItemsView, Iterator, MutableMapping, ValuesView
from typing import Any, Protocol
from weakref import finalize

from pydantic import BaseModel


class ContextCacheClient(Protocol):
    def call(self, operation: str, *args: object) -> Any: ...  # noqa: ANN401
    def view(self, capacity: int = 2_000) -> str: ...
    def persistent_view(self, scope: str, capacity: int = 2_000) -> str: ...
    def close_view(self, view: str) -> None: ...


_backend: ContextCacheClient | None = None


def set_context_cache(client: ContextCacheClient) -> None:
    global _backend
    _backend = client


class CachedContexts[Key: str, Model: BaseModel](MutableMapping[Key, Model]):
    def __init__(
        self,
        model: type[Model],
        client: ContextCacheClient,
        key_type: type[Key],
        scope: str | None = None,
    ) -> None:
        self._model = model
        self._client = client
        self._key_type = key_type
        self._view = client.persistent_view(scope) if scope is not None else client.view()
        if scope is None:
            cleanup = finalize(self, client.close_view, self._view)
            cleanup.atexit = False  # The process lease handles interpreter shutdown.

    def __getitem__(self, key: Key) -> Model:
        value = self._client.call("get", self._view, key)
        if value is None:
            raise KeyError(key)
        return self._model.model_validate_json(value)

    def __setitem__(self, key: Key, value: Model) -> None:
        self._client.call(
            "put", self._view, key, value.model_dump_json(exclude_computed_fields=True)
        )

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


def context_store[Model: BaseModel](
    model: type[Model],
    *,
    scope: str | None = None,
) -> MutableMapping[str, Model]:
    client = _backend
    return CachedContexts(model, client, str, scope) if client is not None else {}


def grouped_context_store[Key: str, Model: BaseModel](
    key_type: type[Key],
    model: type[Model],
    *,
    scope: str | None = None,
) -> defaultdict[str, MutableMapping[Key, Model]]:
    # Outer keys contain only lightweight mapping handles. Values live centrally.
    client = _backend
    groups_view = client.persistent_view(scope + ":groups") if client and scope else None

    def factory(group: str) -> MutableMapping[Key, Model]:
        if client is None:
            return {}
        group_scope = f"{scope}:{group}" if scope else None
        return CachedContexts(model, client, key_type, group_scope)

    class Groups(defaultdict[str, MutableMapping[Key, Model]]):
        def __missing__(self, key: str) -> MutableMapping[Key, Model]:
            value = factory(key)
            if client is not None and groups_view is not None:
                client.call("put", groups_view, key, "{}")
            self[key] = value
            return value

        def setdefault(
            self, key: str, default: MutableMapping[Key, Model] | None = None
        ) -> MutableMapping[Key, Model]:
            if key not in self:
                self[key] = self.__missing__(key)
                if default:
                    self[key].update(default)
            return self[key]

    result = Groups()
    if client is not None and groups_view is not None:
        for group in client.call("keys", groups_view):
            result[group] = factory(group)
    return result
