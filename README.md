# rospy for pure Python

## What is this for?

``rospy`` packages without ROS installation. This can be run in a pure virtualenv.
It also supports ``tf2`` and experimetally Python3.
So you can run ``rospy`` without ``catkin`` and Python2.

Note: ``tf2`` is only supported in Ubuntu.

## Install

```bash
virtualenv -p python3 venv
. ./venv/bin/activate
pip install --extra-index-url https://otamachan.github.io/rospy3/ rospy3
pip install --extra-index-url https://otamachan.github.io/rospy3/ tf2_ros tf2_py
```

## Sample

```python
import os

import rospy
import std_msgs.msg


def callback(msg):
    print(msg)


os.environ['ROS_MASTER_URI'] = 'http://localhost:11311'
os.environ['ROS_PYTHON_LOG_CONFIG_FILE'] = '|'  # specify dummy file
rospy.init_node("hoge")
rospy.loginfo('start')
sub = rospy.Subscriber("sub", std_msgs.msg.String, callback)
pub = rospy.Publisher('pub', std_msgs.msg.Int16, queue_size=10)
rate = rospy.Rate(1)
while not rospy.is_shutdown():
    pub.publish(3)
    rate.sleep()
```

Enjoy!

## Start a local pypi server

```bash
docker build -t localpypi .
docker run --rm -p 8000:8000 localpypi
```

```bash
virtualenv -p python3 venv
. ./venv/bin/activate
pip install --extra-index-url http://localhost:8000 rospy3
```


## Development of this repository

``build.py`` downloads packages from github.com, builds wheel files and generates a Python package server directory.

```bash
virtualenv -p python3 dev
. ./dev/bin/activate
./install.sh
./build.py
cd index
python -m http.server
```

```bash
virtualenv -p python3 venv
. ./venv/bin/activate
pip install --extra-index-url http://localhost:8000 rospy3
```