# -*- coding: utf-8 -*-
"""统一入口：本仓库的 MCP 服务端（约定文件名 server.py）。

实现全部在 mcp_server.py；这里只是让「server.py」这个约定文件名存在，
目录站 / Registry / 评分器的扫描器按它识别「这是个 MCP server」。

用法与 mcp_server.py 完全一致：
  python server.py                     # stdio（桌面客户端）
  python server.py --http --port 8768  # streamable-http（远程）
  python server.py --selftest          # 不走协议，直接打全部工具
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

if __name__ == "__main__":
    runpy.run_path(os.path.join(HERE, "mcp_server.py"), run_name="__main__")
