"""Only generated display coordinates can reach Plotly."""
from eda.viz.models import ChartSpec
from decimal import Decimal


def validate_chart(spec, rows):
    spec = ChartSpec.model_validate(spec)
    fields = {spec.x_field, spec.y_field}
    if spec.series_field:
        fields.add(spec.series_field)
    if not rows or not spec.completeness or any(not fields <= row.keys() for row in rows):
        raise ValueError("invalid display coordinates")
    return spec


def figure(spec, rows):
    import plotly.graph_objects as go
    spec = validate_chart(spec, rows)
    ordered = sorted(rows, key=lambda r: r[spec.x_field]) if spec.x_kind == "temporal" else rows
    x = [r[spec.x_field] for r in ordered]
    # Only the visual renderer uses floating point. Tables retain exact strings.
    y = [None if r[spec.y_field] is None else float(Decimal(str(r[spec.y_field]))) for r in ordered]
    trace = go.Bar(x=x, y=y) if spec.chart_type == "bar" else go.Scatter(x=x, y=y, mode="lines+markers")
    result = go.Figure(trace)
    result.update_layout(title=spec.title, yaxis_title=spec.y_unit,
                         xaxis_type="date" if spec.x_kind == "temporal" else "category")
    return result
