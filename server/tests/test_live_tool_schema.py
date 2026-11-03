"""Gemini Live rejects the whole setup (close 1007) when any enum holds a non-string."""
from hidagent import ipad_demo_flows, live_voice


def _non_string_enums(schema, path):
    if isinstance(schema, dict):
        if 'enum' in schema and not all(isinstance(v, str) for v in schema['enum']):
            yield path
        for key, value in schema.items():
            yield from _non_string_enums(value, f'{path}.{key}')
    elif isinstance(schema, list):
        for i, value in enumerate(schema):
            yield from _non_string_enums(value, f'{path}[{i}]')


def test_live_tool_enums_are_strings():
    declarations = [ipad_demo_flows.TOOL]
    for tools in (live_voice.TOOLS, live_voice.CAMERA_TOOLS):
        declarations += tools[0]['functionDeclarations']
    bad = [p for d in declarations for p in _non_string_enums(d.get('parameters', {}), d['name'])]
    assert bad == []
