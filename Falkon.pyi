# Type stub for the `Falkon` module — the browser-plugin API injected into the
# Python interpreter by the Falkon web browser at runtime (there is no PyPI
# package). Lets a type checker resolve `import Falkon` in
# integrations/falkon/qeth_connector/ instead of treating it as Any. Covers
# only the surface the connector uses.
from typing import Any


class PluginInterface: ...


class ExternalJsObject:
    @staticmethod
    def registerExtraObject(name: str, obj: Any) -> None: ...

    @staticmethod
    def unregisterExtraObject(obj: Any) -> None: ...


class _Scripts:
    def find(self, name: str) -> list[Any]: ...
    def remove(self, script: Any) -> None: ...
    def insert(self, script: Any) -> None: ...


class _WebProfile:
    def scripts(self) -> _Scripts: ...


class MainApplication:
    @staticmethod
    def instance() -> "MainApplication | None": ...

    def webProfile(self) -> _WebProfile: ...


def registerPlugin(plugin: PluginInterface) -> None: ...
