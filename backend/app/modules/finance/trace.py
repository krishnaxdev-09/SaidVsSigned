"""Calculation traces — doc 06 §7 (T3.1). Persisted, immutable (insert only); every input comes from the factory."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.finance.formulas import Result
from app.modules.finance.sourced import Sourced, require


async def record(conn: AsyncConnection, session_id: uuid.UUID, result: Result, inputs: dict[str, Sourced], *,
                 tolerance_row: str, enabled: bool) -> uuid.UUID:
    """`enabled=False` marks a shadow/hidden run (finance not user-visible before HC-5, gated formulas always)."""
    for k, v in inputs.items():
        require(v, k)
    cid = uuid.uuid4()
    await conn.execute(text(
        "INSERT INTO calculations (id, session_id, formula_id, formula_version, inputs, outputs, assumptions, missing, "
        "tolerance_row, enabled) VALUES (:id, :s, :f, :v, CAST(:i AS jsonb), CAST(:o AS jsonb), :a, :m, :t, :e)"),
        {"id": cid, "s": session_id, "f": result.formula_id, "v": result.formula_version,
         "i": json.dumps({k: json.loads(v.model_dump_json()) for k, v in inputs.items()}),
         "o": json.dumps({k: str(v) for k, v in result.outputs.items()}), "a": result.assumptions,
         "m": result.missing, "t": tolerance_row, "e": enabled})
    return cid


def outputs_of(row_outputs: dict[str, str]) -> dict[str, Decimal]:
    return {k: Decimal(v) for k, v in row_outputs.items()}
