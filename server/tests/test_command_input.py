import pytest
from hidagent.command_input import dictated_text, standalone_dictation

@pytest.mark.parametrize('command,text', [
    ('输入 hello2026', 'hello2026'), ('输入「你好，世界」', '你好，世界'),
    ('type hello then press Enter', 'hello'), ('打开备忘录，输入“hello world”', 'hello world'),
    ('输入hello，然后点击发送', 'hello'), ('type this: Hello!', 'Hello!'),
    ('输入并计算 2+2', None), ('输入「未结束', None)])
def test_literal_is_copied_without_generation(command,text):
    assert dictated_text(command) == text

def test_navigation_is_not_treated_as_immediate_dictation():
    assert standalone_dictation('打开备忘录，输入「hello」') is None
    assert standalone_dictation('输入「hello」') == 'hello'
