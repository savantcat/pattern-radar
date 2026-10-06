# -*- coding: utf-8 -*-
"""在 ECS 上给 nginx 加 /mcp-radar 挂载点（幂等，可反复跑）。
用法：python3 nginx_patch_radar.py /etc/nginx/conf.d/savantcat.conf
"""
import io
import re
import sys

CONF = sys.argv[1] if len(sys.argv) > 1 else "/etc/nginx/conf.d/savantcat.conf"
ZONE_LINE = "limit_req_zone $binary_remote_addr zone=radar:10m rate=30r/m;   # Pattern 雷达 MCP（/mcp-radar）"

BLOCK = """
    # ===== 竞品内容 Pattern 雷达 MCP（Agent 可调用层，2026-10-04 上线）=====
    # 与 /mcp、/mcp-compliance 同构：只读、公开、带限流，只加这一条 location，不触碰其它路径。
    location = /mcp-radar {
        limit_req zone=radar burst=15 nodelay;
        limit_req_status 429;

        include /etc/nginx/snippets/sec-headers.conf;

        add_header Access-Control-Allow-Origin  "*" always;
        add_header Access-Control-Allow-Methods "GET, POST, DELETE, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Content-Type, Accept, Mcp-Session-Id, MCP-Protocol-Version, Last-Event-ID, Authorization" always;
        add_header Access-Control-Expose-Headers "Mcp-Session-Id" always;
        if ($request_method = OPTIONS) { return 204; }

        proxy_pass http://127.0.0.1:8768/mcp;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection        "";

        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        client_max_body_size 4m;
    }

"""


def main():
    orig = io.open(CONF, encoding="utf-8").read()
    src = orig
    changed = []

    if "location = /mcp-radar" in src:
        print("· location 已存在，跳过")
    else:
        m = re.search(r"(?m)^[ \t]*location / \{", src)
        if not m:
            print("✗ 找不到 `location / {` 锚点，中止（不改动文件）")
            return 1
        src = src[:m.start()] + BLOCK.lstrip("\n") + src[m.start():]
        changed.append("location = /mcp-radar")

    if "zone=radar:" not in src:
        mm = re.search(r"(?m)^limit_req_zone .*zone=mcpsvc:.*$", src)
        if not mm:
            print("✗ 找不到 mcpsvc 限流区锚点，中止")
            return 1
        src = src[:mm.end()] + "\n" + ZONE_LINE + src[mm.end():]
        changed.append("zone=radar")

    if not changed:
        print("· 无需改动")
        return 0

    io.open(CONF + ".bak_radar", "w", encoding="utf-8").write(orig)
    io.open(CONF, "w", encoding="utf-8").write(src)
    print("✅ 已写入：" + "、".join(changed) + "（备份 " + CONF + ".bak_radar）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
