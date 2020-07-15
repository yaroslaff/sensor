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

from multiprocessing import Process, current_process
from setproctitle import setproctitle
from dotenv import load_dotenv

from remotecheck import Check
import okerrupdate


log = None
connection = None
channel = None
worker_pid = list()
args = None
machine_info = None
role = None  # master, worker, helo, qindincator

workers = list()
qworkers = list()
qindicators = dict()

myindicator = None

processed = 0
processed_limit = 3

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
            print("Caught exc: {}: {}, retry".format(type(e), e))
            time.sleep(5)
        else:
            return channel



def rmq_process(qlist, ch, callback, timeout=None, sleep=1):
    started = time.time()
    while True:
        for qname in qlist:
            method, properties, body = ch.basic_get(qname)
            if method:
                callback(ch, method, properties, body)
        if timeout and time.time() > started+timeout:
            return
        time.sleep(sleep)

def signal_handler(sig, frame):
    #
    #
    #
    log.info('{} {}: got signal'.format(role, os.getpid()))
    sys.exit(0)

def myip():
    url = 'https://diagnostic.opendns.com/myip'

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
        log.error("Do not know how to process _task {!r}".format(data['_task']))

def callback_regular_task(ch, method, properties, body):
    global processed
    data = json.loads(body)
    # print("exch: {} key: {}".format(method.exchange, method.routing_key))


    name = '{}@{}'.format(data.get('name','???'), data.get('textid','???'))
    setproctitle('sensor.process {}'.format(name))
    log.debug('will process {}'.format(name))

    if data['_task'] == 'tproc.indicator':
        check = Check.from_request(data)

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
                myindicator.update('OK', 'Uptime: {}'.format(dhms(time.time() - started)))
            except okerrupdate.OkerrExc as e:
                log.error("okerr update error: {}".format(str(e)))
            #time.sleep(args.sleep)
            connection.sleep(args.hello_sleep)
        except KeyboardInterrupt:
            print("Hello got KeyboardInterrupt")
            # normal quit
            sys.exit(0)
        except pika.exceptions.AMQPConnectionError as e:
            print("DEBUG HANDLERS:", log.handlers)
            log.error("EXCEPTION pid:{} ({}) AMQPConnectionError: {} {}".format(os.getpid(), role, type(e), str(e)))
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
    channel.basic_consume(
        queue='tasks:any', on_message_callback=callback_regular_task, auto_ack=True)

    for qname in machine_info['qlist']:
        channel.queue_declare(queue='tasks:' + qname, auto_delete=True)
        channel.basic_consume(
            queue='tasks:' + qname, on_message_callback=callback_regular_task, auto_ack=True)

    try:
        channel.start_consuming()
    except KeyboardInterrupt as e:
        pass
    except pika.exceptions.AMQPConnectionError as e:
        log.error("EXCEPTION pid:{} ({}) AMQPConnectionError: {} {}".format(os.getpid(), role, type(e), str(e)))

def master_watchdog():

    qalive_cnt = 0
    qalive_cnt = 0

    log.info("master_watchdog() check")

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

    for p in qworkers:
        if p.is_alive():
            qalive_cnt += 1
        else:
            log.debug("reap qi pid:{}".format(p.pid))
            qworkers.remove(p)
            p.join()

def exc_wrapper(func, *args):
    print("wrapper for {}: {}".format(func, args))

def main():
    global log
    global channel
    global args
    global machine_info
    global workers
    global role
    global myindicator

    role = 'master'

    # main code
    conf_file = '/etc/okerr/okerrupdate'
    load_dotenv(dotenv_path=conf_file)

    def_pem = '/etc/okerr/ssl/client.pem'
    def_capem = '/etc/okerr/ssl/ca.pem'

    def_rmqhost = 'localhost'
    def_rmqvhost = 'okerr'
    def_rmquser = 'okerr'
    def_rmqpass = 'okerr_default_password'

    parser = argparse.ArgumentParser(description='okerr indicator MQ tasks client')

    g = parser.add_argument_group('Location')
    g.add_argument('--name', default=os.getenv('SENSOR_NAME', 'noname@nowhere.tld'))
    g.add_argument('--ip', default=os.getenv('SENSOR_IP', myip()))

    g = parser.add_argument_group('Options')
    g.add_argument('-v', '--verbose', action='store_true', default=False, help='verbose mode')
    g.add_argument('-s', '--hello-sleep', type=int, default=10, help='sleep time between hello runs')
    g.add_argument('-n', type=int, default=10, help='number of worker processes')


    g = parser.add_argument_group('RabbitMQ options')
    g.add_argument('--rmqhost', default=os.getenv('RMQ_HOST', def_rmqhost),
                   help='RabbitMQ host ($RMQ_HOST, {})'.format(os.getenv('RMQ_HOST', def_rmqhost)))
    g.add_argument('--rmqvhost', default=os.getenv('RMQ_VHOST',def_rmqvhost),
                   help='RabbitMQ VirtualHost ($RMQ_VHOST, {})'.format(os.getenv('RMQ_VHOST', def_rmqvhost)))
    g.add_argument('--rmquser', default=os.getenv('RMQ_USER', def_rmquser),
                   help='RabbitMQ username ($RMQ_USER, {})'.format(os.getenv('RMQ_USER', def_rmquser)))
    g.add_argument('--rmqpass', default=os.getenv('RMQ_PASS', def_rmqpass),
                   help='RabbitMQ password ($RMQ_PASS, {})'.format(os.getenv('RMQ_PASS', def_rmqpass)))
    g.add_argument('--pem', default=def_pem,
                   help='Client cert+key PEM file: ({})'.format(def_pem))
    g.add_argument('--capem', default=def_capem,
                   help='CA cert PEM file: {}'.format(def_capem))


    args = parser.parse_args()


    log = logging.getLogger('okerr')

    out = logging.StreamHandler(sys.stdout)
    out.setFormatter(logging.Formatter('%(asctime)s %(message)s',
                                       datefmt='%Y/%m/%d %H:%M:%S'))
    log.addHandler(out)
    if args.verbose:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)

    sanity_check(args)

    p = Process(target=hello_loop, args=())
    p.start()

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

    channel = get_rmq_channel(args)

    master_queues = list()

    for qname in machine_info['qlist']:
        channel.queue_declare(queue='tasksq:' + qname, auto_delete=True)
        master_queues.append('tasksq:' + qname)

        #channel.basic_consume(
        #    queue='tasksq:'+qname, on_message_callback=callback_ctl, auto_ack=True)


    channel.queue_declare(queue='tasksq:any', auto_delete=True)
    channel.queue_declare(queue=machine_info['ctlq'], exclusive=True)
    master_queues.append('tasksq:any')
    master_queues.append(machine_info['ctlq'])

    #channel.basic_consume(
    #    queue=machine_info['ctlq'], on_message_callback=callback_ctl, auto_ack=True, exclusive=True)
    #channel.basic_consume(
    #    queue='tasksq:any', on_message_callback=callback_ctl, auto_ack=True)

    log.info("started sensor {}".format(args.name))
    try:
        while True:
            master_watchdog()
            rmq_process(master_queues, channel, callback_ctl, timeout=10)

        # channel.start_consuming()
    except KeyboardInterrupt as e:
        log.error("Exit because of {}".format(e))
        pass

main()