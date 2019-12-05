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
from multiprocessing import Process, current_process
from setproctitle import setproctitle

from check import Check

log = logging.getLogger('okerr')
channel = None
hello_pid = None
worker_pid = list()
args = None
machine_info = None

def signal_handler(sig, frame):
    # print('<{}>: got signal hello: {}'.format(os.getpid(), hello_pid))
    if hello_pid:
        # print("<{}> send signal to: {}".format(os.getpid(), hello_pid))
        os.kill(hello_pid, sig)
    sys.exit(0)


def hello():
    r = {
        '_task': 'tproc.hello',
        '_machine': machine_info
    }

    # print("Hello {}: send hello".format(os.getpid()))
    channel.basic_publish(
        exchange='',
        routing_key='results',
        body=json.dumps(r))

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

def callback(ch, method, properties, body):
    data = json.loads(body)

    name = '{}@{}'.format(data.get('name','???'), data.get('textid','???'))
    setproctitle('okerrtp: process {}'.format(name))

    if data['_task'] == 'tproc.indicator':
        check = Check.from_request(data)
        check.check()
        resp = check.response()
        resp['_machine'] = machine_info

        channel.basic_publish(
            exchange='',
            routing_key='results',
            body=json.dumps(resp))
        log.info("{}: {} = {} ({})".format(current_process().name, name, check.status, check.details))

    elif data['_task'] == 'tproc.ping':
        reply_ping()
    elif data['_task'] == 'tproc.exception':
        print("process exception", data)
    else:
        print("Do not know how to process _task {!r}".format(data['_task']))

    setproctitle('okerrtp: free')

def set_machine_info(args):
    global machine_info
    machine_info = {
        'ip': args.ip,
        'name': args.name,
        'location': args.location,
        'pid': os.getpid()
    }


def hello_loop():
    global channel
    setproctitle('okerrtp: hello')
    set_machine_info(args)
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=args.rmqhost))
    channel = connection.channel()

    channel.queue_declare(queue='results')
    while True:
        hello()
        time.sleep(args.sleep)

def worker_loop():
    global channel, machine_info

    machine_info = {
        'ip': args.ip,
        'name': args.name,
        'location': args.location,
        'pid': os.getpid(),
        'pname': current_process().name
    }

    set_machine_info(args)
    setproctitle('okerrtp: free')

    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=args.rmqhost))
    channel = connection.channel()

    channel.queue_declare(queue='tasks')
    channel.queue_declare(queue='results')

    channel.basic_consume(
        queue='tasks', on_message_callback=callback, auto_ack=True)

    channel.start_consuming()


def main():
    global log
    global channel
    global args
    global hello_pid
    global machine_info

    parser = argparse.ArgumentParser(description='okerr indicator MQ tasks client')

    g = parser.add_argument_group('Location')
    g.add_argument('--name', default=os.getenv('NETPROCESS_NAME','noname'))
    g.add_argument('--location', default=os.getenv('NETPROCESS_LOCATION','nowhere.tld'))
    g.add_argument('--ip', default=os.getenv('NETPROCESS_IP',myip()))

    g = parser.add_argument_group('Options')
    g.add_argument('-v', '--verbose', action='store_true', default=False, help='verbose mode')
    g.add_argument('--rmqhost', default='localhost', help='RabbitMQ host (localhost)')
    g.add_argument('-u', '--unlock', action='store_true', default=False, help='unlock at start')
    g.add_argument('--once', action='store_true', default=False, help='run just once')
    g.add_argument('-s', '--sleep', type=int, default=60, help='sleep time between runs')
    g.add_argument('-n', type=int, default=1, help='number of worker processes')

    args = parser.parse_args()

    if args.verbose:
        err = logging.StreamHandler(sys.stderr)
        log.addHandler(err)
        log.setLevel(logging.DEBUG)
        log.debug('Verbose mode')

    p = Process(target = hello_loop, args=())
    p.start()

    for chindex in range(args.n):
        p = Process(target=worker_loop, args=())
        p.start()

    setproctitle('okerrtp: master ({})'.format(args.n))
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGHUP, signal_handler)



main()