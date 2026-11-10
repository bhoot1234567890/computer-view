import importlib.util
from pathlib import Path
import pytest

HANDSHAKE = b'RFB 003.008\n\x01\x01'
UPDATE = b'\x03\x01' + b'\x00' * 8
KEY = b'\x04\x01\x00\x00\x00\x00\x00A'
POINTER = b'\x05\x01\x00\x01\x00\x02'


def module():
    path = Path(__file__).parents[1] / 'computer_view' / 'rfb.py'
    assert path.exists(), 'RFB filter missing'
    spec = importlib.util.spec_from_file_location('rfb_filter', path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


WATCH = [b'\x00' + bytes(19), b'\x02\x00\x00\x02' + bytes(8),
         b'\x96\x01' + bytes(8), b'\xf8' + bytes(7) + b'\x03abc']
INPUT = [KEY, POINTER, b'\xff\x00' + bytes(10), b'\xfa' + bytes(3),
         b'\xfb\x00\x00\x10\x00\x10\x01\x00' + bytes(16)]
CLIPBOARD = [b'\x06' + bytes(3) + b'\x00\x00\x00\x03abc',
             b'\x06' + bytes(3) + b'\xff\xff\xff\xfdabc']


@pytest.mark.parametrize('message', WATCH + INPUT + CLIPBOARD)
def test_messages_fragmented_at_every_boundary(message):
    for allowed in (False, True):
        expected = message if message in WATCH or (allowed and message in INPUT) else b''
        for split in range(len(message) + 1):
            f = ready(allowed)
            assert f.feed(message[:split]) + f.feed(message[split:]) == expected
            assert f.feed(UPDATE) == UPDATE


@pytest.mark.parametrize('handshake', [b'RFB 003.003\n', b'RFB 003.008\n\x02',
                                     b'RFB 003.008\n\x01\x02',
                                     b'RFB 003.008\n\x01\x00'])
def test_handshake_rejects_other_protocol_or_security(handshake):
    f = module().ClientFilter(lambda: True)
    with pytest.raises(ValueError):
        f.feed(handshake)
    with pytest.raises(ValueError):
        f.feed(HANDSHAKE)


def test_unknown_type_permanently_closes_parser():
    f = ready(True)
    with pytest.raises(ValueError):
        f.feed(b'\x07')
    with pytest.raises(ValueError):
        f.feed(UPDATE)


@pytest.mark.parametrize('header', [b'\x06' + bytes(3) + b'\x01\x00\x00\x00',
                                  b'\x06' + bytes(3) + b'\x80\x00\x00\x00',
                                  b'\xf8' + bytes(7) + b'\x41',
                                  b'\xff\x01' + bytes(10)])
def test_untrusted_length_or_subtype_rejected_early(header):
    with pytest.raises(ValueError):
        ready(True).feed(header)


def test_extended_pointer_rejected_without_server_negotiation_state():
    # Otherwise the server may consume six bytes while the firewall consumes
    # seven, allowing subsequent watch messages to be reinterpreted as input.
    for allowed in (False, True):
        with pytest.raises(ValueError):
            ready(allowed).feed(b'\x05\x80\x00\x01\x00\x02\x01')


def test_release_forwarded_inputs_after_authority_is_gone():
    allowed = True
    f = module().ClientFilter(lambda: allowed)
    f.feed(HANDSHAKE)
    qemu = b'\xff\x00\x00\x01\x00\x00\x00B\x00\x00\x00\x30'
    assert f.feed(KEY + KEY + qemu + POINTER) == KEY + KEY + qemu + POINTER
    allowed = False
    other_key = KEY[:-1] + b'C'
    assert f.feed(other_key + b'\x05\x02\x00\x03\x00\x04') == b''
    assert f.release_inputs() == (b'\x04\x00' + KEY[2:] +
                                  b'\xff\x00\x00\x00' + qemu[4:] +
                                  b'\x05\x00' + POINTER[2:])
    assert f.release_inputs() == b''


def test_normal_key_and_pointer_ups_remove_held_state():
    f = ready(True)
    assert f.feed(KEY + POINTER) == KEY + POINTER
    keyup = b'\x04\x00' + KEY[2:]
    pointerup = b'\x05\x00' + POINTER[2:]
    assert f.feed(keyup + pointerup) == keyup + pointerup
    assert f.release_inputs() == b''


def test_failed_feed_does_not_track_bytes_that_were_never_returned():
    f = ready(True)
    f.feed(KEY)
    with pytest.raises(ValueError):
        f.feed((KEY[:-1] + b'B') + b'\x07')
    assert f.release_inputs() == b'\x04\x00' + KEY[2:]


def test_pressed_key_tracking_is_bounded():
    f = ready(True)
    for key in range(1024):
        message = b'\x04\x01\x00\x00' + key.to_bytes(4, 'big')
        assert f.feed(message) == message
    with pytest.raises(ValueError):
        f.feed(b'\x04\x01\x00\x00' + (1024).to_bytes(4, 'big'))
    releases = f.release_inputs()
    assert len(releases) == 1024 * 8
    assert f.release_inputs() == b''


def test_buffer_limit():
    with pytest.raises(ValueError):
        ready().feed(bytes(16 * 1024 * 1024 + 1))


def ready(allowed=False):
    f = module().ClientFilter(lambda: allowed)
    assert f.feed(HANDSHAKE) == HANDSHAKE
    return f


@pytest.mark.parametrize('split', range(len(HANDSHAKE) + 1))
def test_fragmented_handshake_and_coalesced_view_only(split):
    f = module().ClientFilter(lambda: False)
    assert f.feed(HANDSHAKE[:split]) + f.feed(HANDSHAKE[split:]) == HANDSHAKE
    assert f.feed(UPDATE + KEY + POINTER + UPDATE) == UPDATE + UPDATE


@pytest.mark.parametrize('message', [KEY, POINTER])
def test_each_input_split_and_dynamic_authority(message):
    for split in range(len(message)):
        allowed = True
        f = module().ClientFilter(lambda: allowed)
        assert f.feed(HANDSHAKE) == HANDSHAKE
        assert f.feed(message[:split]) == b''
        allowed = False
        assert f.feed(message[split:]) == b''
        allowed = True
        assert f.feed(message) == message
