from tests.agent.persistence_fixtures import AsyncGate

from mote_kernel.config import Config, ConfigContractError, ConfigSnapshot, ConfigSnapshotKey
from mote_kernel.loop.config import ReActRuntimeConfig


class ConfigCatalog:
    def __init__(self, snapshots: tuple[ConfigSnapshot, ...] = ()) -> None:
        self.snapshots = {snapshot.key: snapshot for snapshot in snapshots}
        self.loads: list[ConfigSnapshotKey] = []
        self.saves: list[ConfigSnapshot] = []
        self.resolutions: list[ConfigSnapshot] = []
        self.load_gate: AsyncGate | None = None
        self.resolve_gate: AsyncGate | None = None

    async def load(self, key: ConfigSnapshotKey, /) -> ConfigSnapshot:
        self.loads.append(key)
        if self.load_gate is not None:
            await self.load_gate.wait()
        try:
            return self.snapshots[key]
        except KeyError as error:
            raise ConfigContractError("the exact Config snapshot is unavailable") from error

    async def save(self, snapshot: ConfigSnapshot, /) -> ConfigSnapshot:
        self.saves.append(snapshot)
        previous = self.snapshots.get(snapshot.key)
        if previous is not None and previous != snapshot:
            raise ConfigContractError("immutable Config key already names different content")
        self.snapshots[snapshot.key] = snapshot
        return snapshot

    async def resolve(self, snapshot: ConfigSnapshot, /) -> Config:
        self.resolutions.append(snapshot)
        if self.resolve_gate is not None:
            await self.resolve_gate.wait()
        projection = ReActRuntimeConfig(snapshot.key, snapshot.key.definition_id, snapshot.key.definition_version)
        return Config(snapshot, projection, projection, projection, projection, ())
