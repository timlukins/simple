# **THIS IS NOT THE OFFICIAL ROSPY INDEX SERVER**

# rospy for pure Python

## What is this for?

``rospy`` [packages](https://rospypi.github.io/simple/) without ROS installation. This can be run in a pure virtualenv.
It also supports ``tf2`` and experimetally Python3.
So you can run ``rospy`` without ``catkin`` and Python2.

## Install

```bash
virtualenv -p python3 venv
. ./venv/bin/activate
pip install --extra-index-url https://rospypi.github.io/simple/ rospy
pip install --extra-index-url https://rospypi.github.io/simple/ tf2_ros
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
pip install --extra-index-url http://localhost:8000/index/ rospy
```


## Development of this repository

``build.py`` downloads packages from github.com, builds wheel files and generates a Python package server directory.

```bash
virtualenv -p python3 dev
. ./dev/bin/activate
pip install --extra-index-url https://rospypi.github.io/simple/ -e rospy-builder/
rospy-build build
python -m http.server
```

```bash
virtualenv -p python3 venv
. ./venv/bin/activate
pip install --extra-index-url http://localhost:8000/index/ rospy
```

## Generate Message Python Package

```bash
git clone https://github.com/rospypi/simple.git
cd simple
virtualenv -p python3 venv
. ./venv/bin/activate
pip install --extra-index-url https://rospypi.github.io/simple/ -e rospy-builder/
mkdir msgs
(cd msgs; git clone https://github.com/ros/std_msgs.git)
(cd msgs; git clone https://github.com/ros/common_msgs.git)
# (cd msgs; any repository that is depended on your message)
rospy-build genmsg your_package_path -s msgs/
# ex.rospy-build genmsg ros_tutorials/rospy_tutorials/ -s msgs/
```
