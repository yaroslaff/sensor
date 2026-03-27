import shlex
import urllib.parse
import requests

from forcediphttpsadapter.adapters import ForcedIPHTTPSAdapter
from .version import __version__

user_agent = 'OkerrSensor/{}'.format(__version__)

def rget(url, options='', allow_redirects=True):

    verify = True
    headers = {
        'User-Agent': user_agent
    }
    
    
    url_parsed = urllib.parse.urlparse(url)
    url_scheme = url_parsed.scheme
    if ':' in url_parsed.netloc:
        url_host = url_parsed.netloc.split(':')[0]
    else:
        url_host = url_parsed.netloc

    o = dict()
    for s in shlex.split(options):
        if '=' in s:
            k,v = s.split('=',1)
            o[k]=v
        else:
            o[s]=True
            
    if 'ssl_noverify' in  o:
        verify = False
    
    session = requests.Session()
    params = dict()
    if 'addr' in o:
        if url_scheme == 'https':
            # https
            session.mount(url, ForcedIPHTTPSAdapter(dest_ip=o['addr']))
        else:
            # http
            url_changed = url_parsed._replace(netloc = o['addr'])
            headers['Host'] = url_host
            url = urllib.parse.urlunparse(url_changed)

    r = session.get(
        url, verify=verify, headers=headers, allow_redirects=allow_redirects, timeout=3, 
        params=params)
    return r
