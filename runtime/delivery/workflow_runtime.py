"""Strict adapter selection; existing brainstorm schemas retain their meaning."""
from pathlib import Path

try:
    from . import delivery_core, phase_state, utility_state
except ImportError:
    import delivery_core
    import phase_state
    import utility_state


def engine(session):
    version = session.get("schema_version") if isinstance(session, dict) else None
    if version == utility_state.SESSION_SCHEMA:
        return utility_state
    if version == "devforge.brainstorm-session/v1":
        return phase_state
    delivery_core._fail("unsupported workflow session schema")


def engine_for_state(state):
    raw = phase_state._external(Path(state) / "MANIFEST.json", phase_state.STATE_JSON_LIMIT, "workflow manifest")
    value = delivery_core._json(raw, "workflow manifest")
    version = value.get("schema_version") if isinstance(value, dict) else None
    if version == utility_state.STATE_SCHEMA:
        return utility_state
    if version == "devforge.brainstorm-state/v1":
        return phase_state
    delivery_core._fail("unsupported workflow state schema")


def load_delivery(path):
    raw = phase_state._external(Path(path), delivery_core.CONTRACT_LIMIT, "selected delivery contract")
    value = delivery_core._json(raw, "selected delivery contract")
    if isinstance(value, dict) and value.get("schema_version") == utility_state.DELIVERY_SCHEMA:
        return utility_state._load_contract(Path(path))
    return delivery_core._load_contract(Path(path))


def protected_paths(contract):
    if contract.get("schema_version") == utility_state.DELIVERY_SCHEMA:
        return utility_state.protected_paths(contract)
    return delivery_core.catalog_paths(contract)


def start(contract, state):
    raw = phase_state._external(Path(contract), phase_state.SESSION_LIMIT, "selected session")
    return engine(delivery_core._json(raw, "selected session")).start(contract, state)
