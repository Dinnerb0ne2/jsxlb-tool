# -*- coding: utf-8 -*-
"""打印客户端安装路径 (供 bat 调用)。"""
import sys, os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from client_locator import locate_or_die
print(locate_or_die())
