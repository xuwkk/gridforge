"""
Streamlit app for interactive GridForge configuration authoring.

Run:
    gridforge-app
or:
    streamlit run gridforge/config_app.py
"""

from __future__ import annotations

from typing import Any, Dict, List
import os
import sys
import copy
import tempfile
from pathlib import Path
import yaml
import streamlit as st
import numpy as np
import pandas as pd

if not hasattr(np, "asscalar"):
    np.asscalar = lambda a: a.item()

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(PACKAGE_DIR)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from gridforge.construct import (
    METADATA_SHEET_NAME,
    SHEET_COLUMNS,
    _load_base_pypower_case,
    construct_grid_config,
)


CORE_SHEETS = ["bus", "gen", "branch"]
ALLOWED_FORMATS = ["absolute", "relative"]
ALLOWED_MAP_BY = ["row", "bus_idx"]
ALLOWED_AGGREGATES = ["max", "min", "mean", "sum"]
RELATIVE_MODE_OPTIONS = ALLOWED_MAP_BY + ALLOWED_AGGREGATES
BUS_TYPE_OPTIONS = ["1", "2", "3", "4", "positive_pd"]
SPECIAL_COLUMNS = {"BUS_IDX", "STATUS"}
BUS_TYPE_LABELS = {
    "1": "PQ buses (1)",
    "2": "PV buses (2)",
    "3": "Slack buses (3)",
    "4": "Positive load buses (4)",
    "positive_pd": "Positive load buses",
}


def _without_metadata_sheet(sheet_map: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    return {
        sheet_name: sheet_df
        for sheet_name, sheet_df in sheet_map.items()
        if sheet_name != METADATA_SHEET_NAME
    }

DEFAULT_COLUMNS: Dict[str, List[str]] = {
    "bus": ["BUS_IDX", "BUS_TYPE", "PD", "QD", "GS", "BS", "BUS_AREA", "VM", "VA", "BASEKV", "ZONE", "VMAX", "VMIN"],
    "gen": [
        "BUS_IDX", "PG", "QG", "QMAX", "QMIN", "VG", "MBASE", "STATUS", "PMAX", "PMIN",
        "COST_MODEL", "COST_STARTUP", "COST_SHUTDOWN", "COST_ORDER",
        "COST_SECOND", "COST_FIRST", "COST_ZERO",
    ],
    "branch": ["F_BUS_IDX", "T_BUS_IDX", "BR_R", "BR_X", "BR_B", "RATE_A", "RATE_B", "RATE_C", "TAP", "SHIFT", "STATUS", "ANGMIN", "ANGMAX"],
}

GENCOST_COLUMN_MAP = {
    "MODEL": "COST_MODEL",
    "STARTUP": "COST_STARTUP",
    "SHUTDOWN": "COST_SHUTDOWN",
    "ORDER": "COST_ORDER",
    "SECOND": "COST_SECOND",
    "FIRST": "COST_FIRST",
    "ZERO": "COST_ZERO",
}


def _normalize_field_name(name: Any) -> str:
    return str(name).strip().upper()


def _normalize_filter_mapping_keys(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    return {_normalize_field_name(k): v for k, v in raw.items()}


def _normalize_loaded_cfg_columns(raw_cfg: Dict[str, Any]) -> Dict[str, Any]:
    grid_cfg = raw_cfg.get("grid_config", {})
    if not isinstance(grid_cfg, dict):
        return raw_cfg

    normalized_grid_cfg: Dict[str, Any] = {}
    for sheet_name, sheet_cfg in grid_cfg.items():
        if not isinstance(sheet_cfg, dict):
            normalized_grid_cfg[sheet_name] = sheet_cfg
            continue

        normalized_sheet_cfg: Dict[str, Any] = {}
        for col_name, col_cfg in sheet_cfg.items():
            normalized_col_name = _normalize_field_name(col_name)
            next_col_cfg = copy.deepcopy(col_cfg)
            if isinstance(next_col_cfg, dict):
                rel_cfg = next_col_cfg.get("relative_to")
                if isinstance(rel_cfg, dict):
                    if "column" in rel_cfg:
                        rel_cfg["column"] = _normalize_field_name(rel_cfg["column"])
                    if "map_by" in rel_cfg:
                        rel_cfg["map_by"] = str(rel_cfg["map_by"]).strip().lower()
                    if "aggregate" in rel_cfg:
                        rel_cfg["aggregate"] = str(rel_cfg["aggregate"]).strip().lower()
                normalized_sheet_cfg[normalized_col_name] = next_col_cfg
        normalized_grid_cfg[sheet_name] = normalized_sheet_cfg

    raw_cfg["grid_config"] = normalized_grid_cfg

    rescale_cfg = raw_cfg.get("rescale", [])
    if isinstance(rescale_cfg, list):
        normalized_rescale = []
        for rule_cfg in rescale_cfg:
            next_rule = copy.deepcopy(rule_cfg)
            if isinstance(next_rule, dict):
                target_cfg = next_rule.get("target")
                if isinstance(target_cfg, dict):
                    if "column" in target_cfg:
                        target_cfg["column"] = _normalize_field_name(target_cfg["column"])
                    if "filter" in target_cfg:
                        target_cfg["filter"] = _normalize_filter_mapping_keys(target_cfg["filter"])
                sources_cfg = next_rule.get("sources")
                if isinstance(sources_cfg, list):
                    normalized_sources = []
                    for src_cfg in sources_cfg:
                        next_src = copy.deepcopy(src_cfg)
                        if isinstance(next_src, dict):
                            if "column" in next_src:
                                next_src["column"] = _normalize_field_name(next_src["column"])
                            if "filter" in next_src:
                                next_src["filter"] = _normalize_filter_mapping_keys(next_src["filter"])
                        normalized_sources.append(next_src)
                    next_rule["sources"] = normalized_sources
            normalized_rescale.append(next_rule)
        raw_cfg["rescale"] = normalized_rescale

    return raw_cfg


def _default_state() -> Dict[str, Any]:
    return {
        "super_config": {
            "pypower_case_name": "case14",
            "baseMVA": 100,
        },
        "grid_config": {k: {} for k in CORE_SHEETS},
        "rescale": [],
    }


def _parse_value_list(raw: str) -> List[Any]:
    text = raw.strip()
    if text == "":
        raise ValueError("Value cannot be empty.")
    if text.startswith("["):
        parsed = yaml.safe_load(text)
        if not isinstance(parsed, list):
            raise ValueError("Value must parse to a list.")
        return parsed

    parts = [p.strip() for p in text.split(",") if p.strip() != ""]
    values: List[Any] = []
    for p in parts:
        lowered = p.lower()
        if lowered in {"true", "false"}:
            values.append(lowered == "true")
            continue
        try:
            if "." in p or "e" in lowered:
                values.append(float(p))
            else:
                values.append(int(p))
            continue
        except ValueError:
            pass
        values.append(p)
    return values


def _get_known_columns(cfg: Dict[str, Any], sheet_name: str) -> List[str]:
    if sheet_name in DEFAULT_COLUMNS:
        base_cols = list(DEFAULT_COLUMNS[sheet_name])
    else:
        base_cols = []
    custom_cols = list(cfg["grid_config"].get(sheet_name, {}).keys())
    merged = []
    for c in base_cols + custom_cols:
        if c not in merged:
            merged.append(c)
    return merged


def _sheet_likely_has_bus_idx(cfg: Dict[str, Any], sheet_name: str) -> bool:
    known_cols = set(_get_known_columns(cfg, sheet_name))
    return "BUS_IDX" in known_cols


def _is_custom_sheet(cfg: Dict[str, Any], sheet_name: str) -> bool:
    return sheet_name in cfg.get("grid_config", {}) and sheet_name not in CORE_SHEETS


def _format_bus_type_token(token: Any) -> str:
    key = str(token).strip()
    return BUS_TYPE_LABELS.get(key, key)


def _format_bus_idx_summary(sheet_name: str, bus_idx_cfg: Dict[str, Any]) -> str:
    mode = str(bus_idx_cfg.get("format", "absolute")).strip().lower()
    values = bus_idx_cfg.get("value", [])
    group_val = str(bus_idx_cfg.get("group", "")).strip()
    remove_gen = bool(bus_idx_cfg.get("remove_gen", False))

    if mode == "absolute":
        summary = f"Place `{sheet_name}` on explicit buses `{values}`."
    else:
        rel_cfg = bus_idx_cfg.get("relative_to", {})
        bus_types = rel_cfg.get("bus_type", []) if isinstance(rel_cfg, dict) else []
        bus_type_text = ", ".join(_format_bus_type_token(token) for token in bus_types) if bus_types else "no bus pool selected"
        ratio = values[0] if isinstance(values, list) and len(values) > 0 else "?"
        summary = f"Select `{ratio}` of candidate buses from `{bus_type_text}` without replacement."

    if group_val:
        summary += f" Prevent overlap with group `{group_val}`."
    if remove_gen:
        summary += " Remove matching generator rows on selected buses."
    return summary


def _get_preview_sheet(sheet_name: str) -> pd.DataFrame | None:
    draft_preview = st.session_state.get("draft_grid_preview", None)
    if isinstance(draft_preview, dict):
        sheet_df = draft_preview.get(sheet_name)
        if isinstance(sheet_df, pd.DataFrame):
            return sheet_df

    case_preview = st.session_state.get("case_preview", None)
    if isinstance(case_preview, dict):
        sheet_df = case_preview.get(sheet_name)
        if isinstance(sheet_df, pd.DataFrame):
            return sheet_df
    return None


def _get_known_bus_ids() -> List[int]:
    bus_df = _get_preview_sheet("bus")
    if bus_df is None or "BUS_IDX" not in bus_df.columns:
        return []
    bus_vals = pd.to_numeric(bus_df["BUS_IDX"], errors="coerce").dropna().astype(int).tolist()
    return sorted(set(bus_vals))


def _get_known_gen_bus_ids() -> List[int]:
    gen_df = _get_preview_sheet("gen")
    if gen_df is None or "BUS_IDX" not in gen_df.columns:
        return []
    bus_vals = pd.to_numeric(gen_df["BUS_IDX"], errors="coerce").dropna().astype(int).tolist()
    return sorted(set(bus_vals))


def _has_bus_preview() -> bool:
    bus_df = _get_preview_sheet("bus")
    return bus_df is not None and "BUS_IDX" in bus_df.columns


def _get_candidate_bus_ids(bus_type_tokens: List[Any]) -> List[int]:
    bus_df = _get_preview_sheet("bus")
    if bus_df is None or "BUS_IDX" not in bus_df.columns:
        return []

    bus_idx_series = pd.to_numeric(bus_df["BUS_IDX"], errors="coerce")
    mask = pd.Series(False, index=bus_df.index)
    for token in bus_type_tokens:
        token_str = str(token).strip().lower()
        if token_str in {"4", "positive_pd", "pd_positive"}:
            if "PD" in bus_df.columns:
                pd_series = pd.to_numeric(bus_df["PD"], errors="coerce").fillna(0.0)
                mask = mask | (pd_series > 0)
        elif token_str in {"1", "2", "3"}:
            if "BUS_TYPE" in bus_df.columns:
                bus_type_series = pd.to_numeric(bus_df["BUS_TYPE"], errors="coerce")
                mask = mask | (bus_type_series == int(token_str))
    candidate_vals = bus_idx_series[mask].dropna().astype(int).tolist()
    return sorted(set(candidate_vals))


def _estimate_relative_selection_count(values: Any, pool_size: int) -> int | None:
    if not isinstance(values, list) or len(values) == 0 or pool_size <= 0:
        return None
    try:
        ratio = float(values[0])
    except (TypeError, ValueError):
        return None
    return max(1, int(ratio * pool_size))


def _validate_loaded_cfg(raw_cfg: Any) -> Dict[str, Any]:
    if not isinstance(raw_cfg, dict):
        raise ValueError("Top-level YAML must be a dictionary.")
    if "super_config" not in raw_cfg or not isinstance(raw_cfg["super_config"], dict):
        raise ValueError("YAML must contain `super_config` as a dictionary.")
    if "grid_config" not in raw_cfg or not isinstance(raw_cfg["grid_config"], dict):
        raise ValueError("YAML must contain `grid_config` as a dictionary.")
    if "rescale" not in raw_cfg:
        raw_cfg["rescale"] = []
    if not isinstance(raw_cfg["rescale"], list):
        raise ValueError("`rescale` must be a list.")
    return _normalize_loaded_cfg_columns(raw_cfg)


def _show_extra_guidance() -> bool:
    return bool(st.session_state.get("show_extra_guidance", True))


def _show_formula_captions() -> bool:
    return bool(st.session_state.get("show_formula_captions", True))


def _reset_builder_widget_state() -> None:
    """Clear sheet/editor widget state so loaded YAML repopulates the UI cleanly."""
    prefixes = (
        "new_col::",
        "fmt::",
        "value::",
        "rel_sheet::",
        "rel_col_choice::",
        "rel_col_manual::",
        "relative_mode::",
        "random_enabled::",
        "random_ratio::",
        "bus_idx_mode::",
        "bus_idx_absolute_select::",
        "bus_idx_value::",
        "bus_idx_bus_type::",
        "bus_idx_group::",
        "bus_idx_remove_gen::",
        "status_mode::",
        "status_value_mode::",
        "status_value::",
        "rescale_name::",
        "rescale_remove::",
        "rescale_target_sheet::",
        "rescale_target_col::",
        "rescale_target_agg::",
        "rescale_target_filter::",
        "rescale_ratio::",
        "rescale_add_source::",
        "rescale_remove_source::",
        "rescale_src_sheet::",
        "rescale_src_col::",
        "rescale_src_agg::",
        "rescale_src_filter::",
    )
    exact_keys = (
        "new_sheet_name",
        "draft_grid_preview",
        "draft_grid_preview_seed",
    )
    for key in list(st.session_state.keys()):
        if key in exact_keys or key.startswith(prefixes):
            del st.session_state[key]


def _load_pypower_case_preview(case_name: str) -> Dict[str, pd.DataFrame]:
    ppc = _load_base_pypower_case(case_name, base_dir=Path.cwd())
    preview: Dict[str, pd.DataFrame] = {}
    for sheet_name in ["bus", "gen", "branch"]:
        if sheet_name not in ppc:
            continue
        arr = ppc[sheet_name]
        cols = SHEET_COLUMNS[sheet_name]
        preview[sheet_name] = pd.DataFrame(arr[:, : len(cols)], columns=cols)
    if "gen" in preview and "gencost" in ppc:
        cost_cols = list(GENCOST_COLUMN_MAP.keys())
        cost_df = pd.DataFrame(
            ppc["gencost"][:, : len(cost_cols)],
            columns=cost_cols,
        ).rename(columns=GENCOST_COLUMN_MAP)
        if len(preview["gen"]) != len(cost_df):
            raise ValueError(
                f"Cannot merge gencost preview into gen: {len(preview['gen'])} != {len(cost_df)} rows."
            )
        preview["gen"] = pd.concat(
            [preview["gen"].reset_index(drop=True), cost_df.reset_index(drop=True)],
            axis=1,
        )
    return preview


def _build_topology_figure_from_xlsx(grid_xlsx_path: str, seed: int = 42):
    try:
        import networkx as nx
        import plotly.graph_objects as go
    except ImportError as e:
        raise ImportError(
            "Live topology preview requires networkx and plotly. Install with `pip install networkx plotly`."
        ) from e

    xls = pd.ExcelFile(grid_xlsx_path)
    bus_df = pd.read_excel(grid_xlsx_path, sheet_name="bus")
    branch_df = pd.read_excel(grid_xlsx_path, sheet_name="branch")
    required_bus_cols = {"BUS_IDX", "BUS_TYPE"}
    required_branch_cols = {"F_BUS_IDX", "T_BUS_IDX"}
    if not required_bus_cols.issubset(set(bus_df.columns)):
        missing = sorted(list(required_bus_cols - set(bus_df.columns)))
        raise ValueError(f"bus sheet missing required columns: {missing}")
    if not required_branch_cols.issubset(set(branch_df.columns)):
        missing = sorted(list(required_branch_cols - set(branch_df.columns)))
        raise ValueError(f"branch sheet missing required columns: {missing}")

    graph = nx.Graph()
    for _, row in bus_df.iterrows():
        graph.add_node(int(row["BUS_IDX"]), bus_type=int(row["BUS_TYPE"]))
    for _, row in branch_df.iterrows():
        graph.add_edge(int(row["F_BUS_IDX"]), int(row["T_BUS_IDX"]))

    pos = nx.spring_layout(graph, seed=seed)
    bus_asset_summary: Dict[int, Dict[str, Dict[str, int]]] = {int(b): {} for b in graph.nodes}
    for sheet_name in xls.sheet_names:
        if sheet_name in {"bus", "branch", METADATA_SHEET_NAME}:
            continue
        df = xls.parse(sheet_name)
        if df.empty or "BUS_IDX" not in df.columns:
            continue
        status_col = "STATUS" if "STATUS" in df.columns else None
        bus_vals = pd.to_numeric(df["BUS_IDX"], errors="coerce")
        for row_idx, bus_val in bus_vals.items():
            if pd.isna(bus_val):
                continue
            bus_idx = int(bus_val)
            if bus_idx not in bus_asset_summary:
                continue
            status_text = "N/A"
            if status_col is not None:
                raw_status = df.at[row_idx, status_col]
                if pd.notna(raw_status):
                    try:
                        s_float = float(raw_status)
                        status_text = str(int(s_float)) if s_float.is_integer() else str(s_float)
                    except (TypeError, ValueError):
                        status_text = str(raw_status)
            bus_asset_summary[bus_idx].setdefault(sheet_name, {})
            bus_asset_summary[bus_idx][sheet_name][status_text] = (
                bus_asset_summary[bus_idx][sheet_name].get(status_text, 0) + 1
            )

    edge_x: List[float] = []
    edge_y: List[float] = []
    for i, j in graph.edges():
        x0, y0 = pos[i]
        x1, y1 = pos[j]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])

    color_by_type = {1: "lightblue", 2: "orange", 3: "red"}
    label_by_type = {1: "PQ", 2: "PV", 3: "Slack"}
    node_x = []
    node_y = []
    node_text = []
    node_color = []
    node_ids = list(graph.nodes())
    for bus_idx in node_ids:
        x, y = pos[bus_idx]
        node_x.append(x)
        node_y.append(y)
        bus_type = int(graph.nodes[bus_idx]["bus_type"])
        node_color.append(color_by_type.get(bus_type, "gray"))
        assets = bus_asset_summary.get(int(bus_idx), {})
        if len(assets) == 0:
            assets_txt = "None"
        else:
            parts = []
            for asset_name in sorted(assets.keys()):
                status_map = assets[asset_name]
                status_txt = ", ".join([f"{k}x{v}" for k, v in sorted(status_map.items())])
                parts.append(f"{asset_name}(status={status_txt})")
            assets_txt = ", ".join(parts)
        node_text.append(
            f"Bus: {bus_idx}<br>Bus type: {label_by_type.get(bus_type, 'Other')}<br>Assets: {assets_txt}"
        )

    edge_trace = go.Scatter(
        x=edge_x,
        y=edge_y,
        mode="lines",
        line=dict(width=1, color="#888"),
        hoverinfo="skip",
        showlegend=False,
    )
    node_trace = go.Scatter(
        x=node_x,
        y=node_y,
        mode="markers+text",
        text=[str(i) for i in node_ids],
        textposition="top center",
        hovertemplate="%{customdata}<extra></extra>",
        customdata=node_text,
        marker=dict(size=14, color=node_color, line=dict(width=1, color="black")),
        showlegend=False,
    )
    fig = go.Figure(data=[edge_trace, node_trace])
    fig.update_layout(
        title="Live Topology Preview",
        template="simple_white",
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    return fig


def _format_column_formula(sheet_name: str, col_name: str, col_cfg: Dict[str, Any]) -> str:
    fmt = str(col_cfg.get("format", "absolute")).strip().lower()
    value = col_cfg.get("value", [])
    random_ratio = col_cfg.get("random_ratio", None)
    random_txt = ""
    if random_ratio is not None:
        random_txt = f" * (1 + {random_ratio} * U[-1,1])"

    if fmt == "absolute":
        return f"`{sheet_name}.{col_name} = value{random_txt}`"

    if fmt == "relative":
        rel = col_cfg.get("relative_to", {})
        if isinstance(rel, dict):
            ref_sheet = rel.get("sheet", "?")
            ref_col = rel.get("column", "?")
            map_by = rel.get("map_by")
            agg = rel.get("aggregate")
            if "bus_type" in rel and col_name == "BUS_IDX":
                return (
                    f"`{sheet_name}.{col_name} ~ sample(relative_to.bus_type={rel.get('bus_type')}, "
                    f"value={value}, without replacement)`"
                )
            if map_by is not None:
                return (
                    f"`{sheet_name}.{col_name} = value * base({ref_sheet}.{ref_col}, "
                    f"map_by={map_by}){random_txt}`"
                )
            if agg is not None:
                return (
                    f"`{sheet_name}.{col_name} = value * base({ref_sheet}.{ref_col}, "
                    f"aggregate={agg}){random_txt}`"
                )
            return (
                f"`{sheet_name}.{col_name} = value * base({ref_sheet}.{ref_col}){random_txt}`"
            )
        return f"`{sheet_name}.{col_name} = value * base(relative_to){random_txt}`"

    return f"`{sheet_name}.{col_name}` (unknown format)"


def _compact_value(value: Any, max_items: int = 6) -> str:
    if isinstance(value, list):
        shown = value[:max_items]
        suffix = ", ..." if len(value) > max_items else ""
        return "[" + ", ".join(str(v) for v in shown) + suffix + "]"
    return str(value)


def _describe_column_rule(sheet_name: str, col_name: str, col_cfg: Dict[str, Any]) -> str:
    fmt = str(col_cfg.get("format", "absolute")).strip().lower()
    value = _compact_value(col_cfg.get("value", []))
    random_ratio = col_cfg.get("random_ratio", None)
    random_txt = f", random +/-{float(random_ratio) * 100:.0f}%" if random_ratio is not None else ""

    if fmt == "absolute":
        return f"{col_name} = {value}{random_txt}"

    rel_cfg = col_cfg.get("relative_to", {})
    if not isinstance(rel_cfg, dict):
        return f"{col_name} is relative to an incomplete source{random_txt}"

    if col_name == "BUS_IDX":
        bus_types = rel_cfg.get("bus_type", [])
        bus_type_text = ", ".join(_format_bus_type_token(token) for token in bus_types) if bus_types else "candidate buses"
        return f"BUS_IDX selects {value} from {bus_type_text}"

    ref_sheet = rel_cfg.get("sheet", "?")
    ref_col = rel_cfg.get("column", "?")
    if "map_by" in rel_cfg:
        return f"{col_name} <- {value} * {ref_sheet}.{ref_col} by {rel_cfg.get('map_by')}{random_txt}"
    if "aggregate" in rel_cfg:
        return f"{col_name} <- {value} * {rel_cfg.get('aggregate')}({ref_sheet}.{ref_col}){random_txt}"
    return f"{col_name} <- {value} * {ref_sheet}.{ref_col}{random_txt}"


def _sheet_summary_lines(cfg: Dict[str, Any], sheet_name: str) -> List[str]:
    sheet_cfg = cfg.get("grid_config", {}).get(sheet_name, {})
    if not isinstance(sheet_cfg, dict):
        return ["Invalid sheet definition."]

    lines: List[str] = []
    if sheet_name in CORE_SHEETS:
        edited_cols = [col for col in sheet_cfg.keys() if col not in SPECIAL_COLUMNS]
        if edited_cols:
            lines.append(f"Edits {len(edited_cols)} field(s): {', '.join(edited_cols[:6])}{', ...' if len(edited_cols) > 6 else ''}.")
        else:
            lines.append("Uses the base PYPOWER sheet unchanged.")
    else:
        bus_idx_cfg = sheet_cfg.get("BUS_IDX")
        if isinstance(bus_idx_cfg, dict):
            lines.append(_format_bus_idx_summary(sheet_name, bus_idx_cfg))
        else:
            lines.append("Missing BUS_IDX placement.")
        if "STATUS" in sheet_cfg:
            lines.append(_describe_column_rule(sheet_name, "STATUS", sheet_cfg["STATUS"]))
        else:
            lines.append("STATUS auto-fills to 1.")

    for col_name, col_cfg in sheet_cfg.items():
        if col_name in SPECIAL_COLUMNS or not isinstance(col_cfg, dict):
            continue
        lines.append(_describe_column_rule(sheet_name, col_name, col_cfg))
    return lines


def _render_sheet_summary(cfg: Dict[str, Any], sheet_name: str) -> None:
    lines = _sheet_summary_lines(cfg, sheet_name)
    if not lines:
        return
    st.caption("Construction summary")
    for line in lines[:8]:
        st.markdown(f"- {line}")
    if len(lines) > 8:
        st.caption(f"{len(lines) - 8} more rule(s) hidden in this summary.")


def _render_config_overview(cfg: Dict[str, Any]) -> None:
    grid_cfg = cfg.get("grid_config", {})
    custom_sheets = [name for name in grid_cfg if name not in CORE_SHEETS]
    edited_core = sum(
        len([col for col in grid_cfg.get(name, {}) if col not in SPECIAL_COLUMNS])
        for name in CORE_SHEETS
    )
    rescale_count = len(cfg.get("rescale", [])) if isinstance(cfg.get("rescale", []), list) else 0
    metric_cols = st.columns(4)
    metric_cols[0].metric("Core sheets", len([name for name in CORE_SHEETS if name in grid_cfg]))
    metric_cols[1].metric("Core edits", edited_core)
    metric_cols[2].metric("Custom sheets", len(custom_sheets))
    metric_cols[3].metric("Rescale rules", rescale_count)

    summary_rows = []
    for sheet_name in grid_cfg:
        sheet_cfg = grid_cfg.get(sheet_name, {})
        rows = "base"
        if isinstance(sheet_cfg, dict) and "BUS_IDX" in sheet_cfg:
            bus_idx_cfg = sheet_cfg.get("BUS_IDX", {})
            if isinstance(bus_idx_cfg, dict):
                values = bus_idx_cfg.get("value", [])
                rows = _compact_value(values, max_items=4)
        summary_rows.append(
            {
                "sheet": sheet_name,
                "kind": "core" if sheet_name in CORE_SHEETS else "custom",
                "placement": rows,
                "rules": len(sheet_cfg) if isinstance(sheet_cfg, dict) else 0,
            }
        )
    if summary_rows:
        st.dataframe(pd.DataFrame(summary_rows), width="stretch", hide_index=True, height=180)


def _render_excel_preview(xlsx_path: str, section_title: str) -> None:
    st.subheader(section_title)
    if not os.path.exists(xlsx_path):
        st.info(
            f"Excel file not found: `{xlsx_path}`. Construct once or use Draft Grid Preview to inspect current settings."
        )
        return
    try:
        all_sheets = _without_metadata_sheet(pd.read_excel(xlsx_path, sheet_name=None))
        if len(all_sheets) == 0:
            st.info("Excel has no sheets.")
            return
        tab_names = list(all_sheets.keys())
        tabs = st.tabs(tab_names)
        for tab, name in zip(tabs, tab_names):
            with tab:
                st.caption(f"Sheet: `{name}` ({len(all_sheets[name])} rows)")
                st.dataframe(
                    all_sheets[name],
                    width="stretch",
                    height=240,
                )
    except Exception as e:
        st.error(f"Failed to read excel preview: {e}")


def _build_sheet_preview_from_cfg(cfg: Dict[str, Any], random_seed: int = 404) -> Dict[str, pd.DataFrame]:
    yaml_path = None
    xlsx_path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as yaml_file:
            yaml.safe_dump(cfg, yaml_file, sort_keys=False)
            yaml_path = yaml_file.name
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_file:
            xlsx_path = xlsx_file.name
        construct_grid_config(yaml_path, xlsx_path, random_seed=random_seed)
        return _without_metadata_sheet(pd.read_excel(xlsx_path, sheet_name=None))
    finally:
        if yaml_path and os.path.exists(yaml_path):
            os.remove(yaml_path)
        if xlsx_path and os.path.exists(xlsx_path):
            os.remove(xlsx_path)


def _render_sheet_preview(sheet_map: Dict[str, pd.DataFrame], title: str) -> None:
    if not isinstance(sheet_map, dict) or len(sheet_map) == 0:
        return
    st.subheader(title)
    tab_names = list(sheet_map.keys())
    tabs = st.tabs(tab_names)
    for tab, name in zip(tabs, tab_names):
        with tab:
            st.caption(f"Sheet: `{name}` ({len(sheet_map[name])} rows)")
            st.dataframe(sheet_map[name], width="stretch", height=240)


def _render_field_editor_header() -> None:
    header_cols = st.columns([1.3, 1.1, 2.0, 1.3, 1.5, 1.2, 1.0, 1.1, 0.9])
    labels = ["Field", "Format", "Value", "Source Sheet", "Source Field", "Map / Aggregate", "Random", "Ratio", "Action"]
    for col, label in zip(header_cols, labels):
        col.caption(label)


def _format_rescale_formula(rule_cfg: Dict[str, Any], idx: int) -> str:
    name = str(rule_cfg.get("name", f"rule_{idx}"))
    target = rule_cfg.get("target", {})
    tgt_sheet = target.get("sheet", "?")
    tgt_col = target.get("column", "?")
    tgt_agg = target.get("aggregate", "sum")
    ratio = rule_cfg.get("ratio", "?")
    sources = rule_cfg.get("sources", [])
    source_terms = []
    if isinstance(sources, list):
        for src in sources:
            if isinstance(src, dict):
                source_terms.append(
                    f"{src.get('aggregate', 'sum')}({src.get('sheet', '?')}.{src.get('column', '?')})"
                )
    source_expr = " + ".join(source_terms) if len(source_terms) > 0 else "sources"
    return (
        f"`{name}`: rescale {tgt_agg}({tgt_sheet}.{tgt_col}) = "
        f"{ratio} * ({source_expr})"
    )


def _mapping_to_inline_yaml(value: Any) -> str:
    if not isinstance(value, dict):
        return "{}"
    return yaml.safe_dump(value, default_flow_style=True).strip()


def _parse_mapping_yaml(raw: str, field_name: str) -> Dict[str, Any]:
    text = str(raw).strip()
    if text == "":
        return {}
    parsed = yaml.safe_load(text)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a YAML dictionary.")
    return _normalize_filter_mapping_keys(parsed)


def _render_rescale_editor(cfg: Dict[str, Any]) -> None:
    cfg.setdefault("rescale", [])
    if not isinstance(cfg["rescale"], list):
        cfg["rescale"] = []

    st.subheader("Rescale Rules")
    if _show_extra_guidance():
        st.caption(
            "Rescale filter is current-sheet columns only. "
            "Example: `{STATUS: 1, ZONE: 1}` for the selected target/source sheet."
        )
        st.caption(
            "A rescale rule multiplies the selected target values so the chosen target aggregate "
            "matches `ratio * sum(source aggregates)`."
        )

    add_cols = st.columns([2, 1])
    add_cols[0].caption(f"Current rules: {len(cfg['rescale'])}")
    if add_cols[1].button("Add Rescale Rule"):
        cfg["rescale"].append(
            {
                "name": f"rule_{len(cfg['rescale'])}",
                "target": {"sheet": "load", "column": "PMAX", "aggregate": "sum"},
                "ratio": 1.0,
                "sources": [{"sheet": "gen", "column": "PMAX", "aggregate": "sum"}],
            }
        )
        st.rerun()

    for i, rule_cfg in enumerate(list(cfg["rescale"])):
        if not isinstance(rule_cfg, dict):
            cfg["rescale"][i] = {}
            rule_cfg = cfg["rescale"][i]

        target_cfg = rule_cfg.setdefault("target", {})
        target_cfg.pop("strict", None)
        sources = rule_cfg.setdefault("sources", [])
        if not isinstance(sources, list):
            sources = []
            rule_cfg["sources"] = sources

        with st.expander(f"Rule {i + 1}: {rule_cfg.get('name', f'rule_{i}')}", expanded=False):
            top_cols = st.columns([3, 1])
            rule_cfg["name"] = top_cols[0].text_input(
                "name (optional)",
                value=str(rule_cfg.get("name", f"rule_{i}")),
                key=f"rescale_name::{i}",
                help="Optional label used only for readability in the app and YAML.",
            )
            if top_cols[1].button("Remove Rule", key=f"rescale_remove::{i}"):
                del cfg["rescale"][i]
                st.rerun()

            st.markdown("**Target**")
            sheet_options = list(cfg.get("grid_config", {}).keys())
            if len(sheet_options) == 0:
                sheet_options = CORE_SHEETS.copy()
            target_sheet = str(target_cfg.get("sheet", sheet_options[0]))
            if target_sheet not in sheet_options:
                sheet_options.append(target_sheet)
            target_cfg["sheet"] = st.selectbox(
                "Target sheet",
                sheet_options,
                index=sheet_options.index(target_sheet),
                key=f"rescale_target_sheet::{i}",
                help="Sheet whose values will be scaled by this rule.",
            )
            target_cols = _get_known_columns(cfg, target_cfg["sheet"])
            if len(target_cols) == 0:
                target_cols = ["PMAX"]
            target_col = str(target_cfg.get("column", target_cols[0]))
            if target_col not in target_cols:
                target_cols.append(target_col)
            target_cfg["column"] = st.selectbox(
                "Target field",
                target_cols,
                index=target_cols.index(target_col),
                key=f"rescale_target_col::{i}",
                help="Column in the target sheet whose selected values will be multiplied.",
            )
            target_agg_options = ["sum", "mean", "min", "max"]
            target_agg = str(target_cfg.get("aggregate", "sum")).strip().lower()
            if target_agg not in target_agg_options:
                target_agg = "sum"
            target_cfg["aggregate"] = st.selectbox(
                "Target aggregate",
                target_agg_options,
                index=target_agg_options.index(target_agg),
                key=f"rescale_target_agg::{i}",
                help="Aggregate used to measure the selected target values before and after scaling.",
            )
            target_filter_raw = st.text_input(
                "Target filter",
                value=_mapping_to_inline_yaml(target_cfg.get("filter", {})),
                key=f"rescale_target_filter::{i}",
                help="YAML dict using current-sheet columns only. Example: {STATUS: 1, ZONE: 1}",
            )
            try:
                target_filter = _parse_mapping_yaml(target_filter_raw, "target.filter")
                if len(target_filter) == 0:
                    target_cfg.pop("filter", None)
                else:
                    target_cfg["filter"] = target_filter
            except Exception as e:
                st.error(f"target.filter parse error: {e}")

            st.markdown("**Rescale**")
            if _show_extra_guidance():
                st.caption("Rescale always scales the target aggregate to a ratio of the source aggregates.")
            rule_cfg["ratio"] = float(
                st.number_input(
                    "ratio",
                    value=float(rule_cfg.get("ratio", 1.0)),
                    step=0.05,
                    key=f"rescale_ratio::{i}",
                    help="Target aggregate after scaling = ratio * sum(source aggregates).",
                )
            )

            src_head_cols = st.columns([3, 1])
            src_head_cols[0].markdown("**Sources**")
            if src_head_cols[1].button("Add Source", key=f"rescale_add_source::{i}"):
                sources.append(
                    {
                        "sheet": "gen",
                        "column": "PMAX",
                        "aggregate": "sum",
                    }
                )
                st.rerun()

            for j, src_cfg in enumerate(list(sources)):
                if not isinstance(src_cfg, dict):
                    sources[j] = {}
                    src_cfg = sources[j]
                src_cfg.pop("strict", None)
                s_cols = st.columns([3, 1])
                s_cols[0].caption(f"Source {j + 1}")
                if s_cols[1].button("Remove", key=f"rescale_remove_source::{i}::{j}"):
                    del sources[j]
                    st.rerun()
                s_sheet_options = list(cfg.get("grid_config", {}).keys())
                if len(s_sheet_options) == 0:
                    s_sheet_options = CORE_SHEETS.copy()
                s_sheet = str(src_cfg.get("sheet", s_sheet_options[0]))
                if s_sheet not in s_sheet_options:
                    s_sheet_options.append(s_sheet)
                src_cfg["sheet"] = st.selectbox(
                    f"Source {j + 1} sheet",
                    s_sheet_options,
                    index=s_sheet_options.index(s_sheet),
                    key=f"rescale_src_sheet::{i}::{j}",
                    help="Source sheet that contributes to the rescale reference total.",
                )
                s_cols_known = _get_known_columns(cfg, src_cfg["sheet"])
                if len(s_cols_known) == 0:
                    s_cols_known = ["PMAX"]
                s_col = str(src_cfg.get("column", s_cols_known[0]))
                if s_col not in s_cols_known:
                    s_cols_known.append(s_col)
                src_cfg["column"] = st.selectbox(
                    f"Source {j + 1} field",
                    s_cols_known,
                    index=s_cols_known.index(s_col),
                    key=f"rescale_src_col::{i}::{j}",
                    help="Source column whose aggregate contributes to the rescale reference total.",
                )
                s_agg_options = ["sum", "mean", "min", "max"]
                s_agg = str(src_cfg.get("aggregate", "sum")).strip().lower()
                if s_agg not in s_agg_options:
                    s_agg = "sum"
                src_cfg["aggregate"] = st.selectbox(
                    f"Source {j + 1} aggregate",
                    s_agg_options,
                    index=s_agg_options.index(s_agg),
                    key=f"rescale_src_agg::{i}::{j}",
                    help="Aggregate computed on the selected source rows before summing across sources.",
                )
                src_filter_raw = st.text_input(
                    f"Source {j + 1} filter",
                    value=_mapping_to_inline_yaml(src_cfg.get("filter", {})),
                    key=f"rescale_src_filter::{i}::{j}",
                    help="YAML dict using current-sheet columns only. Example: {STATUS: 1}",
                )
                try:
                    src_filter = _parse_mapping_yaml(src_filter_raw, f"source[{j}].filter")
                    if len(src_filter) == 0:
                        src_cfg.pop("filter", None)
                    else:
                        src_cfg["filter"] = src_filter
                except Exception as e:
                    st.error(f"source[{j}].filter parse error: {e}")

            if _show_formula_captions():
                st.caption(_format_rescale_formula(rule_cfg, i))


def _render_column_editor(cfg: Dict[str, Any], sheet_name: str, col_name: str) -> None:
    col_cfg = cfg["grid_config"][sheet_name].setdefault(col_name, {"format": "absolute", "value": [0]})
    fmt_default = str(col_cfg.get("format", "absolute"))
    if fmt_default not in ALLOWED_FORMATS:
        fmt_default = "absolute"

    default_value_text = yaml.safe_dump(col_cfg.get("value", [0]), default_flow_style=True).strip()
    random_enabled_default = "random_ratio" in col_cfg

    row_cols = st.columns([1.3, 1.1, 2.0, 1.3, 1.5, 1.2, 1.0, 1.1, 0.9])
    row_cols[0].markdown(f"`{col_name}`")

    fmt = row_cols[1].selectbox(
        "format",
        ALLOWED_FORMATS,
        index=ALLOWED_FORMATS.index(fmt_default),
        key=f"fmt::{sheet_name}::{col_name}",
        label_visibility="collapsed",
        help="Use `absolute` to assign values directly, or `relative` to derive values from another sheet/column.",
    )
    col_cfg["format"] = fmt

    raw_values = row_cols[2].text_input(
        "value list",
        value=default_value_text,
        key=f"value::{sheet_name}::{col_name}",
        help="Examples: [1.0], [1, 2, 3], 1,2,3",
        label_visibility="collapsed",
    )
    try:
        col_cfg["value"] = _parse_value_list(raw_values)
    except Exception as e:
        st.error(f"{sheet_name}.{col_name} value parse error: {e}")

    if fmt == "relative":
        rel_cfg = col_cfg.setdefault("relative_to", {})
        sheet_options = list(cfg["grid_config"].keys())
        if sheet_name not in sheet_options:
            sheet_options.append(sheet_name)
        if len(sheet_options) == 0:
            sheet_options = CORE_SHEETS

        current_rel_sheet = str(rel_cfg.get("sheet", sheet_options[0]))
        if current_rel_sheet not in sheet_options:
            sheet_options.append(current_rel_sheet)
        rel_sheet = row_cols[3].selectbox(
            "source sheet",
            sheet_options,
            index=sheet_options.index(current_rel_sheet),
            key=f"rel_sheet::{sheet_name}::{col_name}",
            label_visibility="collapsed",
            help="Choose the sheet that provides the base values for this relative rule.",
        )
        rel_cfg["sheet"] = rel_sheet

        known_cols = _get_known_columns(cfg, rel_sheet)
        known_cols_with_manual = known_cols + ["<manual>"]
        current_rel_col = str(rel_cfg.get("column", known_cols[0] if known_cols else "PMAX"))
        if current_rel_col in known_cols:
            choice = row_cols[4].selectbox(
                "source field",
                known_cols_with_manual,
                index=known_cols_with_manual.index(current_rel_col),
                key=f"rel_col_choice::{sheet_name}::{col_name}",
                label_visibility="collapsed",
            )
            if choice == "<manual>":
                rel_cfg["column"] = row_cols[4].text_input(
                    "source field",
                    value=current_rel_col,
                    key=f"rel_col_manual::{sheet_name}::{col_name}",
                    label_visibility="collapsed",
                    help="Enter a source column manually when it is not listed yet.",
                )
            else:
                rel_cfg["column"] = choice
        else:
            choice = row_cols[4].selectbox(
                "source field",
                known_cols_with_manual,
                index=len(known_cols_with_manual) - 1,
                key=f"rel_col_choice::{sheet_name}::{col_name}",
                label_visibility="collapsed",
            )
            if choice == "<manual>":
                rel_cfg["column"] = row_cols[4].text_input(
                    "source field",
                    value=current_rel_col,
                    key=f"rel_col_manual::{sheet_name}::{col_name}",
                    label_visibility="collapsed",
                    help="Enter a source column manually when it is not listed yet.",
                )
            else:
                rel_cfg["column"] = choice

        current_mode = str(rel_cfg.get("map_by", rel_cfg.get("aggregate", "row"))).strip().lower()
        if current_mode not in RELATIVE_MODE_OPTIONS:
            current_mode = "row"
        relative_mode = row_cols[5].selectbox(
            "map / aggregate",
            RELATIVE_MODE_OPTIONS,
            index=RELATIVE_MODE_OPTIONS.index(current_mode),
            key=f"relative_mode::{sheet_name}::{col_name}",
            label_visibility="collapsed",
            help="`row` matches by row order, `bus_idx` matches by BUS_IDX, and aggregate modes reduce the source to one scalar.",
        )
        if relative_mode in ALLOWED_MAP_BY:
            rel_cfg["map_by"] = relative_mode
            rel_cfg.pop("aggregate", None)
        else:
            rel_cfg["aggregate"] = relative_mode
            rel_cfg.pop("map_by", None)
    else:
        col_cfg.pop("relative_to", None)
        row_cols[3].markdown("`-`")
        row_cols[4].markdown("`-`")
        row_cols[5].markdown("`-`")

    random_enabled = row_cols[6].checkbox(
        "use random_ratio",
        value=random_enabled_default,
        key=f"random_enabled::{sheet_name}::{col_name}",
        label_visibility="collapsed",
        help="Apply multiplicative noise after the base value is computed.",
    )
    if random_enabled:
        col_cfg["random_ratio"] = float(
            row_cols[7].number_input(
                "random_ratio",
                min_value=0.0,
                max_value=1.0,
                value=float(col_cfg.get("random_ratio", 0.0)),
                step=0.01,
                key=f"random_ratio::{sheet_name}::{col_name}",
                label_visibility="collapsed",
                help="Random multiplier range `1 + U[-r, r]` where `r` is this value.",
            )
        )
    else:
        col_cfg.pop("random_ratio", None)
        row_cols[7].markdown("`-`")

    if row_cols[8].button("Remove", key=f"remove_col::{sheet_name}::{col_name}"):
        del cfg["grid_config"][sheet_name][col_name]
        st.rerun()

    if _show_formula_captions():
        st.caption(_format_column_formula(sheet_name, col_name, col_cfg))
    if fmt == "absolute" and _show_extra_guidance():
        st.caption("Absolute rules write values directly into this field.")

    if fmt == "relative":
        rel_cfg = col_cfg.get("relative_to", {})
        if isinstance(rel_cfg, dict):
            rel_sheet = str(rel_cfg.get("sheet", "")).strip()
            map_by_mode = str(rel_cfg.get("map_by", "")).strip().lower()
            if map_by_mode == "row":
                if _show_extra_guidance():
                    st.caption("`map_by: row` copies values by row order, so source and target row counts must match.")
            elif map_by_mode == "bus_idx":
                if _show_extra_guidance():
                    st.caption("`map_by: bus_idx` matches source and target rows using the same `BUS_IDX`.")
            elif str(rel_cfg.get("aggregate", "")).strip().lower() in ALLOWED_AGGREGATES:
                if _show_extra_guidance():
                    st.caption("Aggregate modes reduce the source column to one scalar, then apply your `value` multiplier.")

            if map_by_mode == "bus_idx":
                missing_bus_idx_sheets = []
                if not _sheet_likely_has_bus_idx(cfg, sheet_name):
                    missing_bus_idx_sheets.append(f"target `{sheet_name}`")
                if rel_sheet != "" and not _sheet_likely_has_bus_idx(cfg, rel_sheet):
                    missing_bus_idx_sheets.append(f"source `{rel_sheet}`")
                if missing_bus_idx_sheets:
                    st.warning(
                        "`map_by: bus_idx` needs `BUS_IDX` on both source and target. "
                        f"Likely missing on: {', '.join(missing_bus_idx_sheets)}."
                    )

            if map_by_mode == "row":
                unstable_row_sheets = []
                if _is_custom_sheet(cfg, sheet_name):
                    target_cfg = cfg["grid_config"].get(sheet_name, {})
                    bus_idx_cfg = target_cfg.get("BUS_IDX")
                    if not isinstance(bus_idx_cfg, dict) or str(bus_idx_cfg.get("format", "")).strip().lower() == "relative":
                        unstable_row_sheets.append(f"target `{sheet_name}`")
                if rel_sheet != "" and _is_custom_sheet(cfg, rel_sheet):
                    source_cfg = cfg["grid_config"].get(rel_sheet, {})
                    source_bus_idx_cfg = source_cfg.get("BUS_IDX")
                    if not isinstance(source_bus_idx_cfg, dict) or str(source_bus_idx_cfg.get("format", "")).strip().lower() == "relative":
                        unstable_row_sheets.append(f"source `{rel_sheet}`")
                if unstable_row_sheets:
                    st.info(
                        "`map_by: row` is positional and needs exact row-count alignment. "
                        f"Double-check row counts for {', '.join(unstable_row_sheets)}."
                    )


def _render_bus_idx_editor(cfg: Dict[str, Any], sheet_name: str, mode: str) -> None:
    bus_idx_cfg = cfg["grid_config"][sheet_name].setdefault("BUS_IDX", {"format": "absolute", "value": [1]})
    bus_idx_cfg["format"] = mode
    with st.expander(f"{sheet_name} placement", expanded=True):
        if _show_extra_guidance():
            st.caption(f"Placement mode is controlled by `BUS_IDX mode: {mode}`.")

        if mode == "absolute":
            placement_cols = st.columns([3.2, 1.8])
            known_bus_ids = _get_known_bus_ids()
            current_bus_values = bus_idx_cfg.get("value", [1])
            current_bus_ids: List[int] = []
            for value in current_bus_values if isinstance(current_bus_values, list) else []:
                try:
                    current_bus_ids.append(int(value))
                except (TypeError, ValueError):
                    continue

            if known_bus_ids:
                bus_options = sorted(set(known_bus_ids + current_bus_ids))
                selected_bus_ids = placement_cols[0].multiselect(
                    "Bus selection",
                    bus_options,
                    default=[bus_id for bus_id in current_bus_ids if bus_id in bus_options],
                    key=f"bus_idx_absolute_select::{sheet_name}",
                    help="Choose the exact bus IDs for this sheet.",
                )
                bus_idx_cfg["value"] = selected_bus_ids
                unknown_bus_ids = [bus_id for bus_id in current_bus_ids if bus_id not in known_bus_ids]
                if unknown_bus_ids:
                    st.warning(
                        f"Some saved bus IDs are not in the current preview bus list: {unknown_bus_ids}."
                    )
                placement_cols[1].caption("Placement")
                placement_cols[1].markdown(
                    f"Choose the exact bus IDs for this sheet. `{len(known_bus_ids)}` buses are available in the current preview."
                )
            else:
                default_value_text = yaml.safe_dump(bus_idx_cfg.get("value", [1]), default_flow_style=True).strip()
                raw_values = placement_cols[0].text_input(
                    "Bus selection",
                    value=default_value_text,
                    key=f"bus_idx_value::{sheet_name}",
                    help="Explicit bus IDs, for example `[2, 8]` or `2,8`.",
                )
                placement_cols[1].caption("Placement")
                placement_cols[1].markdown("Choose the exact bus IDs for this sheet.")
                try:
                    bus_idx_cfg["value"] = _parse_value_list(raw_values)
                except Exception as e:
                    st.error(f"BUS_IDX value parse error: {e}")
            bus_idx_cfg.pop("relative_to", None)
        else:
            rel_cfg = bus_idx_cfg.setdefault("relative_to", {})
            placement_cols = st.columns([1.4, 2.6])
            default_ratio_text = yaml.safe_dump(bus_idx_cfg.get("value", [0.2]), default_flow_style=True).strip()
            raw_values = placement_cols[0].text_input(
                "Selection fraction",
                value=default_ratio_text,
                key=f"bus_idx_value::{sheet_name}",
                help="Single ratio in `[0, 1]`, for example `[0.3]`.",
            )
            try:
                bus_idx_cfg["value"] = _parse_value_list(raw_values)
            except Exception as e:
                st.error(f"BUS_IDX value parse error: {e}")

            bus_type_options = list(BUS_TYPE_LABELS.keys())
            selected_bus_types = placement_cols[1].multiselect(
                "Candidate bus pool",
                bus_type_options,
                default=[str(v) for v in rel_cfg.get("bus_type", ["4"])],
                key=f"bus_idx_bus_type::{sheet_name}",
                format_func=_format_bus_type_token,
                help="Choose which buses are eligible for sampling.",
            )
            rel_cfg["bus_type"] = selected_bus_types
            rel_cfg.pop("sheet", None)
            rel_cfg.pop("column", None)
            rel_cfg.pop("map_by", None)
            rel_cfg.pop("aggregate", None)

            candidate_bus_ids = _get_candidate_bus_ids(selected_bus_types)
            has_bus_preview = _has_bus_preview()
            estimated_selection_count = _estimate_relative_selection_count(bus_idx_cfg.get("value", []), len(candidate_bus_ids))
            preview_cols = st.columns([1.4, 1.2, 2.4])
            preview_cols[0].metric("Candidate buses", len(candidate_bus_ids))
            preview_cols[1].metric("Estimated selected", estimated_selection_count if estimated_selection_count is not None else "-")
            if candidate_bus_ids:
                preview_cols[2].caption(
                    f"Candidate bus IDs: `{candidate_bus_ids[:12]}`"
                    + (" ..." if len(candidate_bus_ids) > 12 else "")
                )
            elif has_bus_preview:
                preview_cols[2].caption("The current preview has no buses matching this candidate pool.")
            else:
                preview_cols[2].caption("Candidate bus IDs are unavailable until a bus preview is loaded or built.")

        policy_cols = st.columns([1.8, 2.2])
        group_val = policy_cols[0].text_input(
            "Prevent overlap group",
            value=str(bus_idx_cfg.get("group", "")),
            key=f"bus_idx_group::{sheet_name}",
            help="Sheets sharing the same group cannot choose the same buses.",
        ).strip()
        if group_val == "":
            bus_idx_cfg.pop("group", None)
        else:
            bus_idx_cfg["group"] = group_val

        bus_idx_cfg["remove_gen"] = bool(
            policy_cols[1].checkbox(
                "Remove generator rows on selected buses",
                value=bool(bus_idx_cfg.get("remove_gen", False)),
                key=f"bus_idx_remove_gen::{sheet_name}",
                help="Remove matching rows from `gen` after this sheet is built.",
            )
        )

        if _show_extra_guidance():
            st.caption(_format_bus_idx_summary(sheet_name, bus_idx_cfg))
        if _show_formula_captions():
            st.caption(_format_column_formula(sheet_name, "BUS_IDX", bus_idx_cfg))

        selected_values = bus_idx_cfg.get("value", [])
        if mode == "relative":
            rel_cfg = bus_idx_cfg.get("relative_to", {})
            selected_bus_types = rel_cfg.get("bus_type", []) if isinstance(rel_cfg, dict) else []
            if not selected_bus_types:
                st.warning("Choose at least one candidate bus pool for relative placement.")
            if isinstance(selected_values, list) and len(selected_values) > 0:
                try:
                    ratio = float(selected_values[0])
                    if ratio < 0 or ratio > 1:
                        st.warning("Selection fraction should be between 0 and 1.")
                except (TypeError, ValueError):
                    st.warning("Selection fraction should be a numeric list like `[0.3]`.")

            candidate_bus_ids = _get_candidate_bus_ids(selected_bus_types)
            has_bus_preview = _has_bus_preview()
            if selected_bus_types and has_bus_preview and len(candidate_bus_ids) == 0:
                st.warning(
                    "The chosen candidate bus pool matches zero buses in the current preview."
                )
            elif selected_bus_types and not has_bus_preview and _show_extra_guidance():
                st.info("Load a PYPOWER case or build a draft preview to inspect candidate bus IDs.")
            if bus_idx_cfg.get("remove_gen", False) and candidate_bus_ids:
                gen_bus_ids = set(_get_known_gen_bus_ids())
                overlap_bus_ids = [bus_id for bus_id in candidate_bus_ids if bus_id in gen_bus_ids]
                if len(overlap_bus_ids) == 0:
                    st.info(
                        "The current candidate bus pool has no overlap with known generator buses, "
                        "so no generator rows may be removed."
                    )
                else:
                    st.caption(
                        f"Generator overlap in current candidate pool: `{len(overlap_bus_ids)}` buses "
                        f"({overlap_bus_ids[:12]}{' ...' if len(overlap_bus_ids) > 12 else ''})."
                    )
        else:
            explicit_bus_ids: List[int] = []
            invalid_bus_tokens: List[Any] = []
            for value in selected_values if isinstance(selected_values, list) else []:
                try:
                    explicit_bus_ids.append(int(value))
                except (TypeError, ValueError):
                    invalid_bus_tokens.append(value)
            if invalid_bus_tokens:
                st.warning(f"Bus selection should contain integer bus IDs. Invalid entries: {invalid_bus_tokens}.")

            known_bus_ids = set(_get_known_bus_ids())
            if known_bus_ids:
                missing_bus_ids = [bus_id for bus_id in explicit_bus_ids if bus_id not in known_bus_ids]
                if missing_bus_ids:
                    st.warning(
                        f"Some selected buses are not present in the current preview bus list: {missing_bus_ids}."
                    )
            if bus_idx_cfg.get("remove_gen", False) and explicit_bus_ids:
                gen_bus_ids = set(_get_known_gen_bus_ids())
                overlap_bus_ids = [bus_id for bus_id in explicit_bus_ids if bus_id in gen_bus_ids]
                if len(overlap_bus_ids) == 0 and gen_bus_ids:
                    st.info("None of the selected buses match known generator buses, so no generator rows may be removed.")
                elif overlap_bus_ids:
                    st.caption(
                        f"Generator rows found on selected buses: `{overlap_bus_ids}`."
                    )

        group_val = str(bus_idx_cfg.get("group", "")).strip()
        if group_val:
            peer_sheets = []
            for other_sheet_name, other_sheet_cfg in cfg.get("grid_config", {}).items():
                if other_sheet_name == sheet_name or not isinstance(other_sheet_cfg, dict):
                    continue
                other_bus_idx_cfg = other_sheet_cfg.get("BUS_IDX")
                if isinstance(other_bus_idx_cfg, dict) and str(other_bus_idx_cfg.get("group", "")).strip() == group_val:
                    peer_sheets.append(other_sheet_name)
            if peer_sheets:
                st.caption(f"Group `{group_val}` is also used by: `{peer_sheets}`.")


def _render_status_editor(cfg: Dict[str, Any], sheet_name: str) -> None:
    status_cfg = cfg["grid_config"][sheet_name].setdefault("STATUS", {"format": "absolute", "value": [1]})
    status_cfg["format"] = "absolute"

    with st.expander(f"{sheet_name} status", expanded=True):
        if _show_extra_guidance():
            st.caption("Custom status overrides the default constructor behavior for this sheet.")

        status_cols = st.columns([1.5, 2.0, 2.5])
        status_mode = status_cols[0].selectbox(
            "Status value",
            ["1", "0", "custom"],
            index=["1", "0", "custom"].index(
                "custom"
                if not (isinstance(status_cfg.get("value"), list) and len(status_cfg["value"]) == 1 and str(status_cfg["value"][0]) in {"0", "1"})
                else str(status_cfg["value"][0])
            ),
            key=f"status_value_mode::{sheet_name}",
            help="Use 1 for active rows, 0 for inactive rows, or custom for a row-wise list.",
        )

        if status_mode in {"1", "0"}:
            status_cfg["value"] = [int(status_mode)]
            status_cols[1].caption("Applied value")
            status_cols[1].markdown(f"All rows will use `STATUS = {status_mode}`.")
        else:
            default_value_text = yaml.safe_dump(status_cfg.get("value", [1]), default_flow_style=True).strip()
            raw_values = status_cols[1].text_input(
                "Status list",
                value=default_value_text,
                key=f"status_value::{sheet_name}",
                help="Examples: `[1]`, `[1, 1, 0]`, or `1,1,0`.",
            )
            try:
                parsed_values = _parse_value_list(raw_values)
                status_cfg["value"] = parsed_values
            except Exception as e:
                st.error(f"STATUS value parse error: {e}")

        status_cols[2].caption("Summary")
        status_cols[2].markdown(
            "Override the default `STATUS = 1` auto-fill for this custom sheet."
        )

        if _show_formula_captions():
            st.caption(_format_column_formula(sheet_name, "STATUS", status_cfg))

        values = status_cfg.get("value", [])
        if isinstance(values, list) and len(values) > 0:
            invalid_values = [v for v in values if str(v) not in {"0", "1"} and v not in {0, 1, True, False}]
            if invalid_values:
                st.warning(f"STATUS should usually contain only 0/1 values. Found: {invalid_values}.")
            elif len(values) > 1 and _show_extra_guidance():
                st.caption(f"Custom row-wise STATUS list with `{len(values)}` entries.")


def _apply_app_typography() -> None:
    """Apply large typography for readability in Streamlit."""
    st.markdown(
        """
        <style>
        html, body, [data-testid="stAppViewContainer"] {
            font-size: 20px;
        }

        [data-testid="stSidebar"] {
            font-size: 19px;
        }

        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            font-size: 24px;
        }

        h1 {
            font-size: 50px !important;
            line-height: 1.12 !important;
        }

        h2 {
            font-size: 36px !important;
            line-height: 1.18 !important;
        }

        h3 {
            font-size: 29px !important;
            line-height: 1.2 !important;
        }

        [data-testid="stMarkdownContainer"] p,
        [data-testid="stMarkdownContainer"] li {
            font-size: 20px;
            line-height: 1.5;
        }

        [data-baseweb="tab"] {
            font-size: 20px;
        }

        [data-testid="stWidgetLabel"] p,
        label p {
            font-size: 19px;
            line-height: 1.35;
        }

        input,
        textarea,
        [data-baseweb="select"] {
            font-size: 19px;
        }

        button,
        [data-testid="stButton"] p {
            font-size: 19px;
            white-space: nowrap;
        }

        button {
            min-height: 3rem;
        }

        [data-testid="stCaptionContainer"] p {
            font-size: 18px;
            line-height: 1.45;
        }

        [data-testid="column"] [data-testid="stCaptionContainer"] p {
            color: #4b5563;
            font-size: 20px;
            font-weight: 600;
        }

        [data-testid="stDataFrame"] {
            font-size: 18px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="GridForge Config Builder", layout="wide")
    _apply_app_typography()
    st.title("GridForge Interactive Config Builder")
    st.caption("Build YAML config, save it, and run grid construction.")

    if "cfg" not in st.session_state:
        st.session_state["cfg"] = _default_state()
    cfg: Dict[str, Any] = st.session_state["cfg"]

    with st.sidebar:
        st.header("Super Config")
        cfg["super_config"]["pypower_case_name"] = st.text_input(
            "pypower_case_name",
            value=str(cfg["super_config"].get("pypower_case_name", "case14")),
        )
        cfg["super_config"]["baseMVA"] = float(
            st.number_input(
                "baseMVA",
                min_value=1.0,
                value=float(cfg["super_config"].get("baseMVA", 100.0)),
                step=1.0,
            )
        )
        if st.button("Reset All"):
            st.session_state["cfg"] = _default_state()
            st.rerun()

        st.divider()
        st.header("PYPOWER Case")
        pypower_case_name = str(cfg["super_config"].get("pypower_case_name", "case14"))
        if st.button("Load PYPOWER Case"):
            try:
                st.session_state["case_preview_name"] = pypower_case_name
                st.session_state["case_preview"] = _load_pypower_case_preview(pypower_case_name)
                st.success(f"Loaded `{pypower_case_name}`")
            except Exception as e:
                st.error(f"Load failed: {e}")

        st.divider()
        st.header("Load Existing YAML")
        yaml_load_path = st.text_input(
            "YAML path",
            value=st.session_state.get("yaml_load_path", "grid_config.yaml"),
            key="yaml_load_path",
        )
        if st.button("Load YAML Into Builder"):
            try:
                with open(yaml_load_path, "r") as f:
                    loaded = yaml.safe_load(f)
                st.session_state["cfg"] = _validate_loaded_cfg(loaded)
                _reset_builder_widget_state()
                st.success(f"Loaded YAML from `{yaml_load_path}`.")
                st.rerun()
            except Exception as e:
                st.error(f"Failed to load YAML: {e}")

        st.divider()
        st.header("View Options")
        st.checkbox(
            "Show generated Excel preview",
            value=bool(st.session_state.get("show_generated_excel", True)),
            key="show_generated_excel",
        )
        st.checkbox(
            "Show extra guidance",
            value=bool(st.session_state.get("show_extra_guidance", True)),
            key="show_extra_guidance",
        )
        st.checkbox(
            "Show formula captions",
            value=bool(st.session_state.get("show_formula_captions", True)),
            key="show_formula_captions",
        )

    case_tab, assets_tab, balancing_tab, preview_tab, yaml_tab = st.tabs(
        ["Case", "Assets & Sheets", "Balancing", "Preview", "YAML"]
    )

    with case_tab:
        st.subheader("Configuration Overview")
        _render_config_overview(cfg)

        st.subheader("PYPOWER Case Preview")
        case_preview = st.session_state.get("case_preview", None)
        case_preview_name = st.session_state.get("case_preview_name", "")
        if isinstance(case_preview, dict) and len(case_preview) > 0:
            st.caption(f"Showing loaded case: `{case_preview_name}`")
            dims = {
                "nbus": len(case_preview.get("bus", [])),
                "ngen": len(case_preview.get("gen", [])),
                "nbranch": len(case_preview.get("branch", [])),
            }
            dim_cols = st.columns(3)
            dim_cols[0].metric("Buses", dims["nbus"])
            dim_cols[1].metric("Generators", dims["ngen"])
            dim_cols[2].metric("Branches", dims["nbranch"])
            tabs = st.tabs([k for k in ["bus", "gen", "branch"] if k in case_preview])
            for tab, key in zip(tabs, [k for k in ["bus", "gen", "branch"] if k in case_preview]):
                with tab:
                    st.dataframe(case_preview[key], width="stretch", height=260)
        else:
            st.info("Load a PYPOWER case from the sidebar to inspect the base bus, gen, and branch sheets.")

    with assets_tab:
        st.subheader("Top-Level Sheets")
        if _show_extra_guidance():
            st.caption(
                "Core sheets edit the base PYPOWER case. Custom sheets define assets attached to buses, such as "
                "`load`, `solar`, `wind`, or `storage`."
            )
        add_sheet_wrap, _ = st.columns([3.8, 1.4])
        with add_sheet_wrap:
            add_sheet_cols = st.columns([0.9, 2.8, 1.7])
            add_sheet_cols[0].caption("New sheet")
            new_sheet_name = add_sheet_cols[1].text_input(
                "New sheet",
                value="",
                label_visibility="collapsed",
                help="Examples: solar, wind, storage",
                key="new_sheet_name",
            )
            if add_sheet_cols[2].button("Add Sheet", width="stretch"):
                s = new_sheet_name.strip()
                if s == "":
                    st.warning("Sheet name cannot be empty.")
                elif s in cfg["grid_config"]:
                    st.warning(f"Sheet '{s}' already exists.")
                else:
                    cfg["grid_config"][s] = {}
                    st.rerun()

        for sheet_name in list(cfg["grid_config"].keys()):
            with st.container(border=True):
                head_cols = st.columns([3.2, 1.4])
                head_cols[0].markdown(f"### `{sheet_name}`")
                if sheet_name not in CORE_SHEETS and head_cols[1].button(
                    "Remove Sheet",
                    key=f"remove_sheet::{sheet_name}",
                    width="stretch",
                ):
                    del cfg["grid_config"][sheet_name]
                    st.rerun()

                _render_sheet_summary(cfg, sheet_name)

                if _show_extra_guidance():
                    if sheet_name in CORE_SHEETS:
                        st.caption(
                            f"`{sheet_name}` is a core sheet. Editing fields here modifies the base-case table loaded from PYPOWER."
                        )
                    else:
                        st.caption(
                            f"`{sheet_name}` is a custom sheet. Fields here define a new asset table that will be added during construction."
                        )

                add_field_wrap, _ = st.columns([4.2, 1.0])
                with add_field_wrap:
                    add_field_cols = st.columns([0.8, 2.8, 1.8])
                    add_field_cols[0].caption("Field")
                    new_col = add_field_cols[1].text_input(
                        f"Add or edit field in `{sheet_name}`",
                        key=f"new_col::{sheet_name}",
                        label_visibility="collapsed",
                        help="Use an existing field name to modify it, or enter a new name to add a field.",
                    )
                    normalized_new_col = _normalize_field_name(new_col)
                    known_fields = set(_get_known_columns(cfg, sheet_name))
                    current_fields = set(cfg["grid_config"][sheet_name].keys())
                    if add_field_cols[2].button(
                        "Add or Edit",
                        key=f"add_col_btn::{sheet_name}",
                        width="stretch",
                    ):
                        c = _normalize_field_name(new_col)
                        if c == "":
                            st.warning("Field name cannot be empty.")
                        elif c in SPECIAL_COLUMNS:
                            st.warning(
                                f"`{c}` is a special entry. Use the dedicated BUS_IDX/STATUS controls instead."
                            )
                        elif c in cfg["grid_config"][sheet_name]:
                            st.info(f"Field '{c}' already exists in '{sheet_name}'. Opening it below for editing.")
                        else:
                            cfg["grid_config"][sheet_name][c] = {"format": "absolute", "value": [0]}
                            st.rerun()

                if normalized_new_col != "":
                    if normalized_new_col in current_fields:
                        st.caption(
                            f"`{normalized_new_col}` is already present in this sheet. Editing it will update the existing rule."
                        )
                    elif normalized_new_col in known_fields:
                        st.caption(
                            f"`{normalized_new_col}` matches an existing field and will modify its values."
                        )
                    else:
                        st.caption(
                            f"`{normalized_new_col}` is new and will be added to this sheet."
                        )
                if _show_extra_guidance():
                    st.caption(
                        "Field names are case-insensitive in the builder and are stored/displayed in uppercase."
                    )
                    if sheet_name in CORE_SHEETS:
                        st.caption(
                            "Use existing field names to modify known PYPOWER columns, or add extra fields if your workflow needs them."
                        )
                    else:
                        st.caption(
                            "Use special controls for `BUS_IDX` and `STATUS`, and use normal fields to define the rest of the custom table."
                        )

                if sheet_name not in CORE_SHEETS:
                    bus_idx_mode_options = ["absolute", "relative"]
                    if "BUS_IDX" in cfg["grid_config"][sheet_name]:
                        current_bus_idx_mode = str(
                            cfg["grid_config"][sheet_name]["BUS_IDX"].get("format", "absolute")
                        ).lower()
                        if current_bus_idx_mode not in {"absolute", "relative"}:
                            current_bus_idx_mode = "absolute"
                    else:
                        cfg["grid_config"][sheet_name]["BUS_IDX"] = {
                            "format": "absolute",
                            "value": [1],
                        }
                        current_bus_idx_mode = "absolute"
                    bus_idx_mode = st.selectbox(
                        "BUS_IDX mode",
                        bus_idx_mode_options,
                        index=bus_idx_mode_options.index(current_bus_idx_mode),
                        key=f"bus_idx_mode::{sheet_name}",
                        help="BUS_IDX is required for custom sheets and controls bus placement.",
                    )
                    if "BUS_IDX" not in cfg["grid_config"][sheet_name]:
                        cfg["grid_config"][sheet_name]["BUS_IDX"] = {
                            "format": bus_idx_mode,
                            "value": [1] if bus_idx_mode == "absolute" else [0.2],
                        }
                    else:
                        cfg["grid_config"][sheet_name]["BUS_IDX"]["format"] = bus_idx_mode
                    _render_bus_idx_editor(cfg, sheet_name, bus_idx_mode)

                    status_mode_options = ["auto(default=1)", "custom"]
                    status_mode_default = "custom" if "STATUS" in cfg["grid_config"][sheet_name] else "auto(default=1)"
                    status_mode = st.selectbox(
                        "STATUS mode",
                        status_mode_options,
                        index=status_mode_options.index(status_mode_default),
                        key=f"status_mode::{sheet_name}",
                        help="STATUS is special; auto mode relies on constructor default.",
                    )
                    if status_mode == "auto(default=1)":
                        cfg["grid_config"][sheet_name].pop("STATUS", None)
                    else:
                        cfg["grid_config"][sheet_name].setdefault("STATUS", {"format": "absolute", "value": [1]})
                        _render_status_editor(cfg, sheet_name)

                normal_field_names = [
                    col_name for col_name in list(cfg["grid_config"][sheet_name].keys())
                    if col_name not in SPECIAL_COLUMNS
                ]
                if len(normal_field_names) > 0:
                    _render_field_editor_header()
                for col_name in list(cfg["grid_config"][sheet_name].keys()):
                    if col_name in SPECIAL_COLUMNS:
                        continue
                    _render_column_editor(cfg, sheet_name, col_name)

    with balancing_tab:
        _render_rescale_editor(cfg)

    with preview_tab:
        st.subheader("Build And Inspect")
        build_cols = st.columns([1, 1, 1])
        preview_seed = int(build_cols[0].number_input("Topology seed", min_value=0, value=42, step=1, key="preview_topology_seed"))
        random_seed = int(build_cols[1].number_input("Construction seed", min_value=0, value=404, step=1, key="preview_random_seed"))
        output_xlsx = build_cols[2].text_input("Output xlsx path", value="grid_config.xlsx", key="preview_output_xlsx")

        action_cols = st.columns(3)
        if action_cols[0].button("Build Draft Tables"):
            try:
                st.session_state["draft_grid_preview"] = _build_sheet_preview_from_cfg(cfg, random_seed=random_seed)
                st.session_state["draft_grid_preview_seed"] = random_seed
                st.success("Draft tables refreshed from current builder settings.")
            except Exception as e:
                st.error(f"Draft table build failed: {e}")

        if action_cols[1].button("Refresh Topology"):
            yaml_path = None
            xlsx_path = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as yaml_file:
                    yaml.safe_dump(cfg, yaml_file, sort_keys=False)
                    yaml_path = yaml_file.name
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as xlsx_file:
                    xlsx_path = xlsx_file.name
                construct_grid_config(yaml_path, xlsx_path, random_seed=random_seed)
                st.session_state["draft_grid_preview"] = _without_metadata_sheet(
                    pd.read_excel(xlsx_path, sheet_name=None)
                )
                st.session_state["draft_grid_preview_seed"] = random_seed
                st.session_state["topology_preview_fig"] = _build_topology_figure_from_xlsx(xlsx_path, seed=preview_seed)
                st.success("Topology refreshed from current builder settings.")
            except Exception as e:
                st.error(f"Topology preview failed: {e}")
            finally:
                if yaml_path and os.path.exists(yaml_path):
                    os.remove(yaml_path)
                if xlsx_path and os.path.exists(xlsx_path):
                    os.remove(xlsx_path)

        if action_cols[2].button("Construct Workbook"):
            yaml_path = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as yaml_file:
                    yaml.safe_dump(cfg, yaml_file, sort_keys=False)
                    yaml_path = yaml_file.name
                construct_grid_config(yaml_path, output_xlsx, random_seed)
                if os.path.exists(output_xlsx):
                    st.session_state["last_output_xlsx"] = output_xlsx
                    st.success(f"Constructed grid config at `{output_xlsx}`")
                else:
                    st.warning("Construct function returned, but output file was not found.")
            except Exception as e:
                st.error(f"Construct failed: {e}")
            finally:
                if yaml_path and os.path.exists(yaml_path):
                    os.remove(yaml_path)

        fig = st.session_state.get("topology_preview_fig")
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)
        elif _show_extra_guidance():
            st.info("Use Refresh Topology to visualize the generated buses, branches, and custom assets.")

        draft_preview = st.session_state.get("draft_grid_preview", None)
        if isinstance(draft_preview, dict) and len(draft_preview) > 0:
            _render_sheet_preview(
                draft_preview,
                title=f"Draft Tables (seed={st.session_state.get('draft_grid_preview_seed', random_seed)})",
            )

        if bool(st.session_state.get("show_generated_excel", True)):
            last_output_xlsx = st.session_state.get("last_output_xlsx", output_xlsx)
            _render_excel_preview(
                xlsx_path=last_output_xlsx,
                section_title="Generated Workbook",
            )

    with yaml_tab:
        st.subheader("YAML")
        yaml_text = yaml.safe_dump(cfg, sort_keys=False)
        edited_yaml = st.text_area("Edit YAML directly", value=yaml_text, height=460)

        action_cols = st.columns(3)
        save_path = action_cols[0].text_input("Save path", value="grid_config.yaml", key="yaml_save_path")
        yaml_output_xlsx = action_cols[1].text_input("Output xlsx path", value="grid_config.xlsx", key="yaml_output_xlsx")
        yaml_random_seed = int(action_cols[2].number_input("Random seed", min_value=0, value=404, step=1, key="yaml_random_seed"))

        btn_cols = st.columns(3)
        if btn_cols[0].button("Apply YAML To Builder"):
            try:
                parsed = yaml.safe_load(edited_yaml)
                st.session_state["cfg"] = _validate_loaded_cfg(parsed)
                _reset_builder_widget_state()
                st.success("Builder state updated from YAML.")
                st.rerun()
            except Exception as e:
                st.error(f"YAML parse/apply failed: {e}")

        if btn_cols[1].button("Save YAML"):
            try:
                parsed = yaml.safe_load(edited_yaml)
                with open(save_path, "w") as f:
                    yaml.safe_dump(parsed, f, sort_keys=False)
                st.success(f"Saved YAML to `{save_path}`")
            except Exception as e:
                st.error(f"Save failed: {e}")

        if btn_cols[2].button("Construct From YAML"):
            try:
                parsed = yaml.safe_load(edited_yaml)
                tmp_path = save_path
                with open(tmp_path, "w") as f:
                    yaml.safe_dump(parsed, f, sort_keys=False)
                construct_grid_config(tmp_path, yaml_output_xlsx, yaml_random_seed)
                if os.path.exists(yaml_output_xlsx):
                    st.session_state["last_output_xlsx"] = yaml_output_xlsx
                    st.success(f"Constructed grid config at `{yaml_output_xlsx}`")
                else:
                    st.warning("Construct function returned, but output file was not found.")
            except Exception as e:
                st.error(f"Construct failed: {e}")

        st.subheader("Current Config Object")
        st.json(copy.deepcopy(cfg), expanded=False)


if __name__ == "__main__":
    main()
