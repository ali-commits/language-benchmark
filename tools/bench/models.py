"""Validated campaign identities shared by tooling; no application imports."""
from dataclasses import dataclass, field

@dataclass
class RuntimeCell:
    id: str
    python: str
    app: str = 'baseline'
    build: str = 'standard'
    adapter: str = 'async'
    workers: int = 2
    threads: int = 16
    pool_budget: int = 20
    jit: bool = False
    gil: bool = True
    supported: bool = True
    reasons: list[str] = field(default_factory=list)
    dependency_lock: str = ''
    image: str = ''

    def __post_init__(self):
        if self.app not in ('baseline','modern') or self.build not in ('standard','jit','free-threaded'):
            raise ValueError('unknown application/build')
        if self.workers not in (1,2) or self.pool_budget != 20 or self.threads != 16:
            raise ValueError('uncontrolled resource settings')
        if self.adapter not in ('async','threaded'):
            raise ValueError('unknown adapter')
        if self.adapter == 'threaded' and (self.app != 'modern' or self.workers != 1):
            raise ValueError('threaded comparison requires modern one-process configuration')
        if not self.supported and not self.reasons:
            raise ValueError('unsupported cells require reasons')
