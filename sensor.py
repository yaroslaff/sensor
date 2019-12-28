#!/usr/bin/env python3
import sys
import os

import pika
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

from remotecheck import Check
import okerrupdate


log = None
connection = None
channel = None
worker_pid = list()
args = None
machine_info = None
role = None # master, worker, helo, qindincator

workers = list()
qworkers = list()
qindicators = dict()

myindicator = None


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


def signal_handler(sig, frame):
    #
    #
    #
    print('{} {}: got signal'.format(role, os.getpid()))
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
    ch = get_rmq_channel(args)
    name = '{}@{}'.format(data.get('name','???'), data.get('textid','???'))

    # ch.add_on_close_callback(callback_connection_closed)
    ch.add_on_return_callback(callback_return)

    last_status = None
    last_reported = 0
    nthrottled=0
    reported = 0

    throttle = data['throttle']

    try:
        while True:
            check = Check.from_request(data)
            check.check()
            setproctitle('sensor.qindicator {} = {} ({}x{} {})'.format(
                name, check.status, reported, nthrottled,throttle - int(time.time() - last_reported)))

            if check.status != last_status or time.time() > last_reported + throttle:
                resp = check.response()
                resp['_machine'] = machine_info
                resp['_throttled'] = nthrottled
                ch.basic_publish(
                    exchange='',
                    routing_key=data['resultq'],
                    body=json.dumps(resp),
                    mandatory=True)
                log.info("{}: {} = {} ({})".format(
                    os.getpid(), name, check.status, check.details))
                last_status = check.status
                last_reported = time.time()
                nthrottled = 0
                reported += 1
            else:
                nthrottled += 1
            # time.sleep(data['period'])
            connection.sleep(data['period'])
    except KeyboardInterrupt as e:
        log.error("qindicator keyboard interrupt")

def callback_ctl(ch, method, properties, body):
    global workers
    global qindicators
    data = json.loads(body)

    if data['_task'] == 'tproc.kill':
        for p in qworkers:
            if p.pid == data['pid']:
                log.info("kill {}: {}".format(p.pid, data['reason']))
                qworkers.remove(p)
                qindicators = {key: val for key, val in qindicators.items() if val != p}
                p.terminate()
                p.join()

    elif data['_task'] == 'tproc.indicator':
        name = '{}@{}'.format(data.get('name', '???'), data.get('textid', '???'))

        # kill old qi
        try:
            p = qindicators[name]
            log.debug("replace old qi {} {}".format(name, qindicators['name'].pid))
            qindicators = {key: val for key, val in qindicators.items() if val != p}
            p.terminate()
            p.join()
        except KeyError:
            pass

        # we got it from qtasks
        p = Process(target=qindicator_loop, args=(data,))
        p.start()
        qworkers.append(p)
        qindicators[name] = p
    else:
        log.error("Do not know how to process _task {!r}".format(data['_task']))

    master_watchdog()

def callback_regular_task(ch, method, properties, body):
    data = json.loads(body)
    # print("exch: {} key: {}".format(method.exchange, method.routing_key))

    name = '{}@{}'.format(data.get('name','???'), data.get('textid','???'))
    setproctitle('sensor.process {}'.format(name))

    if data['_task'] == 'tproc.indicator':
        check = Check.from_request(data)

        check.check()
        resp = check.response()
        resp['_machine'] = machine_info

        channel.basic_publish(
            exchange='',
            routing_key=data['resultq'],
            body=json.dumps(resp))
        log.info("{}: {} = {} ({})".format(os.getpid(), name, check.status, check.details))
    else:
        print("Do not know how to process _task {!r}".format(data['_task']))

    setproctitle('sensor.process')

def set_machine_info(args):
    global machine_info

    ctlq = '{}:ctl'.format(args.name)

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

    channel = get_rmq_channel(args)

    channel.exchange_declare(exchange='hello_ex', exchange_type='fanout')

    try:
        while True:
            r['uptime'] = int(time.time() - started)
            channel.basic_publish(
                exchange='hello_ex',
                routing_key='',
                body=json.dumps(r))
            myindicator.update('OK','Uptime: {}'.format(dhms(time.time() - started)))
            #time.sleep(args.sleep)
            connection.sleep(args.hello_sleep)
    except KeyboardInterrupt:
        # normal quit
        pass

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

    channel = get_rmq_channel(args)
    channel.queue_declare(queue='tasks')
    channel.basic_consume(
        queue='tasks', on_message_callback=callback_regular_task, auto_ack=True)

    for qname in machine_info['qlist']:
        channel.queue_declare(queue=qname)
        channel.basic_consume(
            queue=qname, on_message_callback=callback_regular_task, auto_ack=True)

    try:
        channel.start_consuming()
    except KeyboardInterrupt as e:
        pass

def master_watchdog():
    alive_cnt = 0
    dead_cnt = 0
    for p in qworkers:
        if p.is_alive():
            alive_cnt += 1
        else:
            log.debug("reap {}".format(p.pid))
            qworkers.remove(p)
            p.join()
            dead_cnt += 1


def main():
    global log
    global channel
    global args
    global machine_info
    global workers
    global role
    global myindicator

    role = 'master'

    def_pem = '/etc/okerr/ssl/client.pem'
    def_capem = '/etc/okerr/ssl/ca.pem'

    parser = argparse.ArgumentParser(description='okerr indicator MQ tasks client')

    g = parser.add_argument_group('Location')
    g.add_argument('--name', default=os.getenv('SENSOR_NAME','noname@nowhere.tld'))
    g.add_argument('--ip', default=os.getenv('SENSOR_IP',myip()))

    g = parser.add_argument_group('Options')
    g.add_argument('-v', '--verbose', action='store_true', default=False, help='verbose mode')
    g.add_argument('-u', '--unlock', action='store_true', default=False, help='unlock at start')
    g.add_argument('--once', action='store_true', default=False, help='run just once')
    g.add_argument('-s', '--hello-sleep', type=int, default=10, help='sleep time between hello runs')
    g.add_argument('-n', type=int, default=10, help='number of worker processes')


    g = parser.add_argument_group('RabbitMQ options')
    g.add_argument('--rmqhost', default=os.getenv('RMQ_HOST','localhost'), help='RabbitMQ host ($RMQ_HOST, localhost)')
    g.add_argument('--rmqvhost', default=os.getenv('RMQ_VHOST','okerr'), help='RabbitMQ VirtualHost ($RMQ_VHOST, okerr)')
    g.add_argument('--rmquser', default=os.getenv('RMQ_USER', 'okerr'), help='RabbitMQ username (okerr)')
    g.add_argument('--rmqpass', default=os.getenv('RMQ_PASS', 'okerr_default_password'),
                   help='RabbitMQ password (okerr)')
    g.add_argument('--pem', default=def_pem,
                   help='Client cert+key PEM file: {}'.format(def_pem))
    g.add_argument('--capem', default=def_capem,
                   help='CA cert PEM file: {}'.format(def_capem))


    args = parser.parse_args()


    log = logging.getLogger('okerr')
    err = logging.StreamHandler(sys.stderr)
    err.setFormatter(logging.Formatter('%(asctime)s %(message)s',
                                       datefmt='%Y%m%d %H:%M:%S'))
    log.addHandler(err)
    if args.verbose:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)

    sanity_check(args)

    p = Process(target = hello_loop, args=())
    p.start()

    for chindex in range(args.n):
        p = Process(target=worker_loop, args=())
        p.start()
        workers.append(p)

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

    for qname in machine_info['qlist']:
        channel.queue_declare(queue='q:' + qname)
        channel.basic_consume(
            queue='q:'+qname, on_message_callback=callback_ctl, auto_ack=True)

    channel.queue_declare(queue='q:tasks')
    channel.queue_declare(queue=machine_info['ctlq'])
    channel.basic_consume(
        queue=machine_info['ctlq'], on_message_callback=callback_ctl, auto_ack=True, exclusive=True)
    channel.basic_consume(
        queue='q:tasks', on_message_callback=callback_ctl, auto_ack=True)

    log.info("started sensor {}".format(args.name))
    try:
        channel.start_consuming()
    except KeyboardInterrupt as e:
        log.error("Exit because of {}".format(e))
        pass

main()