#!/usr/bin/env python3
import sys
import os

import pika
import pika.exceptions
import logging
import json
import argparse
import time
import signal
import requests
import ssl
import re
import datetime

from pathlib import Path
from multiprocessing import Process, current_process, active_children
from setproctitle import setproctitle
from dotenv import load_dotenv

from remotecheck.version import __version__
import okerrupdate

from remotecheck.check import Check
from remotecheck.exceptions import CheckException
from remotecheck.settings import settings


log = None
connection = None
channel = None
worker_pid = list()
args = None
machine_info = None
role = None  # master, worker, helo, qindincator

workers = list()
qworkers = list()
hello_process = None
qindicators = dict()

myindicator = None

processed = 0
processed_limit = 3

started = time.time()

def dhms(sec, sep=" "):
    out = ""
    added = 0
    t = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}

    if isinstance(sec, datetime.timedelta):
        sec = sec.total_seconds()

    for k in sorted(t, key=t.__getitem__, reverse=True):

        if added == 2:
            return out.rstrip()

        if sec >= t[k]:
            n = int(sec / t[k])
            sec -= n * t[k]
            out += "%d%s%s" % (n, k, sep)
            added += 1
    return out.rstrip()


def sanity_check(args):
    if not os.path.isfile(args.pem):
        log.error("No PEM file (--pem {})".format(args.pem))
        sys.exit(1)

    if not os.path.isfile(args.capem):
        log.error("No CA PEM file (--capem {})".format(args.capem))
        sys.exit(1)

def get_rmq_channel(args):
    global connection, channel

    credentials = pika.PlainCredentials(args.rmquser, args.rmqpass)
    context = ssl.create_default_context(cafile=args.capem)
    context.load_cert_chain(args.pem)
    ssl_options = pika.SSLOptions(context, "rabbitmq")

    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=args.rmqhost, port=5671,
            virtual_host=args.rmqvhost,
            ssl_options=ssl_options,
            credentials=credentials))
    channel = connection.channel()
    return channel


def get_rmq_channel_safe(args):
    while True:
        try:
            channel = get_rmq_channel(args)
        except pika.exceptions.AMQPError as e:
            print("Caught AMPQ exc ({}): {}: {}, retry".format(role, type(e), e))
            time.sleep(5)
        except ConnectionError as e:
            print("Caught generic python exc ({}): {}: {}, retry".format(role, type(e), e))
            time.sleep(5)
        else:
            return channel

def rmq_process(qlist, ch, reg_callback, ctl_callback, timeout=None, sleep=1):
    global properties
    started = time.time()
    while True:
        for qname in qlist:
            try:
                method, properties, body = ch.basic_get(qname)
            except pika.exceptions.AMQPConnectionError as e:
                log.error("rmq_process EXCEPTION pid:{} ({}) AMQPConnectionError: {} {}".format(
                    os.getpid(), role, type(e), str(e)))
                # ch = get_rmq_channel_safe(args)
                print("Exit now ({} pid: {})....".format(role, os.getpid()))
                master_exit(1)  # Only in master here. crash immediately, systemd will restart us
                print("Exited???")

            if method:
                if qname.startswith('ctl:'):                    
                    ctl_callback(ch, method, properties, body)
                else:
                    reg_callback(ch, method, properties, body)
        if timeout and time.time() > started+timeout:
            return
        time.sleep(sleep)

def signal_handler(sig, frame):
    #
    #
    #
    log.info('{} {}: got signal'.format(role, os.getpid()))
    sys.exit(0)

def master_exit(status=0):
    killed = list()
    for p in active_children():
        killed.append(p.pid)
        p.terminate()
    print("Master exit killed:", ' '.join([str(pid)  for pid in killed]))
    sys.exit(status)

def myip():
    url = 'https://ifconfig.me/ip'

    while True:
        try:
            r = requests.get(url)
        except requests.exceptions.RequestException:
            time.sleep(1)
            pass
        if r.status_code == 200:
            return r.text
        else:
            time.sleep(5)


def callback_connection_closed(connection, reply_code, reply_text):
    print("connection closed. c: {} code: {} text: {}".format(
        connection, reply_code, reply_text
    ))

def callback_return(ch, method, properties, body):
    log.warning('{}: got return msg from qindicator, probably mqsender restarted/stopped. stopping'.format(os.getpid()))
    sys.exit(0)

def qindicator_loop(data):
    global role

    role = 'qindicator'
    set_machine_info(args)
    ch = get_rmq_channel_safe(args)
    name = '{}@{}'.format(data.get('name', '???'), data.get('textid','???'))

    # ch.add_on_close_callback(callback_connection_closed)
    ch.add_on_return_callback(callback_return)

    last_status = None
    last_reported = 0
    nthrottled=0
    reported = 0

    throttle = data['throttle']

    try:
        while True:
            try:
                check = Check.from_request(data)
                check.check()
                setproctitle('sensor.qindicator {} = {} r{} th{}'.format(
                    name, check.status, reported, nthrottled))

                if check.status != last_status or time.time() > last_reported + throttle:
                    resp = check.response()
                    resp['_machine'] = machine_info
                    resp['_throttled'] = nthrottled
                    try:
                        ch.basic_publish(
                            exchange='',
                            routing_key=data['resultq'],
                            body=json.dumps(resp),
                            mandatory=True)

                    except pika.exceptions.AMQPConnectionError as e:
                        log.error('QIndicator {} pid:{} self-destruct because caught exception: {} {}'.format(
                            name, os.getpid(), type(e), e))
                        return

                    log.info("QREPORT {}: {} = {} ({})".format(
                        os.getpid(), name, check.status, check.details))
                    last_status = check.status
                    last_reported = time.time()
                    nthrottled = 0
                    reported += 1
                else:
                    nthrottled += 1
                # time.sleep(data['period'])
                connection.sleep(data['period'])
            except pika.exceptions.AMQPConnectionError as e:
                log.error("EXCEPTION pid:{} ({}) AMQPConnectionError: {} {}".format(os.getpid(), role, type(e), str(e)))
                ch = get_rmq_channel_safe(args)
    except KeyboardInterrupt as e:
        log.error("qindicator keyboard interrupt")

def oneprocess_callback_ctl(ch, method, properties, body):
    print("1p callback ctl",ch, method, properties, body)

def callback_ctl(ch, method, properties, body):
    global workers
    global qindicators

    data = json.loads(body)

    if data['_task'] == 'tproc.kill':
        for p in qworkers:
            if p.pid == data['pid']:
                log.info("kill pid:{} {}".format(p.pid, data['reason']))
                qworkers.remove(p)
                qindicators = {key: val for key, val in qindicators.items() if val != p}
                p.terminate()
                p.join()

    elif data['_task'] == 'tproc.indicator':
        name = '{}@{}'.format(data.get('name', '???'), data.get('textid', '???'))
        # kill old qi
        try:
            p = qindicators[name]
            log.debug("replace old qi {} pid:{}".format(name, qindicators[name].pid))
            qindicators = {key: val for key, val in qindicators.items() if val != p}
            p.terminate()
            p.join()
        except KeyError as e:
            # no such qindicator, good!
            pass

        # we got it from qtasks
        p = Process(target=qindicator_loop, args=(data,))
        p.start()
        qworkers.append(p)
        qindicators[name] = p
    else:
        log.error(f"Do not know how to process _task={data['task']} data: {data}")

def callback_regular_task(ch, method, properties, body):
    global processed
    body = body.decode('utf8')
    data = json.loads(body)
    # print("exch: {} key: {}".format(method.exchange, method.routing_key))


    name = '{}@{}'.format(data.get('name','???'), data.get('textid','???'))
    setproctitle('sensor.process {}'.format(name))
    log.debug('will process {}'.format(name))

    if data['_task'] == 'tproc.indicator':
        check = Check.from_request(data)

        # try/except?
        check.check()

        resp = check.response()
        resp['_machine'] = machine_info

        channel.basic_publish(
            exchange='',
            routing_key=data['resultq'],
            body=json.dumps(resp))
        log.info("REPORT {}: {} = {} ({})".format(os.getpid(), name, check.status, check.details))
    else:
        print("Do not know how to process _task {!r}".format(data['_task']))

    setproctitle('sensor.process')
    processed += 1

def set_machine_info(args):
    global machine_info

    ctlq = 'ctl:{}'.format(args.name)

    m = re.match('(.*)@(.*)\.(.*)', args.name)

    machine_info = {
        'ip': args.ip,
        'name': args.name,
        'pid': os.getpid(),
        'ctlq': ctlq,
        'qlist': [args.name, m.group(2)+'.'+m.group(3), m.group(3)]
    }


def hello_loop():
    global channel
    global role

    role = 'hello'

    started = time.time()
    set_machine_info(args)

    r = {
        '_task': 'tproc.hello',
        '_machine': machine_info
    }

    setproctitle('sensor.hello')


    op = okerrupdate.OkerrProject()
    myindicator = op.indicator("sensor:{}".format(args.name.replace('@','_')), method='heartbeat')

    channel = get_rmq_channel_safe(args)

    channel.exchange_declare(exchange='hello_ex', exchange_type='fanout')

    while True:

        if channel is None:
            log.error("Restore channel...")
            channel = get_rmq_channel_safe(args)

        try:
            r['uptime'] = int(time.time() - started)
            channel.basic_publish(
                exchange='hello_ex',
                routing_key='',
                body=json.dumps(r))
            try:
                # log.info('ZZZZ hello.loop update {}'.format(myindicator))
                print("loop update")
                myindicator.update('OK', 'v: {} up: {}'.format(__version__, dhms(time.time() - started)))
            except okerrupdate.OkerrExc as e:
                log.error("okerr update error: {}".format(str(e)))
            #time.sleep(args.sleep)
            connection.sleep(args.hello_sleep)
        except KeyboardInterrupt:
            print("Hello got KeyboardInterrupt")
            # normal quit
            sys.exit(0)
        except pika.exceptions.AMQPConnectionError as e:
            log.error("EXCEPTION pid:{} ({}) AMQPConnectionError: {} {}".format(
                os.getpid(), role, type(e), str(e)))
            # channel = get_rmq_channel_safe(args)
            channel = None


def worker_loop():
    global channel, machine_info, role
    role = 'worker'

    machine_info = {
        'ip': args.ip,
        'name': args.name,
        'pid': os.getpid(),
        'pname': current_process().name
    }

    set_machine_info(args)
    setproctitle('sensor.process')

    channel = get_rmq_channel_safe(args)
    channel.queue_declare(queue='tasks:any', auto_delete=True)
    print("consume tasks:any to callback_regular_task")
    channel.basic_consume(
        queue='tasks:any', on_message_callback=callback_regular_task, auto_ack=True)

    for qname in machine_info['qlist']:
        channel.queue_declare(queue='tasks:' + qname, auto_delete=True)
        print(f"consume tasks:{qname} to callback_regular_task")
        channel.basic_consume(
            queue='tasks:' + qname, on_message_callback=callback_regular_task, auto_ack=True)

    try:
        channel.start_consuming()
    except KeyboardInterrupt as e:
        pass
    except pika.exceptions.AMQPConnectionError as e:
        log.error("EXCEPTION pid:{} ({}) AMQPConnectionError: {} {}".format(os.getpid(), role, type(e), str(e)))

def master_watchdog():
    global hello_process

    qalive_cnt = 0
    qalive_cnt = 0

    log.info("master_watchdog() check")


    #
    # bury and restart regular workers
    # 

    for p in workers:
        if p.is_alive():
            pass
        else:
            log.info("Reap regular worker {} pid:{} dead (code: {})".format(p, p.pid, p.exitcode))
            workers.remove(p)

    while len(workers) != args.n:
        p = Process(target=worker_loop, args=())
        p.start()
        workers.append(p)
        log.info("Start regular worker pid:{}".format(p.pid))

    #
    # Bury (not restart) qworkers
    #

    for p in qworkers:
        if p.is_alive():
            qalive_cnt += 1
        else:
            log.debug("reap qi pid:{}".format(p.pid))
            qworkers.remove(p)
            p.join()

    #
    # restart hello
    #

    if not hello_process.is_alive():
        log.info('Reap old hello process pid {pid}'.format(pid=hello_process.pid))
        hello_process = Process(target=hello_loop, args=())
        hello_process.start()
        log.info('New hello process pid {pid}'.format(pid=hello_process.pid))

def exc_wrapper(func, *args):
    print("wrapper for {}: {}".format(func, args))


def oneprocess(args):


    def hello(channel, myindicator):

        r = {
            '_task': 'tproc.hello',
            '_machine': machine_info
        }

        r['uptime'] = int(time.time() - started)
        channel.basic_publish(
            exchange='hello_ex',
            routing_key='',
            body=json.dumps(r))
        try:
            myindicator.update('OK', 'v: {} up: {} '.format(__version__, dhms(time.time() - started)))
        except okerrupdate.OkerrExc as e:
            log.error("okerr update error: {}".format(str(e)))


    channel = get_rmq_channel(args)
    set_machine_info(args)

    op = okerrupdate.OkerrProject()
    myindicator = op.indicator("sensor:{}".format(args.name.replace('@','_')), method='heartbeat')

    channel.exchange_declare(exchange='hello_ex', exchange_type='fanout')



    master_queues = list()

    for qname in machine_info['qlist']:
        channel.queue_declare(queue='tasks:' + qname, auto_delete=True)
        master_queues.append('tasks:' + qname)

        #channel.basic_consume(
        #    queue='tasksq:'+qname, on_message_callback=callback_ctl, auto_ack=True)


    try:
        # Do not use tasksq:any for singleprocess
        # channel.queue_declare(queue='tasksq:any', auto_delete=True)
        channel.queue_declare(queue=machine_info['ctlq'], exclusive=True)
        # master_queues.append('tasksq:any')
        master_queues.append(machine_info['ctlq'])
    except Exception as e:
        print("MAIN FAILED TO DECLARE QUEUES, QUIT. {}: {}". format(type(e), str(e)))
        master_exit(1)

    log.info("started sensor {}".format(args.name))
    try:
        while True:
            try:
                print(f"HELLO {myindicator}")
                hello(channel, myindicator)                
                rmq_process(master_queues, channel, callback_regular_task, callback_ctl, timeout=10)
            except pika.exceptions.AMQPError as e:
                print("MAIN LOOP AMPQ exception: {}: {}, retry".format(type(e), e))
                # exit for restart
                print("Exit master pid {}".format(os.getpid()))
                sys.exit(1)

            except Exception as e:
                log.error("MAIN LOOP exception ({}): {}".format(type(e), str(e)))
                sys.exit(1)

        # channel.start_consuming()
    except KeyboardInterrupt as e:
        log.error("Exit because of {}".format(e))
        pass


def testrmq(args: argparse.Namespace):

    print(f"""
    Connection parameters:
    Host: {args.rmqhost}
    VHost: {args.rmqvhost}
    User: {args.rmquser}
    Password: {args.rmqpass}

    SSL CA: {args.capem}
    SSL Certificate: {args.pem}
    """)

    sanity_check(args)

    context = ssl.create_default_context(cafile=args.capem)
    context.load_cert_chain(certfile=args.pem)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED

    ssl_options = pika.SSLOptions(context, args.rmqhost)

    credentials = pika.PlainCredentials(args.rmquser, args.rmqpass)
    params = pika.ConnectionParameters(
        host=args.rmqhost,
        port=5671,
        virtual_host=args.rmqvhost,
        credentials=credentials,
        ssl_options=ssl_options
    )

    try:
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        queue = channel.queue_declare(queue='', exclusive=True)
        print(f"OK, tmp test queue created: {queue.method.queue}")
        connection.close()
    except Exception as e:
        print(f"ERROR {type(e)} : {e}")



def dump_systemd():
    pkg_dir = Path(__file__).parent
    tpl_file = pkg_dir / 'contrib' / 'okerr-sensor.service'
    tpl = open(tpl_file).read() 
    tpl = tpl.replace('%PATH%', sys.argv[0])
    print(tpl)


def get_args():

    def_pem = os.getenv('SENSOR_PEM','/etc/okerr/ssl/client.pem')
    def_capem = os.getenv('SENSOR_CAPEM', '/etc/okerr/ssl/ca.pem')

    def_rmqhost = 'localhost'
    def_rmqvhost = 'okerr'
    def_rmquser = 'okerr'
    def_rmqpass = 'okerr_default_password'


    actions_list = [
            'sslcert',
            'sha1static',
            'sha1dynamic',
            'ping',
            'httpgrep',
            'httpstatus',
            'tcpport',
            'whois',
            'dns',
            'dnsbl']

    parser = argparse.ArgumentParser(description=f'okerr indicator MQ tasks client ver {__version__}')

    g = parser.add_argument_group('Location')
    g.add_argument('--name', default=os.getenv('SENSOR_NAME', None))
    g.add_argument('--ip', default=os.getenv('SENSOR_IP', myip()))

    g = parser.add_argument_group('Options')
    g.add_argument('-v', '--verbose', action='store_true', default=False, help='verbose mode')
    g.add_argument('-s', '--hello-sleep', type=int, default=10, help='sleep time between hello runs')
    g.add_argument('-n', type=int, default=10, help='number of worker processes')
    g.add_argument('--oneprocess', default=bool(os.getenv('SENSOR_ONEPROCESS')), action='store_true', help='work as one process')


    g = parser.add_argument_group('RabbitMQ options')
    g.add_argument('--rmqhost', default=os.getenv('RMQ_HOST', None),
                   help='RabbitMQ host ($RMQ_HOST, {})'.format(os.getenv('RMQ_HOST', None)))
    g.add_argument('--rmqvhost', default=os.getenv('RMQ_VHOST',None),
                   help='RabbitMQ VirtualHost ($RMQ_VHOST, {})'.format(os.getenv('RMQ_VHOST', None)))
    g.add_argument('--rmquser', default=os.getenv('RMQ_USER', None),
                   help='RabbitMQ username ($RMQ_USER, {})'.format(os.getenv('RMQ_USER', None)))
    g.add_argument('--rmqpass', default=os.getenv('RMQ_PASS', None),
                   help='RabbitMQ password ($RMQ_PASS, {})'.format(os.getenv('RMQ_PASS', None)))
    g.add_argument('--pem', default=None,
                   help='Client cert+key PEM file: ({})'.format(def_pem))
    g.add_argument('--capem', default=None,
                   help='CA cert PEM file: {}'.format(def_capem))

    g = parser.add_argument_group('Debugging/development')
    g.add_argument('--manual', default=list(), nargs='+', metavar=('CheckMethod','ARG'),
                   help='Run manual check. CheckMethods: {}. Example: --manual httpstatus url=http://okerr.com status=200'.format(' '.join(actions_list)))

    g.add_argument('--testrmq', default=False, action='store_true', 
                   help='check connection to RabbitMQ server')
    g.add_argument('--systemd', default=False, action='store_true', 
                   help='dump systemd unit file')

    g.add_argument('--env', default=None, 
                   help='Load this env file')


    # {'_task': 'tproc.indicator', 'id': 20088699, 'textid': 'okrrdm', 'name': 'медуза', 'cm': 'httpstatus', 
    # 'args': {'url': 'https://meduza.io', 'status': '200', 'options': ''}, 
    # 'mtime': 1608919844, 'period': 3600, 'throttle': 2400, 'resultq': 'results:charlie:9525'}



    args = parser.parse_args()

    if args.env:
        print(f"Load env from {args.env}")
        load_dotenv(dotenv_path=args.env)

    args.name = args.name or os.getenv('SENSOR_NAME', 'noname@nowhere.tld')
    args.rmqhost = args.rmqhost or os.getenv('RMQ_HOST', def_rmqhost)
    args.rmqvhost = args.rmqvhost or os.getenv('RMQ_VHOST', def_rmqvhost)
    args.rmquser = args.rmquser or os.getenv('RMQ_USER', def_rmquser)
    args.rmqpass = args.rmqpass or os.getenv('RMQ_PASS', def_rmqpass)
    args.pem = args.pem or os.getenv('SENSOR_PEM', def_pem)
    args.capem = args.capem or os.getenv('SENSOR_CAPEM', def_capem)

    
    if args.verbose:
        print(args)

    return args

def main():
    global log
    global channel
    global args
    global machine_info
    global workers
    global hello_process
    global role
    global myindicator

    role = 'master'

    # main code
    conf_file = '/etc/okerr/okerrupdate'
    load_dotenv(dotenv_path=conf_file)

    log = logging.getLogger('okerr')

    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(logging.Formatter('%(asctime)s %(message)s',
                                       datefmt='%Y/%m/%d %H:%M:%S'))
    out.setLevel(logging.INFO)
    log.addHandler(out)

    args = get_args()

    if args.verbose:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)


    #
    # manual mode
    #

    if args.manual:

        data = dict(textid='_sensor', name='_manual', cm=args.manual[0], args=dict())

        try:
            for arg in args.manual[1:]:
                k,v = arg.split('=', 1)
                data['args'][k]=v
        except ValueError as e:
            print(f"Bad manual option: {arg}. Use param=value")
            sys.exit(1)


        print("REQUEST:\n", json.dumps(data, indent=4),"\n")
        check = Check.from_request(data)

        # try/except?
        try:
            check.check()
        except CheckException as e:
            print(e, file=sys.stderr)
            sys.exit(1)

        resp = check.response()
        print("RESPONSE:\n", json.dumps(resp, indent=4),"\n")

        sys.exit(0)

    if args.systemd:
        dump_systemd()
        sys.exit(0)


    sanity_check(args)

    if args.testrmq:
        testrmq(args)
        sys.exit(0)


    #
    # one-process mode
    #

    if args.oneprocess:
        print("Run in one-process mode")
        oneprocess(args)
        sys.exit(0)


    #
    # default mode
    #

    hello_process = Process(target=hello_loop, args=())
    hello_process.start()

    #for chindex in range(args.n):
    #    p = Process(target=worker_loop, args=())
    #    p.start()
    #    workers.append(p)

    setproctitle('sensor.master {} {}'.format(args.name, args.n))
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)

    set_machine_info(args)

    log.debug('Connect to RMQ host {!r}:5671 vhost: {!r} user: {!r} ca: {!r} client: {!r}'.format(
        args.rmqhost, args.rmqvhost, args.rmquser,
        args.capem, args.pem
    ))

    try:
        channel = get_rmq_channel(args)
    except (ConnectionResetError, Exception) as e:
        print("Failed to connect: {host}:5671 vhost: {vhost} user: {user}".format(
            host=args.rmqhost, vhost=args.rmqvhost, user=args.rmquser
        ))
        print("EXIT")
        master_exit(1)


    master_queues = list()

    for qname in machine_info['qlist']:
        channel.queue_declare(queue='tasksq:' + qname, auto_delete=True)
        master_queues.append('tasksq:' + qname)

        #channel.basic_consume(
        #    queue='tasksq:'+qname, on_message_callback=callback_ctl, auto_ack=True)


    try:
        channel.queue_declare(queue='tasksq:any', auto_delete=True)
        channel.queue_declare(queue=machine_info['ctlq'], exclusive=True)
        master_queues.append('tasksq:any')
        master_queues.append(machine_info['ctlq'])
    except Exception as e:
        print("MAIN FAILED TO DECLARE QUEUES, QUIT. {}: {}". format(type(e), str(e)))
        master_exit(1)

    #channel.basic_consume(
    #    queue=machine_info['ctlq'], on_message_callback=callback_ctl, auto_ack=True, exclusive=True)
    #channel.basic_consume(
    #    queue='tasksq:any', on_message_callback=callback_ctl, auto_ack=True)

    log.info(f"started sensor {args.name} ver {__version__}")
    try:
        while True:
            master_watchdog()
            try:
                rmq_process(master_queues, channel, callback_ctl, callback_ctl, timeout=10)
            except pika.exceptions.AMQPError as e:
                print("MAIN LOOP AMPQ exception: {}: {}, retry".format(type(e), e))
                # exit for restart
                print("Exit master pid {}".format(os.getpid()))
                master_exit(1)

            except Exception as e:
                log.error("MAIN LOOP exception ({}): {}".format(type(e), str(e)))
                master_exit(1)

        # channel.start_consuming()
    except KeyboardInterrupt as e:
        log.error("Exit because of {}".format(e))
        pass

main()
