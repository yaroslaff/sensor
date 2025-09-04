import socket

def connect46(host, port, timeout=5):
    """ connect to host:port over IPv4/IPv6 """
    s = None
    for res in socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM):
        af, socktype, proto, canonname, sa = res
        try:
            s = socket.socket(af, socktype, proto)
            s.settimeout(timeout)
            s.connect(sa)
            return s
        except Exception as e:
            if s is not None:
                s.close()
            raise