"""Public API for deterministic in-process strategy compilation and execution."""

from .compiler import StrategyCompiler
from .errors import CompileError, StrategyLoadError, StrategyRuntimeError
from .loader import load_strategy_yaml
from .models import CompiledNode, CompiledPlan, DynamicOutputBinding, ExecutionResult
from .ports import AuditSink, RegistrySnapshotPort, RuleProviderPort, RuleRegistrationPort
from .runtime import StrategyRuntime
from .subprocess_runner import SubprocessRuleRunner

__all__ = [
    "AuditSink",
    "CompileError",
    "CompiledNode",
    "CompiledPlan",
    "DynamicOutputBinding",
    "ExecutionResult",
    "RegistrySnapshotPort",
    "RuleProviderPort",
    "RuleRegistrationPort",
    "StrategyCompiler",
    "StrategyLoadError",
    "StrategyRuntime",
    "StrategyRuntimeError",
    "SubprocessRuleRunner",
    "load_strategy_yaml",
]
