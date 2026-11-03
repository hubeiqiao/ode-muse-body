"""Receiver command construction only: never start UxPlay or touch a device."""

from unittest.mock import Mock

import pytest

from hidagent.screen import Screen


@pytest.fixture
def launch(monkeypatch):
    for name in ('UXPLAY_DISPLAY_SIZE', 'MIRROR_FRAME_HEIGHT'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('UXPLAY', '/offline/uxplay-wrapper')
    monkeypatch.setenv('UXPLAY_RESET_S', '15')
    popen = Mock(return_value=Mock(pid=123))
    monkeypatch.setattr('hidagent.screen.subprocess.Popen', popen)
    monkeypatch.setattr('hidagent.screen.threading.Thread', Mock())

    def run():
        Screen(name='iPad Test', fps=8, quality=75)._launch('fdsink fd=99', pass_fds=(99,))
        return popen.call_args.args[0]

    run.popen = popen
    return run


@pytest.mark.parametrize('unused_height', [None, '720', 'invalid'])
def test_unconfigured_receiver_command_is_byte_identical(launch, monkeypatch, unused_height):
    if unused_height is not None:
        monkeypatch.setenv('MIRROR_FRAME_HEIGHT', unused_height)
    assert launch() == [
        '/offline/uxplay-wrapper', '-n', 'iPad Test', '-nh', '-a', '-reset', '15', '-vs',
        'videorate drop-only=true ! video/x-raw,framerate=8/1 ! '
        'jpegenc quality=75 ! fdsink fd=99',
    ]
    assert launch.popen.call_args.kwargs['pass_fds'] == (99,)


@pytest.mark.parametrize('size', ['2266x1488', '1488x2266', '320x7680', '7680x320'])
@pytest.mark.parametrize('height', [None, '720'])
def test_native_display_advertised_with_scaled_jpeg_pipeline(launch, monkeypatch, size, height):
    monkeypatch.setenv('UXPLAY_DISPLAY_SIZE', size)
    if height is not None:
        monkeypatch.setenv('MIRROR_FRAME_HEIGHT', height)
    args = launch()
    assert args[args.index('-s') + 1] == size
    sink = args[args.index('-vs') + 1]
    assert f'videoscale ! video/x-raw,height={height or 1080},pixel-aspect-ratio=1/1' in sink
    assert sink.index('videorate') < sink.index('videoscale') < sink.index('jpegenc')
    assert sink.endswith('jpegenc quality=75 ! fdsink fd=99')


@pytest.mark.parametrize('size', ['2266x1488', '1488x2266'])
def test_mini_native_rounding_keeps_exact_template_sizes_without_forcing_aspect(launch, monkeypatch, size):
    monkeypatch.setenv('UXPLAY_DISPLAY_SIZE', size)
    args = launch()
    sink = args[args.index('-vs') + 1]
    # Height-only scaling negotiates 1645 / 709. Pad a single right column
    # rather than force every source (including unexpected 16:9) to one aspect.
    assert 'height=1080,pixel-aspect-ratio=1/1 ! videobox right=-1 ! jpegenc' in sink
    assert 'width=' not in sink


def test_no_padding_when_native_scaled_widths_are_already_even(launch, monkeypatch):
    monkeypatch.setenv('UXPLAY_DISPLAY_SIZE', '2048x1536')
    args = launch()
    assert 'videobox' not in args[args.index('-vs') + 1]


@pytest.mark.parametrize('size', ['1920', '2266X1488', '2266x1488 -a', '319x1488',
                                 '2266x7681', '0x1080', '-320x1080', '2266x1488\n'])
def test_invalid_display_size_fails_before_starting_child(launch, monkeypatch, size):
    monkeypatch.setenv('UXPLAY_DISPLAY_SIZE', size)
    with pytest.raises(ValueError, match='UXPLAY_DISPLAY_SIZE'):
        launch()
    launch.popen.assert_not_called()


@pytest.mark.parametrize('height', ['0', '-1', '1080.0', '1080 ! fakesink', 'invalid'])
def test_invalid_frame_height_fails_before_starting_child(launch, monkeypatch, height):
    monkeypatch.setenv('UXPLAY_DISPLAY_SIZE', '2266x1488')
    monkeypatch.setenv('MIRROR_FRAME_HEIGHT', height)
    with pytest.raises(ValueError, match='MIRROR_FRAME_HEIGHT'):
        launch()
    launch.popen.assert_not_called()
