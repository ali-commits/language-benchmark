"""Untimed standalone engine probe; never evidence that an API handler was compiled."""

JIT_PROBE = """
import sys,json
jit=getattr(sys,'_jit',None)
observed=False
if jit and jit.is_enabled():
    total=0
    for i in range(100000):
        total+=i
        observed=jit.is_active() or observed
print(json.dumps({'available':bool(jit and jit.is_available()),'enabled':bool(jit and jit.is_enabled()),'observed_active':observed,'scope':'standalone untimed engine probe; API compilation not established'}))
"""
