Okerr remote network sensor

# Install

~~~
apt install libadns1-dev
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
~~~
# Run from shell

~~~
. /etc/okerr/env/sensor
export RMQ_HOST RMQ_PASS RMQ_USER RMQ_VHOST SENSOR_NAME

# sensor.py 
20191228 01:09:35 started sensor deb10@nsk.ru
~~~

# Install as systemd service
~~~
mkdir /var/log/okerr

cp /usr/local/okerrsensor/okerr-sensor.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable okerr-sensor
systemctl start okerr-sensor
~~~

# Build dist from source
~~~
git clone git@github.com:yaroslaff/sensor.git
python3 setup.py bdist_wheel
~~~

# Other okerr resources
- [Okerr main website](https://okerr.com/)
- [Okerr-server source code repository](https://github.com/yaroslaff/okerr-dev/) 
- [Okerr client (okerrupdate) repositoty](https://github.com/yaroslaff/okerrupdate) and [okerrupdate documentation](https://okerrupdate.readthedocs.io/)
- [Okerrbench network server benchmark](https://github.com/yaroslaff/okerrbench)
- [Okerr custom status page](https://github.com/yaroslaff/okerr-status)
- [Okerr JS-powered static status page](https://github.com/yaroslaff/okerrstatusjs)
- [Okerr network sensor](https://github.com/yaroslaff/sensor)


