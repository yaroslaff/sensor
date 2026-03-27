import socket
import sys
import requests

from .exceptions import CheckException
from .rget import rget

def check_httpgrep(url: str, musthave: str, mustnothave: str, options: str) -> str:

    try:
        r = rget(url, options)

        if r.status_code != 200:
            raise CheckException("Status code: {} (not 200)".format(str(r.status_code)))

        # encoding = chardet.detect(r.content)['encoding']
        # print("Detected encoding: {}".format(encoding))
        # ucontent = r.content.decode(encoding or 'utf-8', errors='replace')
        ucontent = r.text
        
        # check if it has musthave
        if len(musthave)>0:
            if ucontent.find(musthave) == -1:
                raise CheckException("Content has no substring '{}'".format(musthave))

        if len(mustnothave)>0:
            if ucontent.find(mustnothave) >= 0:
                raise CheckException("Content has substring '{}'".format(mustnothave))

        return ""
    
    except requests.exceptions.RequestException as e:
        raise CheckException('HTTP requests error: {}'.format(str(e)))

