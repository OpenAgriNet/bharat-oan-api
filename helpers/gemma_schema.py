"""Adapt tool schemas to the subset rendered by the Gemma 4 chat template."""
from copy import deepcopy


def gemma_compatible(schema: dict) -> dict:
    """Inline local definitions and express optional values with ``nullable``.

    This changes model guidance, not the tool's Python argument validator.
    Unsupported unions/references fail explicitly instead of losing alternatives.
    """
    schema = deepcopy(schema)
    definitions = schema.pop("$defs", {})

    def walk(node, stack=()):
        if isinstance(node, list):
            return [walk(value, stack) for value in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
                raise ValueError(f"Unsupported schema reference: {ref}")
            name = ref[len("#/$defs/"):].replace("~1", "/").replace("~0", "~")
            if name not in definitions:
                raise ValueError(f"Unknown schema definition: {name}")
            if name in stack:
                raise ValueError(f"Recursive schema is not supported: {name}")
            merged = {**definitions[name], **{k: v for k, v in node.items() if k != "$ref"}}
            return walk(merged, (*stack, name))
        if "oneOf" in node or "allOf" in node:
            raise ValueError("Unsupported schema composition: oneOf/allOf")
        if "anyOf" in node:
            alternatives = node["anyOf"]
            values = [value for value in alternatives if value != {"type": "null"}]
            if len(values) != 1:
                raise ValueError(f"Unsupported schema union: {alternatives}")
            merged = {**walk(values[0], stack), **{k: v for k, v in node.items() if k != "anyOf"}}
            if len(values) < len(alternatives):
                merged["nullable"] = True
            return walk(merged, stack)
        return {key: walk(value, stack) for key, value in node.items()}

    return walk(schema)
