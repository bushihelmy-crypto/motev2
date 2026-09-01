from mote_kernel.observability import ObservedNode
from mote_kernel.observability.record import Observation


class Port:
    def record(self, _observation: Observation, /) -> None:
        pass


ObservedNode(Port(), None)
