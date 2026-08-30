import pytest

import mote_kernel.execution.claim as claim_owner


def test_consumed_claim_receipt_cannot_be_constructed_directly() -> None:
    with pytest.raises(TypeError, match="issued only"):
        eval("ConsumedExecutionClaim(None, None, None, None)", dict(vars(claim_owner)))
