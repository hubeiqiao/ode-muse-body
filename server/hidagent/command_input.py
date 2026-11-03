"""Literal dictation spans, inspired by savka777/jev-use CommandInput (MIT).

The executor copies the user's text; Jev selects when/where to enter it.
"""
import re
import json


def dictated_text(command: str) -> str | None:
    # Calculation input belongs to the calculator workflow, not free dictation.
    if re.search(r'输入并?计算|输入并?求值', command):
        return None
    match = re.search(r'(?:输入|键入|填写|打字)(?:以下(?:文字|内容)|文字|内容)?[：:\s]*(.+)|\btype(?:\s+in|\s+this:)?[：:\s]+(.+)', command, re.I)
    if not match:
        return None
    rest = (match.group(1) or match.group(2)).strip()
    if rest.startswith('\"'):
        try:
            literal,_=json.JSONDecoder().raw_decode(rest)
            if isinstance(literal,str):return literal
        except ValueError:pass
    if rest[:1] in ('「', '“', '"', "'"):
        end = {'「': '」', '“': '”', '"': '"', "'": "'"}[rest[0]]
        closing = rest.find(end, 1)
        return rest[1:closing] if closing >= 1 else None
    rest = re.split(r'[，,。]\s*(?:然后|再)(?:点击|按|发送|提交|打开)|\s+(?:and|then)\s+(?:press|click|tap|send|submit|open)\b', rest, maxsplit=1, flags=re.I)[0]
    return rest.strip() or None


def standalone_dictation(command: str) -> str | None:
    if not re.match(r'^\s*(?:输入|键入|填写|打字|type\b)', command, re.I):
        return None
    return dictated_text(command)
