"""Windows usbmux workaround for pymobiledevice3's second-socket probe."""

import asyncio
import plistlib
import sys

from pymobiledevice3 import usbmux


def install_same_socket_probe():
    """Keep the ReadBUID socket for subsequent usbmux requests on Windows."""
    if sys.platform != "win32" or getattr(usbmux.MuxConnection.create, "_bloom_same_socket", False):
        return

    async def create(usbmux_address=None):
        sock = await usbmux.MuxConnection.create_usbmux_socket(usbmux_address=usbmux_address)
        try:
            probe = usbmux.usbmuxd_request.build({
                "header": {
                    "version": usbmux.usbmuxd_version.PLIST,
                    "message": usbmux.usbmuxd_msgtype.PLIST,
                    "tag": 1,
                },
                "data": plistlib.dumps({"MessageType": "ReadBUID"}),
            })
            await asyncio.get_running_loop().sock_sendall(sock, probe)
            response = usbmux.usbmuxd_response.parse(await usbmux.MuxConnection._recv_packet(sock))
            if response.header.version == usbmux.usbmuxd_version.PLIST:
                connection = usbmux.PlistMuxConnection(sock)
            elif response.header.version == usbmux.usbmuxd_version.BINARY:
                connection = usbmux.BinaryMuxConnection(sock)
            else:
                raise usbmux.MuxVersionError(f"Unsupported usbmux version: {response.header.version}")
            connection._tag = 2
            return connection
        except BaseException:
            sock.close()
            raise

    create._bloom_same_socket = True
    usbmux.MuxConnection.create = staticmethod(create)
