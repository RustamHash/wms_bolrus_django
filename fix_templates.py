import re
from pathlib import Path

root = Path(__file__).resolve().parent / "templates"


def for_else_to_empty(text: str) -> str:
    token = re.compile(r"\{%-?\s*(for|empty|endfor|if|elif|else|endif|with|endwith)\b")
    stack = []
    out = []
    pos = 0
    for match in re.finditer(r"\{%.*?%\}", text, flags=re.S):
        out.append(text[pos : match.start()])
        chunk = match.group(0)
        kind = token.search(chunk)
        name = kind.group(1) if kind else ""
        if name == "for":
            stack.append("for")
        elif name == "if":
            stack.append("if")
        elif name == "with":
            stack.append("with")
        elif name == "else" and stack and stack[-1] == "for":
            chunk = "{% empty %}"
        elif name == "endfor" and stack and stack[-1] == "for":
            stack.pop()
        elif name == "endif" and stack and stack[-1] == "if":
            stack.pop()
        elif name == "endwith" and stack and stack[-1] == "with":
            stack.pop()
        out.append(chunk)
        pos = match.end()
    out.append(text[pos:])
    return "".join(out)


def fix(text: str) -> str:
    if text.startswith("{% load wms %}\n{% extends"):
        first, second, rest = text.split("\n", 2)
        text = second + "\n" + first + "\n" + rest
    text = re.sub(r"\{%\s*with next_url=.*?%\}\s*", "", text)
    text = text.replace("|{% include", "{% include")
    text = text.replace(".items()", ".items")
    text = text.replace('.strftime("%d.%m.%Y %H:%M")', '|date:"d.m.Y H:i"')
    text = text.replace('.strftime("%d.%m.%Y")', '|date:"d.m.Y"')
    text = text.replace("headers|length + 1", "headers|length|add:1")
    text = text.replace(
        '{{ shipment.shipping_terms or "Не указаны" }}',
        '{% firstof shipment.shipping_terms "Не указаны" %}',
    )
    text = re.sub(r"\{\{\s*describe_line\(([^)]+)\)\s*\}\}", r"{{ \1|describe }}", text)
    text = re.sub(
        r"\(\s*([A-Za-z_][A-Za-z0-9_\.]*)\.get\(([^)]+)\)\s*or\s*0\s*\)\|kg",
        r"\1|get_item:\2|kg",
        text,
    )
    text = re.sub(
        r"([A-Za-z_][A-Za-z0-9_\.]*)\.get\(([^,)]+)(?:,\s*[^)]+)?\)",
        r"\1|label:\2",
        text,
    )
    return for_else_to_empty(text)


def main() -> None:
    for path in root.rglob("*.html"):
        old = path.read_text(encoding="utf-8")
        new = fix(old)
        if new != old:
            path.write_text(new, encoding="utf-8")
            print("fixed", path.relative_to(root).as_posix())


if __name__ == "__main__":
    main()
