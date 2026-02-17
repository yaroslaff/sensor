import socket
import sys
import OpenSSL
import ssl
import re
import datetime
import shlex
import fnmatch

import cryptography
from cryptography.x509.oid import ExtensionOID

from dns.exception import DNSException
import dns.resolver

from .exceptions import CheckException
from .connect46 import connect46

def check_sslcert(host: str, port: int, days: int, options):

    CA_CERTS = "/etc/ssl/certs/ca-certificates.crt"

    #
    # get cert and return notAfter (datetime), NO verification
    #

    def nafter(addr: str, hostname: str, port: int, verify: bool = True) -> datetime.datetime:
        context = ssl.create_default_context()
        if not verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        with socket.create_connection((addr, port), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as sslsock:
                der_cert = sslsock.getpeercert(binary_form=True)

        cert = cryptography.x509.load_der_x509_certificate(der_cert)
        return cert.not_valid_after_utc



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


        #if 'noverify' in o or 'ssl_noverify' in o:
        cert_nafter = nafter(addr, host, port, verify='noverify' not in o and 'ssl_noverify' not in o)
                    
        cur_date = datetime.datetime.now(datetime.timezone.utc)
                    
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
            raise CheckException(f'Certificate will expire soon (in {expire_days} days)')
