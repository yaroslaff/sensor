Okerr remote network sensor

# Install

~~~shell
PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx install git+https://github.com/yaroslaff/sensor/

PIPX_HOME=/opt/pipx PIPX_BIN_DIR=/usr/local/bin pipx reinstall okerrsensor

# or simpler
pipx install git+https://github.com/yaroslaff/sensor/
~~~

or (old way):
~~~
pip3 install git+https://github.com/yaroslaff/sensor/
~~~


configure `/etc/okerrclient.conf`

Prepare env config file:
~~~
root@deb10:~# cat /etc/okerr/env/sensor  
SENSOR_NAME=deb10@nsk.ru
RMQ_USER=okerr-rabbit-user
RMQ_PASS=okerr-rabbit-secret-password
RMQ_VHOST=okerr
RMQ_HOST=rabbitmq.example.com

SENSOR_IP=1.2.3.4

## only for local debugging
#SENSOR_PEM=.local/ssl/client.pem
# SENSOR_CAPEM=.local/ssl/ca.pem
export SENSOR_NAME RMQ_HOST RMQ_VHOST  RMQ_USER RMQ_PASS SENSOR_PEM SENSOR_CAPEM

~~~
## Run from shell

~~~shell
$ cp .env-example .env
$ vim .env # Adjust settings
$ . .env
$ sudo sensor.py 
20191228 01:09:35 started sensor deb10@nsk.ru
~~~

Specify check from CLI:
~~~
sensor --manual httpstatus url=https://cat.okerr.com/ status=200 options=addr=88.214.35.64
~~~

## Install as systemd service
~~~
mkdir /var/log/okerr

cp /usr/local/okerrsensor/okerr-sensor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable okerr-sensor
systemctl start okerr-sensor
~~~

## Run on low-memory machines
Use `--oneprocess` option, or set env variable `SENSOR_ONEPROCESS` to any non-empty value (e.g. in `/etc/okerr/env/sensor`)

## Build dist from source
~~~
git clone git@github.com:yaroslaff/sensor.git
python3 setup.py bdist_wheel
~~~

## Other okerr resources
- [Okerr main website](https://okerr.com/)
- [Okerr-server source code repository](https://github.com/yaroslaff/okerr-dev/) 
- [Okerr client (okerrupdate) repositoty](https://github.com/yaroslaff/okerrupdate) and [okerrupdate documentation](https://okerrupdate.readthedocs.io/)
- [Okerrbench network server benchmark](https://github.com/yaroslaff/okerrbench)
- [Okerr custom status page](https://github.com/yaroslaff/okerr-status)
- [Okerr JS-powered static status page](https://github.com/yaroslaff/okerrstatusjs)
- [Okerr network sensor](https://github.com/yaroslaff/sensor)


