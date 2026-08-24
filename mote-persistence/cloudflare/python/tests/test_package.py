import mote_persistence_cloudflare
from mote_persistence_cloudflare import Commit


def test_only_commit_is_public() -> None:
    assert mote_persistence_cloudflare.__all__ == ["Commit"]
    assert Commit.__name__ == "Commit"
