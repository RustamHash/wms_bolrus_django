import re
from pathlib import Path

ROOT = Path(r"d:\projects\wms_BolRus_django\templates")
SKIP = {"base.html", "tsd_base.html", "masters/nav.html", "partials/pallet_weight.html"}


def ternaries(text: str) -> str:
    pattern = re.compile(r"\{\{((?:(?!\{\{|\}\}).)+?) if ((?:(?!\{\{|\}\}).)+?) else ((?:(?!\{\{|\}\}).)+?)\}\}")

    def repl(match):
        yes, cond, no = match.group(1).strip(), match.group(2).strip(), match.group(3).strip()
        yes_html = yes if yes.startswith("<") or yes == "" else "{{ " + yes + " }}"
        no_html = no if no.startswith("'") or no.startswith('"') else "{{ " + no + " }}"
        if (yes.startswith("'") and yes.endswith("'")) or (yes.startswith('"') and yes.endswith('"')):
            yes_html = yes[1:-1]
        if (no.startswith("'") and no.endswith("'")) or (no.startswith('"') and no.endswith('"')):
            no_html = no[1:-1]
        return "{% if " + cond + " %}" + yes_html + "{% else %}" + no_html + "{% endif %}"

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def convert(path: Path, text: str) -> str:
    rel = str(path.relative_to(ROOT)).replace("\\", "/")
    if rel in SKIP:
        return text
    lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("{% from ") or stripped.startswith("{%- from "):
            continue
        if stripped.startswith("{% set ") or stripped.startswith("{%- set "):
            continue
        if stripped.startswith("{% macro") or stripped.startswith("{%- endmacro") or stripped.startswith("{% endmacro"):
            continue
        lines.append(line)
    text = "".join(lines)
    text = text.replace("loop.last", "forloop.last").replace("loop.index", "forloop.counter")
    text = text.replace("|sum(attribute='qty_kg')", "|sum_attr:'qty_kg'")
    text = text.replace("|sum(attribute='gross_kg')", "|sum_attr:'gross_kg'")
    text = text.replace("|map(attribute='name')|join(', ')", "|join_attr:'name'")
    text = text.replace("|string", "")
    text = text.replace('{% if posted %}', "{% if line.id in posted_ids %}")
    text = text.replace("{% for line in (order.lines if order else []) %}", "{% for line in order.lines.all %}")
    text = text.replace("{% with next_url=\"/tasks/\" ~ task.id %}", "")
    text = text.replace("{% with next_url=\"/tsd/tasks/\" ~ task.id %}", "")
    text = text.replace("{% endwith %}", "")
    text = text.replace("user and user.", "user.is_authenticated and user.")
    text = text.replace(".units[0]", ".units.all.0")
    text = text.replace(".grades[0]", ".grades.all.0")
    text = text.replace(".units|length", ".units.all|length")
    text = text.replace("{% if line.task_lines %}", "{% if line.task_lines.all %}")
    text = text.replace("{% if not line.task_lines %}", "{% if not line.task_lines.all %}")
    text = text.replace('{% if movement.pallet.units %}', "{% if movement.pallet.units.all %}")
    text = text.replace('{% if kind in [\'calibers\', \'grades\'] %}', "{% if kind == 'calibers' or kind == 'grades' %}")
    text = text.replace('{% if unit.status not in ["shipped", "rejected"] %}', '{% if unit.status != "shipped" and unit.status != "rejected" %}')
    text = text.replace("{{ label(movement.pallet.pallet_type) }}", "{% pallet_label movement.pallet.pallet_type %}")
    text = text.replace('data-tare="{{ tare(key) }}"', 'data-tare="{% pallet_tare key %}"')
    text = text.replace("{{ label(key) }}", "{% pallet_label key %}")
    text = ternaries(text)
    text = re.sub(
        r"(<form\b[^>]*>)",
        lambda m: m.group(1) + "{% csrf_token %}" if "csrf_token" not in m.group(1) else m.group(1),
        text,
    )
    if "{% load wms %}" not in text:
        text = "{% load wms %}\n" + text
    if 'name="next"' in text and "next_url" in text:
        text = text.replace('value="{{ next_url }}"', 'value="/tasks/{{ task.id }}"')
    return text


for path in ROOT.rglob("*.html"):
    original = path.read_text(encoding="utf-8")
    updated = convert(path, original)
    if updated != original:
        path.write_text(updated, encoding="utf-8")
print("converted")
