import sys
import socket
import struct

import time
import threading

DMX_SIZE = 512


class DmxConnection(object):
    """Sends DMX messages over the network."""

    # See https://en.wikipedia.org/wiki/Art-Net
    HEADER = (
        b"A",
        b"r",
        b"t",
        b"-",
        b"N",
        b"e",
        b"t",
        b"\x00",
        b"\x00",
        b"\x50",
        b"\x00",
        b"\x0e",  # Version 14
        b"\x00",
        b"\x00",
        b"\x00",
        b"\x00",  # Universe 0 (Only supportes 1 universe)
        b"\x02",
        b"\x00",
    )  # 512 Channels

    HEADER_BYTES = struct.pack("c" * len(HEADER), *HEADER)

    def __init__(self, address):
        """Constructor.

        address (tuple): Host and port. Example: ("localhost", 8000).
        """
        self._dmx_frame = [0] * DMX_SIZE
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._address = address
        self._connected = True

    def set_channel(self, channel, value, autorender=False):
        """Sets the desired DMX channel givan a value.

        channel (int): The channel to set.
        value (int): The value to set the channel [0, 255].
        autorender (bool): Whether to send the DMX message immediately.
        """
        if not 1 <= chan <= DMX_SIZE:
            raise RuntimeError(f"Invalid DMX channel {channel}")
        if not 0 <= value < 256:
            raise RuntimeError(f"Invalid DMX value {value}")

        self._dmx_frame[channel - 1] = value
        if autorender:
            self.render()

    def set_channels(self, start_channel, values):
        """Sets multiple DMX channels.

        start_channel (int): The initial DMX channel to set.
        values (list): The list of values
        """
        if not (
            (1 <= start_channel <= DMX_SIZE)
            and (start_channel + len(values) <= DMX_SIZE + 1)
        ):
            raise RuntimeError(f"Invalid indices: {start_channel}")
        start_channel = start_channel - 1
        self._dmx_frame[start_channel : start_channel + len(values)] = values

    def clear(self):
        """Clears all channels to zero. blackout."""
        self._dmx_frame = [0] * DMX_SIZE

    def render(self):
        """Sends the DMX message over the network to update the DMX output."""
        try:
            self._socket.sendto(
                self.HEADER_BYTES + bytes(self._dmx_frame), self._address
            )
            self._connected = True
        except ValueError as e:
            self._connected = False
            print(self._dmx_frame)
            # TODO: Remove this one all bugs have been fixed
            raise e

    def get_dmx_frame(self):
        """Return the DMX frame."""
        return tuple(self._dmx_frame)

    def connected(self):
        return self._connected


class NodeDmxServer:
    """Server that listens for packets containing DMX information from one or more NodeDmxClients.

    A packet should contain an array of 1 or more numbers. The first
    number is always the DMX offset, followed by zero or more DMX channels.
    """

    BUFFER_SIZE = 512

    def __init__(self, period, port, dmx_connection, ip="127.0.0.1", debug=False):
        """Constructor.

        period (float): The update rate of the DMX controller.
        port (int): The port to listen to for packets.
        dmx_connection (DmxConnection): The outgoing DMX connection.
        ip (str): The IP address.
        debug (bool): If true, prints out the DMX frame.
        """
        self.period = period
        self.port = port
        self.dmx_connection = dmx_connection
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((ip, port))
        self.done = False
        self.debug = debug

    def start(self):
        """Starts the server.

        This is a blocking call.
        """

        def recv_thread():
            while not self.done:
                buffer, addr = self.socket.recvfrom(1024)
                n_bytes = len(buffer)
                if n_bytes <= 0:
                    print("Received empty packet!")
                    continue
                data = struct.unpack("B" * n_bytes, buffer)
                start_channel = data[0]
                if n_bytes > 1:
                    self.dmx_connection.set_channels(start_channel, data[1::])
                if self.debug:
                    print(self.dmx_connection.get_dmx_frame()[0])
            self.socket.close()
            print("Stopped DMX Server.")

        def send_thread():
            while not self.done:
                t0 = time.time()
                self.dmx_connection.render()
                t1 = time.time()
                duration = t1 - t0
                if duration < self.period:
                    time.sleep(self.period - duration)

        self.threads = [
            threading.Thread(target=recv_thread),
            threading.Thread(target=send_thread),
        ]

        for t in self.threads:
            t.daemon = True
            t.start()

    def stop(self):
        """Stop the server."""
        self.done = True
        for t in self.threads:
            t.join()


class NodeDmxClient:
    """Client that sends packets containing DMX information to a NodeDmxServer."""

    def __init__(self, server_addr, dmx_address, n_channels, debug=False):
        """Constructor.

        server_addr (tuple): The host and port of the DmxServer.
        dmx_address (int): The starting DMX address.
        n_channels (int): The number of channels this client will control.
        """
        self._server_addr = server_addr
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._dmx_address = dmx_address
        self._dmx_sub_frame = [0] * n_channels
        self._connected = True

    def set_channel(self, channel, value):
        self._dmx_sub_frame[channel - 1] = value

    def set_channels(self, start_channel, values):
        start_channel -= 1
        self._dmx_sub_frame[start_channel : start_channel + len(values)] = values

    def send_frame(self):
        packet = struct.pack("B", self._dmx_address)
        packet += bytes(self._dmx_sub_frame)
        try:
            self._socket.sendto(packet, self._server_addr)
            self._connected = True
        except:
            self._connected = False

    def clear(self):
        self._dmx_sub_frame = [0] * len(self._dmx_sub_frame)

    def connected(self):
        return self._connected
