import socket
import sys
import OpenSSL
import ssl
import re
import datetime
import shlex

from dns.exception import DNSException
import dns.resolver

from .exceptions import CheckException
from .connect46 import connect46

def check_sslcert(host: str, port: int, days: int, options):

    CA_CERTS = "/etc/ssl/certs/ca-certificates.crt"

    #
    # get cert and return notAfter (datetime), NO verification
    #
    def nafter_noverify(addr, host,port, options):
            
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((addr, port))

        ctx = ssl._create_unverified_context()
        ctx.verify_mode = ssl.CERT_NONE
        
        sslsock = ctx.wrap_socket(
            sock,
            server_hostname = host,
            # cert_reqs = ssl.CERT_NONE,
            )
                
        cert = sslsock.getpeercert(True)
        x509 = OpenSSL.crypto.load_certificate(OpenSSL.crypto.FILETYPE_ASN1, cert)
        
        m = re.match('(\d+)', x509.get_notAfter().decode('ascii'))
        if m:
            nafterstr = m.group(0)
        else:
            print("Cannot parse notAfter for {}:{}! '{}'".format(host,port,x509.get_notAfter().decode('ascii')))

        cert_nafter = datetime.datetime.strptime(nafterstr, '%Y%m%d%H%M%S')

        sock.close()
        sslsock.close()

        return cert_nafter

    #
    # get cert and return notAfter (datetime), verification
    #
    def nafter_verify(addr, host, port, options):

        ssl_date_fmt = r'%b %d %H:%M:%S %Y %Z'    
            
        # sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # sock.settimeout(5)
        # sock.connect((addr, port))
        sock = connect46(host, port, timeout=5)
        

        # !!! TODO FIXME check with wrong hostname (throws exception)
        ctx = ssl.create_default_context()

        sslsock = ctx.wrap_socket(
            sock,
            server_hostname = host,
            )            
        cert = sslsock.getpeercert()

        ssl.match_hostname(cert,host)
        nafterstr = cert['notAfter']
        cert_nafter = datetime.datetime.strptime(nafterstr, ssl_date_fmt)
        
        sock.close()
        sslsock.close()
        return cert_nafter


    o = dict()
    for s in shlex.split(options):
        if '=' in s:
            k,v = s.split('=',1)
            o[k]=v
        else:
            o[s]=True

    if 'addr' in o:
        addr = o['addr']
    else:
        addr = host

    try:
        
        if 'noverify' in o or 'ssl_noverify' in o:
            cert_nafter = nafter_noverify(addr, host, port, options)
        else:
            cert_nafter = nafter_verify(addr, host, port, options)

                    
        cur_date = datetime.datetime.utcnow()
                    
        #cert_nafter = datetime.datetime.strptime(cert['notAfter'], ssl_date_fmt)

        expire_days = int((cert_nafter - cur_date).days)

    except (socket.gaierror, socket.timeout, socket.error) as e:                
        details = 'socket error: {}'.format(str(e))                        
        # add details if this is hostname
        
        try:
            socket.inet_aton(host)
        except socket.error:          
            # this is hostname
            ips = list()
            my_resolver = dns.resolver.Resolver()
            try:
                answer = my_resolver.query(host, 'a')
                for rr in answer.rrset:
                    ips.append(rr.address)
            except DNSException:
                return 
            details += ' (DNS: {})'.format(' '.join(ips))
            raise CheckException(details)
        return
        
    except (ssl.CertificateError, ssl.SSLError) as e:
        raise CheckException('SSL error: {}'.format(str(e)))
        return 
        
    if expire_days > days:
        return "{} days left".format(expire_days)
    else:
        if expire_days > 0:
            raise CheckException(f'Certificate will expire soon (in {expire_days})')
