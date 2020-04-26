Okerr remote network sensor

# Install

~~~
apt install libadns1-dev
pip3 install git+https://gitlab.com/yaroslaff/sensor/
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
git clone git@gitlab.com:yaroslaff/sensor.git
python3 setup.py bdist_wheel
~~~

# Other okerr resources
- [Okerr main website](https://okerr.com/)
- [Okerr-server source code repository](gitlab.com/yaroslaff/okerr-dev/) and [okerr server wiki doc](https://gitlab.com/yaroslaff/okerr-dev/wikis/)
- [Okerr client (okerrupdate) repositoty](https://gitlab.com/yaroslaff/okerrupdate) and [okerrupdate wiki doc](https://gitlab.com/yaroslaff/okerrupdate/wikis/)
- [Okerrbench network server benchmark](https://gitlab.com/yaroslaff/okerrbench)
- [Okerr network sensor](https://gitlab.com/yaroslaff/sensor)
