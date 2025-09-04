import socket
import sys

from .exceptions import CheckException

def check_tcpport(host, port, timeout=5, substr=None):
    sock = None
    try:
        # Разрешаем хост в IP-адрес (поддерживает IPv4 и IPv6)
        addrinfo = socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        
        # Пытаемся подключиться к каждому из разрешенных адресов
        for res in addrinfo:
            af, socktype, proto, canonname, sa = res
            try:
                sock = socket.socket(af, socktype, proto)
                sock.settimeout(timeout)
                
                sock.connect(sa)
                
                if substr:
                    data = sock.recv(1024).decode('utf-8', errors='ignore')  # Читаем до 1024 байт                    
                    if substr not in data:
                        sock.close()
                        raise CheckException(f"Connected, but no '{substr}' in {data!r}")
                    # banned match
                    sock.close()
                    return data[:100]

                # no need to check banned
                sock.close()
                return f"Connected to {sa[0]}:{port}"
            except ConnectionRefusedError as e:
                raise CheckException(f"Connection refused to {sa[0]}:{port}: {e}")
            except TimeoutError as e:
                raise CheckException(f"Timeout on {sa[0]}:{port}: {e}")
            except socket.error as e:
                raise CheckException(f"socket.error on {sa[0]}:{port}: {e}")
            finally:
                if sock:
                    sock.close()  # Закрываем соединение в любом случае
        
        raise CheckException(f"Can not connect (2) to {sa[0]}:{port}: {e}")
    except socket.gaierror as e:
        raise CheckException(f"Can not resolve {host}: {e}")

if __name__ == "__main__":
    host = sys.argv[1]  # Can be an IPv4, IPv6, or hostname
    port = int(sys.argv[2])  # Port to test
    check_tcpport(host, port)
