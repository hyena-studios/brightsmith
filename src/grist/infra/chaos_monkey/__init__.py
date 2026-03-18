"""Chaos monkey — schema-agnostic adversarial DQ testing.

Injects realistic data corruption into shadow copies of Iceberg tables
to stress-test DQ rule coverage. Works against any zone (raw, base,
consumable) by introspecting PyIceberg schemas and mapping column types
to appropriate corruption strategies.

Safety: Three-layer kill switch — CHAOS_MONKEY_ENABLED + GRIST_ENV=dev
+ shadow namespace validation. See safety.py.
"""

from grist.infra.chaos_monkey.injector import ChaosInjector, SchemaIntrospector
from grist.infra.chaos_monkey.manifest import ChaosManifest
from grist.infra.chaos_monkey.reconciler import AfterActionReconciler
from grist.infra.chaos_monkey.safety import SafetyGate

__all__ = [
    "ChaosInjector",
    "SchemaIntrospector",
    "ChaosManifest",
    "AfterActionReconciler",
    "SafetyGate",
]
