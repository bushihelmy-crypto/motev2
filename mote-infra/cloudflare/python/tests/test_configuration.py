import json
from pathlib import Path
from typing import TypedDict, cast


class _DurableObjectBinding(TypedDict):
    name: str
    class_name: str


class _DurableObjects(TypedDict):
    bindings: list[_DurableObjectBinding]


class _DurableObjectExport(TypedDict):
    type: str
    storage: str


class _Exports(TypedDict):
    AgentDurableObject: _DurableObjectExport


class _WranglerConfig(TypedDict):
    main: str
    compatibility_flags: list[str]
    durable_objects: _DurableObjects
    exports: _Exports


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_wrangler_config() -> _WranglerConfig:
    content = (PROJECT_ROOT / "wrangler.jsonc").read_text(encoding="utf-8")
    return cast(_WranglerConfig, json.loads(content))


def test_declares_python_sqlite_durable_object() -> None:
    config = _load_wrangler_config()

    assert config["main"] == "src/mote_infra_cloudflare/entry.py"
    assert "python_workers" in config["compatibility_flags"]
    assert config["durable_objects"]["bindings"] == [
        {
            "name": "AGENT_OBJECTS",
            "class_name": "AgentDurableObject",
        }
    ]
    assert config["exports"]["AgentDurableObject"] == {
        "type": "durable-object",
        "storage": "sqlite",
    }


def test_uses_declarative_exports_instead_of_legacy_migrations() -> None:
    config_path = PROJECT_ROOT / "wrangler.jsonc"
    raw_config = cast(dict[str, object], json.loads(config_path.read_text(encoding="utf-8")))

    assert "migrations" not in raw_config
