# ADR 0001: Engine-owned strategy adapters

Status: accepted, 2026-08-09.

## Context

The central assembly selected implementations correctly, but it also knew the private constructor
options and rule keys of several engines. Adding a rule or implementation required modifying a
large cross-engine file. Requiring every `EngineSlot` for every historical definition also meant a
new logical engine would invalidate immutable rollback definitions unless another version-specific
exception was added.

## Decision

Keep a root `EngineRegistry` as the composition boundary. Each registration owns implementation
factories, eager strategy validation/configuration callbacks, and the MarketBot definition version
from which the slot is required. Rule-key interpretation lives in the engine package. The assembly
provides generic `resolve_strategy(slot, ...)` and `build(slot, ...)` operations and retains typed
methods only for compatibility. Runtime inputs such as LONG portfolio allocations are supplied to
the resolver; artifact parsing and policy construction remain owned by the engine adapter.

## Consequences

- Business-rule changes no longer modify the assembly.
- Unsupported implementations and malformed artifacts still fail before processes start.
- New engine slots do not invalidate definitions released before their `required_since` version.
- Adding a logical engine still requires an explicit root catalog and runtime-topology decision;
  process lifecycle and universe semantics are intentionally not inferred from imports.
