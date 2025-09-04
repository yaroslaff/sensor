import OpenSSL
import ssl
import socket
import datetime
import sys
import hashlib
import requests
import logging
import subprocess
import whois
import aiodns
import dns.resolver
from dns.exception import DNSException
#import pyping
# import ping3
import icmplib
import re
import time
import string
import urllib.parse
import shlex
import select
import traceback
import argparse
import asyncio

import urllib3
urllib3.disable_warnings()

import async_dnsbl_client 

#if __name__ == '__main__':
#    from remotecheck.forcedip import ForcedIPHTTPSAdapter
#else:
#    from .forcedip import ForcedIPHTTPSAdapter                            
from forcediphttpsadapter.adapters import ForcedIPHTTPSAdapter

from .version import __version__
from .exceptions import CheckException
from .check_tcpport import check_tcpport
from .check_sslcert import check_sslcert


from .checkargs import checkargs
from .settings import settings


class Check(object):

    user_agent = 'OkerrSensor/{}'.format(__version__)

    def __init__(self, data=None):
        self.status = None
        self.oldstatus = None
        self.details = None
        self.set_args = dict()
        self.args = dict()
        self.msgtags = dict()
        self.logs = list()
        self.alerts = list()
        self.id = None
        self.name = None
        self.textid = None
        self.mtime = 0
        self.period = 0
        self.last_update = 0.0  # last time sent update to server
        self.throttle = 300      # how often sent updates (even if nothing changed)
        self.scheduled = 0.0
        self.problem = False
        self.fetch_id = None

        self.code = None

        # self.user_agent = 'remotecheck'

        self.allfields = [
            "name", "textid", "fullname", "fetch_id",
            "cm", "args", 
            "status", "details", "oldstatus",
            "mtime", "rs_url", "rs_name",
            "set_args","problem","logs","alerts",
            "code", "msgtags",
            "period","scheduled","last_update","throttle"
            ]

        self.actions = {
            'sslcert': self.action_sslcert,
            'sha1static': self.action_sha1static,
            'sha1dynamic': self.action_sha1dynamic,
            'ping': self.action_ping,
            'httpgrep': self.action_httpgrep,
            'httpstatus': self.action_httpstatus,
            'tcpport': self.action_tcpport,
            'whois': self.action_whois,
            'dns': self.action_dns,
            'dnsbl': self.action_dnsbl
        }

    def __str__(self):
        return "{}@{} = {} ({})".format(self.name, self.textid, self.status, self.details)
    
        out = ''
        for f in self.__dict__:
            if self.__dict__[f]:
                out += '{} = {}\n'.format(f, self.__dict__[f]) 
        return out
    
    def data(self):
        return self.__dict__
    
    def UNUSED_reborn(self):
        self.reschedule()
        self.msgtags = dict()
        self.oldstatus = self.status
        self.alerts = list()
        self.set_args = dict()
        self.logs = list()
    
    @staticmethod
    def from_request(data):
        check = Check()
        for attr in ['textid', 'name', 'period', 'throttle', 'mtime', 'cm', 'args']:
            if attr in data:
                setattr(check,attr,data[attr])

        for attr in ['name', 'textid']:
            # setattr(cr, attr, forceunicode(r[attr]))
            setattr(check, attr, data[attr])

        # cr.fullname = u'{}@{}'.format(forceunicode(cr.name), forceunicode(cr.textid))
        check.fullname = '{}@{}'.format(check.name, check.textid)

        #if rs_url:
        #    cr.rs_url = rs_url

        #if rs_name:
        #   cr.rs_name = rs_name

        check.oldstatus = None
        check.last_update = 0
        return check
    
    #
    # convert to dict for api_tproc_set
    #
    def response(self):
        r = dict()
        r['_task'] = 'tproc.reply'
        for field in ['name', 'textid', 'status', 'details', 'set_args', 'logs', 'alerts',
                      'mtime', 'problem', 'code']:
            r[field] = getattr(self, field)
            
        # make code message
        r['code_message'] = ''.join(["["+k+"]" for k in self.msgtags.keys()])      
        return r
    
    def serialize(self):
            
        d = dict()
        for field in self.allfields:
            if hasattr(self, field):
                d[field] = getattr(self, field)
        return d
    
    def reschedule(self, when = None):
        if when is None:
            when = time.time() + self.period
        
        self.scheduled = when

    def worthcache(self):
        return self.period <= 1800

    def suppress(self):
        # print "suppress? {} {}:{} last: {:.2f} throttle: {} ({:.2f}s left)".format(self, self.status, self.oldstatus, self.last_update, self.throttle, self.last_update + self.throttle - time.time())
        
        if self.status != self.oldstatus:
            # print "changed status"
            return False        
        
        if time.time() > self.last_update + self.throttle:
            # print "time passed (throttle: {})".format(self.throttle)
            return False

        if self.alerts:
            #print "have alerts"
            return False
        
        # print "suppress ({}s left)".format(int(self.last_update + self.throttle - time.time()))
        return True
                    
    def set_arg(self, argname, argval):
        self.set_args[argname]=argval
        self.args[argname]=argval
        
    def log(self, msg):
        self.logs.append(msg)

    def alert(self, msg):
        self.alerts.append(msg)

    def checkfilter(self, checkfilter):
        try:
            if 'notcpport' in checkfilter:
                self.code = 501
                self.msgtags["notcpport"] = 1
                return True
            if 'nosmtp' in checkfilter:
                ports = [25, 465, 587]
                if self.cm in ['sslcert', 'tcpport'] and int(self.args['port']) in ports:
                    # dont check it
                    self.code = 501
                    self.msgtags["nosmtp:{}".format(self.args['port'])] = 1
                    return True                 
            if 'noroot' in checkfilter:
                if self.cm in ['ping']:
                    self.code = 501
                    self.msgtags["noroot"] = 1
                    return True                                                     
            return False
        except Exception as e:
            print("checkfilter exception:",str(e))
            self.code = 502
            self.msgtags["checkfilter:exception"] = 1
            return True

    def dump(self):
        print(self)


    def rget(self, url, options='', allow_redirects=True):

        verify = True
        headers = {
            'User-Agent': self.user_agent
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


    def check_args(self):
        
        try:
            cargs = checkargs[self.cm]
        except KeyError:
            log.error(f"Unknown check {self.cm}")
            raise CheckException(f"Unknown check {self.cm!r}. Known are: {' '.join(checkargs.keys())}")

        for arg in cargs['required']:
            if arg not in self.args:
                log.error(f"Missing argument {arg!r} for {self.cm}. req: {cargs['required']}  opt: {cargs['optional']} {cargs.get('description', '')}")
                raise CheckException(f"Missing argument {arg!r} for {self.cm}. req: {cargs['required']}  opt: {cargs['optional']} {cargs.get('description', '')}")

        



    def check(self):

        self.check_args()

        if self.checkfilter(settings.checkfilter):
            return

        if self.cm in self.actions:
            self.code = None

            try:
                self.actions[self.cm]()
            except Exception as e:
                log.error('Exception {} when processing {} for {}@{}'.format(e, self.cm, self.name, self.textid))
                traceback.print_exc()
                self.status = 'ERR'
                self.details = 'exception: ' + str(e)
                raise

            if self.code is None:
                # set default success code
                self.code = 200
                self.msgtags['CHECKED']=1
        else:
            print("ERROR !!! dont know how to handle", repr(self.cm))
            print(f"Known actions: {' '.join(self.actions)}")
            sys.exit(1)        



    def action_httpstatus(self):
        url = self.args.get("url", 'http://okerr.com/')
        options = self.args.get("options", '')
        required_status = int(self.args.get("status", 200))

        try:
        
            r = self.rget(url, options=options, allow_redirects=False)

            if r.status_code == required_status:
                # good, status as it should be
                self.details = "Got status code {} {} as expected".format(required_status, r.reason)
                self.status = "OK"
            else:
                if 'Location' in r.headers:
                    sub_details = "Location: " + r.headers['Location']
                else:
                    sub_details = ''
                self.details = "Bad status code {} {} (not {}) {}".format(
                    r.status_code, r.reason, required_status, sub_details)
                self.status = "ERR"
        except requests.exceptions.RequestException as e:
            self.details = "Cannot perform request URL {}: {}".format(url, e)
            self.status = "ERR"


    def action_sslcert(self):
        try:
            host = self.args.get('host', 'okerr.com')
            port = int(self.args.get('port', '443'))
            days = int(self.args.get('days', '20'))
            options = self.args.get('options','')    
        except KeyError as e:
            self.status = 'ERR'
            self.details = str(e)
            return

        try:
            result = check_sslcert(host=host, port=port, days=days, options=options)
            self.status = "OK"
            self.details = result
        except CheckException as e:
            self.status = "ERR"
            self.details = str(e)
            return

    def action_sha1static(self):
        url = self.args.get("url", 'http://okerr.com/')                
        pagehash = self.args.get("hash",'')
        options = self.args.get("options",'')
        setargs = dict()

        try:
            r = self.rget(url, options)
           
            if r.status_code != 200:
                self.status = "ERR"
                self.details = "Status code: {} (not 200)".format(str(r.status_code))
                return

            realhash = hashlib.sha1(r.content).hexdigest()
            # check if it has musthave
            if len(pagehash) == 0:
                self.status = "OK"
                self.details = "hash initialized"
                self.set_arg("hash", realhash)
                return 
            else:                    
                if realhash == pagehash:
                    self.details = "hash match"
                    self.status = "OK"
                    return
                else:
                    self.details = "hash mismatch"
                    self.status = "ERR"
                    return
        
        except requests.exceptions.RequestException as e:
            self.details = "exception: {}".format(e)
            self.status = "ERR"


    def action_sha1dynamic(self):
        url = self.args.get("url",'http://okerr.com/')                
        pagehash = self.args.get("hash",'')
        options = self.args.get("options",'')

        try:
            r=self.rget(url, options = options)

            if r.status_code != 200:
                self.status = "ERR"
                self.details = "Status code: {} (not 200)".format(str(r.status_code))
                return

            realhash = hashlib.sha1(r.content).hexdigest()
            # check if it has musthave
            if len(pagehash)==0:
                self.status = "OK"
                self.details = "hash initialized"
                self.set_arg("hash", realhash)
                return 
            else:
                if realhash == pagehash:
                    self.status = "OK"
                    self.details = "hash match"
                    return 
                else:
                    # send alert from here, because we return OK !!!!
                    self.status = "OK"
                    self.details = "new hash"
                    self.set_arg("hash",realhash)
                    self.alert("Page {url} changed hash from {old} to {new}".format(url=url,old=pagehash,new=realhash))
                    return
        
        except requests.exceptions.RequestException as e:
            self.status = "ERR"
            self.details = "exception: {}".format(e)


    def action_ping(self):

        try:
            # r = pyping.ping(self.args["host"])
            # r = ping3.ping(self.args['host'])
            r = icmplib.ping(self.args['host'], count=3, timeout=2, interval=1)
            log.info(f"ping host: {self.args['host']} {r.address} avgrtt: {r.avg_rtt!r}")
        except Exception as e:
            self.status = "ERR"
            self.details = str(e)
            return 

        self.status = "OK" if r.packets_received == r.packets_sent else "ERR"
        
        self.details = f"{r.address}: {r.avg_rtt:.2f}ms ({r.packets_received}/{r.packets_sent})"


        """
        self.details = "{} ({}) rtt: {}/{}/{} lost: {}".format(r.destination, r.destination_ip, r.min_rtt, r.avg_rtt, r.max_rtt, r.packet_lost)
        # log.debug(self.details)                    
        if r.ret_code == 0:
            self.status = "OK"
        else: 
            self.status = "ERR"
        """

    def action_tcpport(self):
        host = self.args["host"]
        port = int(self.args["port"])
        substr = self.args.get("substr")
        timeout=2

        try:
            result = check_tcpport(host=host, port=port, timeout=timeout, substr=substr)
            self.status = "OK"
            self.details = result
        except CheckException as e:
            self.status = "ERR"
            self.details = str(e)
            return


    def action_httpgrep(self):
        url = self.args.get("url",'')
        musthave = self.args.get("musthave",'')
        mustnothave = self.args.get("mustnothave",'')
        options = self.args.get("options",'')


        try:
            r = self.rget(url, options)

            if r.status_code != 200:
                self.status = "ERR"
                self.details = "Status code: {} (not 200)".format(str(r.status_code))
                return

            ucontent = r.content.decode('utf8')

            # check if it has musthave
            if len(musthave)>0:
                if ucontent.find(musthave) == -1:
                    self.status = "ERR"
                    self.details = "Content has no substring '{}'".format(musthave)
                    return

            if len(mustnothave)>0:
                if ucontent.find(mustnothave) >= 0:
                    self.status = "ERR"
                    self.details = "Content has substring '{}'".format(mustnothave)
                    return
            
            self.status = "OK"
            self.details = ""
            return
        
        except requests.exceptions.RequestException as e:
            self.status = "ERR"
            self.details = "Cannot perform request URL {}: {}".format(url, e)
            return



    def action_whois(self):
        domain = self.args["domain"]
        days = int(self.args.get("days", "30"))

        try:
            w = whois.whois(domain)
        except (ConnectionResetError, Exception) as e:
            log.error("EXC {}: {}".format(type(e), e))
            self.code = 200 
            # self.msgtags['WHOIS EXC:' + str(e)] = 1
            # self.problem = True
            self.status = "ERR"
            self.details = str(e)
            return

        today = datetime.datetime.now()
            
        exp = w.expiration_date
        
        if exp is None:
            self.status = "ERR"
            self.details = "whois error"
            return 
        
        if isinstance(exp, list):
            exp = exp[0]
        
        left = exp - today

        if left.days > 0:
            self.details = "{} days left".format(left.days)
        else:
            self.details = "expired {} days ago".format(abs(left.days))
        
        if left.days < days:
            self.status = 'ERR'
        else:
            self.status = 'OK'


    def action_dns(self):
        host = self.args.get("host")
        qtype = self.args.get("type")
        options = self.args.get("options", "")
        value = self.args.get("value")
        

        resolver = dns.resolver.Resolver()
        resolver.search = None    
        
        try:
            
            # DNSBL part
            if qtype.lower().startswith("dnsbl"):
                
                # args["host"] = '1.2.3.4'
                dnsbl, blsuffix = qtype.split()

                if not blsuffix.endswith('.'):
                    blsuffix = blsuffix + '.'

                # resolve host to IP first
                ip4 = socket.gethostbyname(host)
                
                qhost = '.'.join(reversed(ip4.split('.'))) + '.'+blsuffix
                
                try:
                    answers = resolver.resolve(qhost, 'A')
                except dns.resolver.NXDOMAIN:
                    # great! not found in blacklist
                    self.status = "OK"
                    self.details = "{} ({}) not in {}".format(host, ip4, blsuffix) 
                    return

                # bad. we are in list!                
                self.set_arg('value', str(answers[0]))
                self.status = "ERR"
                self.details = str(resolver.resolve(qhost, 'TXT')[0])
                return

        
            # COMMON PART            
            if qtype.lower() == "reverse":
                host = dns.reversename.from_address(host)
                qtype = 'PTR'

            answers = resolver.resolve(host, qtype)
            dnsstr = ' '.join(sorted( str(a) for a in answers ))
                    
            # initialization?
            if value == '' and 'init' in options:
                self.set_arg("value", dnsstr)
                self.status = "OK"
                self.details = "init: {}".format(dnsstr)            
                return
            
            if dnsstr == value:
                self.status = "OK"
                self.details = "match: {}".format(dnsstr)
                return 
            
            # value mismatch
            if 'dynamic' in options:
                self.status = "OK"
                self.details = "new: {}".format(dnsstr)
                self.set_arg("value", dnsstr)
                self.alert("{} > {}".format(value, dnsstr))
                return
            
            self.status = "ERR"
            self.details = dnsstr
            return 

        except dns.rdatatype.UnknownRdatatype as e:
            log.debug("Exception {} {}".format(type(e), str(e)))
            self.status = "ERR"
            self.details = str(e)
            self.problem = True
            return 
            
        except Exception as e:
            log.debug("Exception {} {}".format(type(e), str(e)))
            self.status = "ERR"
            self.details = str(e)
            return

    def action_dnsbl(self):
        host = self.args.get("host")
        skip = self.args.get("skip", '')
        extra = self.args.get("extra", '')


        dnsbl_zones = async_dnsbl_client.dnsbl_zones

        skip_dnsbl = list(filter(None, re.split('[, ]+', skip)))
        extra_dnsbl = filter(None, re.split('[, ]+', extra))

        checked = len(dnsbl_zones)

        for bl in skip_dnsbl:
            try:
                dnsbl_zones.remove(bl)
            except ValueError:
                self.status = "ERR"
                self.details = f"Zone {bl} not available. Maybe mistype?"
        
        dnsbl_zones.extend(extra_dnsbl)
        
        try:
            hits = asyncio.run(async_dnsbl_client.dnsbl(host, zonelist=dnsbl_zones, nameserver=settings.nameserver))
        except (aiodns.error.DNSError, OSError):
            log.warning(f"Failed to resolve {host} in dnsbl")
            self.status = "ERR"
            self.details = f"Failed to resolve {host}"
            return
            
        if hits:
            self.status = "ERR"
            self.details = "{} (total: {}/{})".format(', '.join(hits[:3]), len(hits), checked)
            return
        else:
            self.status = "OK"
            self.details = "Not found in {} DNSBL checked".format(checked)
            if skip_dnsbl:
                self.details += ' ({} skipped)'.format(len(skip_dnsbl))

    __repr__ = __str__
        
        
# main
log = logging.getLogger('okerr')                
if __name__ == '__main__':
    ch = Check()
    ch.name = 'test'
    ch.textid = 'test'

    parser = argparse.ArgumentParser(description='RemoteCheck manual interface')
    parser.add_argument('cm', help='CheckMethod. One of: {}'.format(' '.join(ch.actions)))
    parser.add_argument('option', nargs='+', help='check options in opt=value format, e.g. host=google.com')

    args = parser.parse_args()
    print(args)
    ch.cm = args.cm
    for optval in args.option:
        opt, val = optval.split('=', 1)
        ch.set_arg(opt, val)

    ch.check()
    print(ch)

