"""How the model daemon and its clients reach each other.

unix: a unix socket file at cfg.socket_path(), mode 0600. The default wherever
Python has unix sockets with descriptor passing (recvmsg and CMSG_SPACE).

tcp: 127.0.0.1 on a port the system picks. The daemon writes the port and a
random token into an endpoint file next to the socket path (modeld.port), which
only the user can read, and every request has to carry that token. Python on
Windows has no recvmsg, so it always takes this path. LEKS_MODELD_TRANSPORT=tcp
forces it on any system, which is how the Windows path is tested on macOS and
Linux.
"""
from __future__ import annotations

import json
import os
import socket
import stat

ENV = 'LEKS_MODELD_TRANSPORT'
HOST = '127.0.0.1'
TOKEN_HEX = 64            # secrets.token_hex(32)
MAX_FILE = 4096

# descriptor passing: the python docs list recvmsg and CMSG_SPACE as unix only
UNIX_OK = (hasattr(socket, 'AF_UNIX') and hasattr(socket, 'CMSG_SPACE')
           and hasattr(getattr(socket, 'socket', None), 'recvmsg'))


def kind():
    """'unix' or 'tcp'. tcp when the setting asks for it or the system has no unix sockets."""
    v = (os.environ.get(ENV) or '').strip().lower()
    if v == 'tcp' or not UNIX_OK:
        return 'tcp'
    return 'unix'


def endpoint_path(sock_path):
    """The file a tcp daemon writes its port and token into: modeld.sock -> modeld.port."""
    base, ext = os.path.splitext(sock_path)
    return (base if ext == '.sock' else sock_path) + '.port'


def read_endpoint(sock_path):
    """(port, token) of a tcp daemon, or None when there is no usable endpoint file.

    The file must be a plain file of this user that nobody else can read or write
    (the mode check runs where file modes exist; on windows the home folder's
    access rules do that job).
    """
    path = endpoint_path(sock_path)
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
        return None
    if os.name == 'posix' and (st.st_uid != os.getuid() or st.st_mode & 0o077):
        return None
    try:
        with open(path, 'rb') as f:
            d = json.loads(f.read(MAX_FILE))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    port, token = d.get('port'), d.get('token')
    if type(port) is not int or not 0 < port < 65536:
        return None
    if not isinstance(token, str) or len(token) != TOKEN_HEX:
        return None
    return port, token


def write_endpoint(sock_path, port, token):
    """Write the endpoint file atomically with mode 0600. Returns its path."""
    path = endpoint_path(sock_path)
    tmp = path + '.tmp'
    try:
        os.remove(tmp)
    except OSError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='ascii') as f:
        json.dump({'port': int(port), 'token': token, 'pid': os.getpid()}, f)
    os.replace(tmp, path)
    return path


def drop_endpoint(sock_path, token):
    """Remove the endpoint file, but only while it still holds this token (a later daemon may own it)."""
    ep = read_endpoint(sock_path)
    if ep is not None and ep[1] == token:
        try:
            os.remove(endpoint_path(sock_path))
        except OSError:
            pass


def present(sock_path, mode=None):
    """True when the socket file (unix) or a usable endpoint file (tcp) is there. Never connects."""
    if (mode or kind()) == 'tcp':
        return read_endpoint(sock_path) is not None
    try:
        return stat.S_ISSOCK(os.stat(sock_path).st_mode)
    except OSError:
        return False


def connect(sock_path, timeout, mode=None):
    """A connected socket and the token each request must carry (None for unix).

    Raises FileNotFoundError when there is no socket or endpoint file and
    ConnectionRefusedError when nothing listens, like a unix connect does.
    """
    if (mode or kind()) == 'unix':
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            c.settimeout(timeout)
            c.connect(sock_path)
        except BaseException:
            c.close()
            raise
        return c, None
    ep = read_endpoint(sock_path)
    if ep is None:
        raise FileNotFoundError(endpoint_path(sock_path))
    c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        c.settimeout(timeout)
        c.connect((HOST, ep[0]))
    except BaseException:
        c.close()
        raise
    return c, ep[1]


def with_token(req, token):
    """The request with the token added (tcp), or as it is (unix)."""
    if token is None:
        return req
    out = dict(req)
    out['token'] = token
    return out


def listen_tcp():
    """A listening socket on 127.0.0.1 and a port the system picks."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            # windows: no other process may bind the same port on top of ours
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        srv.bind((HOST, 0))
        srv.listen(16)
    except BaseException:
        srv.close()
        raise
    return srv
