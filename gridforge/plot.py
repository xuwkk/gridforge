"""
Plotting utilities for GridForge.
"""

from typing import Dict, Tuple
import pandas as pd


def draw_grid_topology(
    grid_xlsx_path: str,
    output_path: str = "grid_topology.png",
    layout: str = "spring",
    with_labels: bool = True,
    node_size: int = 500,
    figsize: Tuple[float, float] = (8, 6),
    seed: int = 42,
) -> None:
    """
    Draw and save the grid topology from a GridForge config Excel file.

    Args:
        grid_xlsx_path: Path to the grid configuration Excel file.
        output_path: Path to save the rendered topology image.
        layout: Layout algorithm ("spring", "kamada_kawai", "circular", "shell").
        with_labels: Whether to draw bus labels.
        node_size: Node size for plotting.
        figsize: Figure size in inches.
        seed: Random seed for deterministic layouts where applicable.
    """
    try:
        import networkx as nx
    except ImportError as e:
        raise ImportError(
            "draw_grid_topology requires networkx. Install with `pip install networkx`."
        ) from e

    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Patch
    except ImportError as e:
        raise ImportError(
            "draw_grid_topology requires matplotlib. Install with `pip install matplotlib`."
        ) from e

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
        bus_idx = int(row["BUS_IDX"])
        bus_type = int(row["BUS_TYPE"])
        graph.add_node(bus_idx, bus_type=bus_type)

    for _, row in branch_df.iterrows():
        from_bus = int(row["F_BUS_IDX"])
        to_bus = int(row["T_BUS_IDX"])
        graph.add_edge(from_bus, to_bus)

    if layout == "spring":
        pos = nx.spring_layout(graph, seed=seed)
    elif layout == "kamada_kawai":
        pos = nx.kamada_kawai_layout(graph)
    elif layout == "circular":
        pos = nx.circular_layout(graph)
    elif layout == "shell":
        pos = nx.shell_layout(graph)
    else:
        raise ValueError(
            f"Unsupported layout '{layout}'. Use one of: spring, kamada_kawai, circular, shell."
        )

    color_by_type = {1: "lightblue", 2: "orange", 3: "red"}
    node_colors = [color_by_type.get(graph.nodes[n]["bus_type"], "gray") for n in graph.nodes]
    label_by_type = {1: "PQ", 2: "PV", 3: "Slack"}

    present_bus_types = {graph.nodes[n]["bus_type"] for n in graph.nodes}
    legend_handles = []
    for bus_type in sorted(t for t in present_bus_types if t in label_by_type):
        legend_handles.append(
            Patch(facecolor=color_by_type[bus_type], edgecolor="black", label=label_by_type[bus_type])
        )
    if any(t not in label_by_type for t in present_bus_types):
        legend_handles.append(Patch(facecolor="gray", edgecolor="black", label="Other"))

    plt.figure(figsize=figsize)
    nx.draw_networkx(
        graph,
        pos=pos,
        with_labels=with_labels,
        node_size=node_size,
        node_color=node_colors,
        font_size=8,
    )
    plt.title("Grid Topology")
    if legend_handles:
        plt.legend(handles=legend_handles, title="Bus Type", loc="best")
    plt.axis("off")
    plt.tight_layout()
    plt.savefig(output_path, dpi=200)
    plt.close()


def draw_grid_topology_interactive(
    grid_xlsx_path: str,
    output_path: str = "grid_topology.html",
    layout: str = "spring",
    node_size: int = 14,
    seed: int = 42,
) -> None:
    """
    Draw and save an interactive grid topology HTML from a GridForge config Excel file.

    On hover, each node shows bus metadata and the assets with statuses on that bus.

    Args:
        grid_xlsx_path: Path to the grid configuration Excel file.
        output_path: Path to save the interactive HTML file.
        layout: Layout algorithm ("spring", "kamada_kawai", "circular", "shell").
        node_size: Node size for plotting.
        seed: Random seed for deterministic layouts where applicable.
    """
    try:
        import networkx as nx
    except ImportError as e:
        raise ImportError(
            "draw_grid_topology_interactive requires networkx. Install with `pip install networkx`."
        ) from e

    try:
        import plotly.graph_objects as go
    except ImportError as e:
        raise ImportError(
            "draw_grid_topology_interactive requires plotly. Install with `pip install plotly`."
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
        bus_idx = int(row["BUS_IDX"])
        bus_type = int(row["BUS_TYPE"])
        graph.add_node(bus_idx, bus_type=bus_type)
    for _, row in branch_df.iterrows():
        from_bus = int(row["F_BUS_IDX"])
        to_bus = int(row["T_BUS_IDX"])
        graph.add_edge(from_bus, to_bus)

    if layout == "spring":
        pos = nx.spring_layout(graph, seed=seed)
    elif layout == "kamada_kawai":
        pos = nx.kamada_kawai_layout(graph)
    elif layout == "circular":
        pos = nx.circular_layout(graph)
    elif layout == "shell":
        pos = nx.shell_layout(graph)
    else:
        raise ValueError(
            f"Unsupported layout '{layout}'. Use one of: spring, kamada_kawai, circular, shell."
        )

    bus_asset_status_count_by_sheet: Dict[int, Dict[str, Dict[str, int]]] = {
        int(b): {} for b in graph.nodes
    }
    for sheet_name in xls.sheet_names:
        if sheet_name in {"branch", "bus"}:
            continue
        df = xls.parse(sheet_name)
        if df.empty:
            continue

        bus_columns = []
        for col in df.columns:
            col_upper = str(col).upper()
            if col_upper in {"BUS_IDX"} or col_upper.endswith("_BUS_IDX"):
                bus_columns.append(col)

        if len(bus_columns) == 0:
            continue

        status_col = None
        if "STATUS" in df.columns:
            status_col = "STATUS"

        for col in bus_columns:
            values = pd.to_numeric(df[col], errors="coerce")
            for row_idx, bus_val in values.items():
                if pd.isna(bus_val):
                    continue
                bus_idx = int(bus_val)
                if bus_idx not in bus_asset_status_count_by_sheet:
                    continue

                status_text = "N/A"
                if status_col is not None:
                    raw_status = df.at[row_idx, status_col]
                    if pd.notna(raw_status):
                        # Keep integer-like statuses compact (0/1).
                        try:
                            status_float = float(raw_status)
                            status_text = str(int(status_float)) if status_float.is_integer() else str(status_float)
                        except (TypeError, ValueError):
                            status_text = str(raw_status)

                if sheet_name not in bus_asset_status_count_by_sheet[bus_idx]:
                    bus_asset_status_count_by_sheet[bus_idx][sheet_name] = {}
                status_count_map = bus_asset_status_count_by_sheet[bus_idx][sheet_name]
                status_count_map[status_text] = status_count_map.get(status_text, 0) + 1

    edge_x = []
    edge_y = []
    for i, j in graph.edges():
        x0, y0 = pos[i]
        x1, y1 = pos[j]
        edge_x.extend([x0, x1, None])
        edge_y.extend([y0, y1, None])

    color_by_type = {1: "lightblue", 2: "orange", 3: "red"}
    label_by_type = {1: "PQ", 2: "PV", 3: "Slack"}

    node_ids = list(graph.nodes())
    node_x = []
    node_y = []
    node_text = []
    node_type_name = []
    for bus_idx in node_ids:
        x, y = pos[bus_idx]
        bus_type = int(graph.nodes[bus_idx]["bus_type"])
        bus_type_name = label_by_type.get(bus_type, "Other")
        node_x.append(x)
        node_y.append(y)
        node_type_name.append(bus_type_name)

        asset_hits = bus_asset_status_count_by_sheet.get(int(bus_idx), {})
        if len(asset_hits) == 0:
            asset_text = "None"
        else:
            def _status_sort_key(status_key: str) -> Tuple[int, str]:
                try:
                    return (0, f"{float(status_key):020.8f}")
                except (TypeError, ValueError):
                    return (1, status_key)

            parts = []
            for asset_name in sorted(asset_hits.keys()):
                status_count_map = asset_hits[asset_name]
                total_count = int(sum(status_count_map.values()))
                status_parts = [
                    f"{status}x{count}"
                    for status, count in sorted(status_count_map.items(), key=lambda item: _status_sort_key(item[0]))
                ]
                status_text = ", ".join(status_parts)
                parts.append(f"{asset_name}(count={total_count}, status={status_text})")
            asset_text = ", ".join(parts)
        node_text.append(
            f"Bus: {bus_idx}<br>"
            f"Bus type: {bus_type_name}<br>"
            f"Assets: {asset_text}"
        )

    edge_trace = go.Scatter(
        x=edge_x,
        y=edge_y,
        mode="lines",
        line=dict(width=1, color="#888"),
        hoverinfo="skip",
        showlegend=False,
    )
    node_traces = []
    legend_order = ["PQ", "PV", "Slack", "Other"]
    for type_name in legend_order:
        indices = [i for i, t in enumerate(node_type_name) if t == type_name]
        if len(indices) == 0:
            continue
        node_traces.append(
            go.Scatter(
                x=[node_x[i] for i in indices],
                y=[node_y[i] for i in indices],
                mode="markers+text",
                text=[str(node_ids[i]) for i in indices],
                textposition="top center",
                hovertemplate="%{customdata}<extra></extra>",
                customdata=[node_text[i] for i in indices],
                marker=dict(
                    size=node_size,
                    color=color_by_type.get(
                        {"PQ": 1, "PV": 2, "Slack": 3}.get(type_name, -1), "gray"
                    ),
                    line=dict(width=1, color="black"),
                ),
                name=type_name,
                showlegend=True,
            )
        )

    fig = go.Figure(data=[edge_trace] + node_traces)
    fig.update_layout(
        title="Interactive Grid Topology",
        template="simple_white",
        legend=dict(title="Bus Type"),
        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
        margin=dict(l=10, r=10, t=40, b=10),
    )
    fig.write_html(output_path, include_plotlyjs="cdn")
