from __future__ import annotations

from catboost_floader.diagnostics.model_compare import DEFAULT_COMPARISON_FIELDS, build_comparison_rows
from catboost_floader.diagnostics.model_snapshot import build_model_snapshots

from catboost_floader.frontend_api.dto import ComparisonRowDTO


def get_model_comparison(
    *,
    model_keys: list[str] | None = None,
    fields: list[str] | None = None,
) -> list[ComparisonRowDTO]:
    fields = list(fields or DEFAULT_COMPARISON_FIELDS)
    rows = build_comparison_rows(
        build_model_snapshots(),
        model_keys=model_keys,
        fields=fields,
    )
    return [
        ComparisonRowDTO(
            model_key=str(row.get("model_key")),
            model_name=str(row.get("model_name")),
            fields={field: row.get(field) for field in fields},
        )
        for row in rows
    ]