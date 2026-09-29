"""data_provenance (V2 rule 9): every dataset, model output, chart and table says what it is built from.

    real          real data, above the readiness threshold
    real_partial  real data, but below the readiness threshold (too little history to trust)
    synthetic     any synthetic input at all

Combining inputs takes the WORST provenance: one synthetic input makes the result synthetic.
"""
REAL = "real"
REAL_PARTIAL = "real_partial"
SYNTHETIC = "synthetic"
PROVENANCES = (REAL, REAL_PARTIAL, SYNTHETIC)
_RANK = {REAL: 0, REAL_PARTIAL: 1, SYNTHETIC: 2}

LABEL = {
    SYNTHETIC: "SYNTHETIC — METHODOLOGY DEMO, NOT A REAL RESULT",
    REAL_PARTIAL: "REAL — LIMITED HISTORY",
    REAL: "REAL",
}


def check(p: str) -> str:
    if p not in _RANK:
        raise ValueError(f"data_provenance must be one of {PROVENANCES}, got {p!r}")
    return p


def worst(*ps: str) -> str:
    ps = [check(p) for p in ps if p is not None]
    if not ps:
        raise ValueError("worst() needs at least one provenance")
    return max(ps, key=_RANK.__getitem__)


def from_synthetic_flag(is_synthetic: bool, ready: bool = True) -> str:
    if is_synthetic:
        return SYNTHETIC
    return REAL if ready else REAL_PARTIAL
