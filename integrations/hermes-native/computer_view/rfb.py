"""Incremental client-side RFB 3.8 / security None message firewall.

One instance per connection, before any browser bytes reach Xvnc. Close both
transports on ValueError (including authority callback failure). Handshake
validation here is client-only; the caller must require a trusted local Xvnc
server with RFB 3.8 / None. Unknown messages fail closed, clipboard is always
dropped. Message layouts follow noVNC core/rfb.js RFB.messages.

Authority is checked per complete input message, never per WebSocket frame.
The caller MUST send/drain release_inputs() directly to Xvnc before handback,
disconnect, or expiry, serialized with forwarding. This synthesizes releases for
forwarded held keys/buttons; it cannot verify delivery after transport failure.
Extended marker-bit pointer events are refused without server negotiation state.
"""

MAX_BUFFER = 16 * 1024 * 1024
MAX_PRESSED_KEYS = 1024


class ClientFilter:
    def __init__(self, may_input):
        self._may_input = may_input
        self._buffer = bytearray()
        self._stage = 0
        self._failed = False
        self._pressed = {}
        self._pointer = None

    def feed(self, data: bytes) -> bytes:
        if self._failed:
            raise ValueError('RFB parser is closed')
        try:
            if not isinstance(data, bytes):
                raise ValueError('RFB data must be bytes')
            if len(self._buffer) + len(data) > MAX_BUFFER:
                raise ValueError('RFB buffer limit exceeded')
            self._buffer.extend(data)
            output = bytearray()
            # Commit held state only when this call actually returns bytes.
            pressed = self._pressed.copy()
            pointer = self._pointer
            offset = 0
            b = self._buffer
            while offset < len(b):
                available = len(b) - offset
                if self._stage < 3:
                    size = 12 if self._stage == 0 else 1
                    if available < size:
                        break
                    message = bytes(b[offset:offset + size])
                    if self._stage == 0 and message != b'RFB 003.008\n':
                        raise ValueError('only RFB 3.8 is supported')
                    if self._stage == 1 and message != b'\x01':
                        raise ValueError('only security None is supported')
                    if self._stage == 2 and message != b'\x01':
                        raise ValueError('ClientInit must be shared to avoid evicting viewers')
                    self._stage += 1
                    output.extend(message)
                    offset += size
                    continue
                kind = b[offset]
                size = self._message_size(offset, available)
                if size is None or available < size:
                    break
                message = bytes(b[offset:offset + size])
                if kind == 255 and message[1] != 0:
                    raise ValueError('unsupported QEMU subtype')
                if kind != 6 and (kind not in (4, 5, 250, 251, 255)
                                  or self._may_input()):
                    pointer = self._track(message, pressed, pointer)
                    output.extend(message)
                offset += size
            del b[:offset]
            self._pressed = pressed
            self._pointer = pointer
            return bytes(output)
        except Exception as exc:
            self._failed = True
            self._buffer.clear()
            if isinstance(exc, ValueError):
                raise
            raise ValueError('RFB filtering failed') from exc

    def release_inputs(self) -> bytes:
        """Return synthetic releases, once, even after authority/parser failure.

        The caller MUST write/drain these bytes directly to the same Xvnc stream
        before handback/disconnect/expiry, serialized with normal writes. Do not
        feed them back through the authority filter. State tracks returned bytes,
        so the caller must actually forward every successful feed() result.
        """
        output = b''.join(self._pressed.values())
        if self._pointer is not None:
            output += b'\x05\x00' + self._pointer
        self._pressed.clear()
        self._pointer = None
        return output

    def _track(self, message, pressed, pointer):
        kind = message[0]
        if kind == 5:
            return message[2:6] if message[1] else None
        if kind == 4:
            identity = (kind, message[4:8])
            down = bool(message[1])
            release = b'\x04\x00' + message[2:]
        elif kind == 255:
            identity = (kind, message[4:12])
            down = bool(int.from_bytes(message[2:4], 'big'))
            release = b'\xff\x00\x00\x00' + message[4:]
        else:
            return pointer
        if down:
            if identity not in pressed and len(pressed) >= MAX_PRESSED_KEYS:
                raise ValueError('too many held RFB keys')
            pressed[identity] = release
        else:
            pressed.pop(identity, None)
        return pointer

    def _message_size(self, offset, available):
        b = self._buffer
        kind = b[offset]
        fixed = {0: 20, 3: 10, 4: 8, 150: 10, 250: 4, 255: 12}
        if kind in fixed:
            return fixed[kind]
        if kind == 5:
            if available < 2:
                return None
            # We do not observe the server's extension negotiation. Reject
            # marker-bit pointers rather than risk a 6/7-byte disagreement.
            if b[offset + 1] & 0x80:
                raise ValueError('extended pointer events require server negotiation tracking')
            return 6
        header = {2: 4, 6: 8, 248: 9, 251: 8}.get(kind)
        if header is None:
            raise ValueError('unsupported RFB client message')
        if available < header:
            return None
        if kind == 2:
            size = 4 + 4 * int.from_bytes(b[offset + 2:offset + 4], 'big')
        elif kind == 6:
            # ExtendedClipboard uses a negative signed length. Drop both forms
            # without decompressing/inspecting their attacker-controlled data.
            size = 8 + abs(int.from_bytes(b[offset + 4:offset + 8], 'big', signed=True))
        elif kind == 248:
            length = b[offset + 8]
            if length > 64:
                raise ValueError('Fence payload exceeds protocol maximum')
            size = 9 + length
        else:
            size = 8 + 16 * b[offset + 6]
        if size > MAX_BUFFER:
            raise ValueError('RFB declared message length exceeds limit')
        return size
