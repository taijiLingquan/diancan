import sys
import os

# 把项目目录加入Python路径
path = '/home/你的用户名/order-system'
if path not in sys.path:
    sys.path.append(path)

from server import app as application
