#!/usr/bin/env bash
# 部署 Pattern 雷达 MCP 到 ECS（幂等）。用法：bash deploy.sh
set -uo pipefail

SSH_KEY="${SSH_KEY:-C:/Users/savan/.ssh/hermes-mqtt.pem}"
HOST="${HOST:-root@47.109.58.210}"
RDIR=/opt/pattern-radar
LOCAL="$(cd "$(dirname "$0")/.." && pwd)"
SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15 $HOST"
SCP="scp -i $SSH_KEY -o StrictHostKeyChecking=no -o ConnectTimeout=15"
# 过滤 ssh/scp 的噪声（macOS 提示等）
FLT='grep -v WARNING | grep -v "may need\|vulnerable\|openssh.com"'

echo "════ 1/7 建目录 + 上传代码 ════"
$SSH "mkdir -p $RDIR" 2>&1 | eval $FLT | sed 's/^/  /'
$SCP "$LOCAL/radar.py" "$LOCAL/mcp_server.py" "$HOST:$RDIR/" 2>&1 | eval $FLT | sed 's/^/  /'
$SCP "$LOCAL/deploy/nginx_patch_radar.py" "$HOST:/root/nginx_patch_radar.py" 2>&1 | eval $FLT | sed 's/^/  /'
$SCP "$LOCAL/deploy/pattern-radar.service" "$HOST:/etc/systemd/system/pattern-radar.service" 2>&1 | eval $FLT | sed 's/^/  /'
echo "  上传完成"

echo "════ 2/7 远端语法校验 + selftest（不过就不重启） ════"
$SSH "cd $RDIR && /usr/bin/python3.11 -m py_compile radar.py mcp_server.py && echo '  语法 OK' && /usr/bin/python3.11 mcp_server.py --selftest 2>&1 | tail -6" 2>&1 | eval $FLT | sed 's/^/  /'

echo "════ 3/7 nginx 加 location（只做加法，失败自动还原） ════"
$SSH "python3 /root/nginx_patch_radar.py /etc/nginx/conf.d/savantcat.conf" 2>&1 | eval $FLT | sed 's/^/  /'
if $SSH "nginx -t" 2>/dev/null; then
  echo "  nginx -t 通过"
else
  echo "  ✗ nginx -t 失败 → 还原备份"
  $SSH "cp -a /etc/nginx/conf.d/savantcat.conf.bak_radar /etc/nginx/conf.d/savantcat.conf && nginx -t" 2>&1 | eval $FLT | sed 's/^/  /'
  exit 1
fi

echo "════ 4/7 启服务 + reload nginx ════"
# 注意：`enable --now` 对「已在运行」的服务不会重启 → 换代码后必须显式 restart，
# 否则新代码上了盘、进程仍跑旧版（现象：改了算法但公网输出没变）。
$SSH "systemctl daemon-reload; systemctl enable pattern-radar >/dev/null 2>&1; systemctl restart pattern-radar; systemctl reload nginx; sleep 4; echo -n '  pattern-radar: '; systemctl is-active pattern-radar; echo -n '  nginx:         '; systemctl is-active nginx; echo -n '  进程启动于: '; systemctl show -p ActiveEnterTimestamp --value pattern-radar" 2>&1 | eval $FLT | sed 's/^/  /'

echo "════ 5/7 本地端口握手（127.0.0.1:8768） ════"
$SSH "curl -s -o /dev/null -w '  HTTP %{http_code}\n' -X POST http://127.0.0.1:8768/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"probe\",\"version\":\"1\"}}}'" 2>&1 | eval $FLT | sed 's/^/  /'

echo "════ 6/7 公网三步握手 ════"
$SSH "S=-H'Content-Type: application/json'; curl -s -X POST https://savantcat.cn/mcp-radar -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"probe\",\"version\":\"1\"}}}' | head -c 600" 2>&1 | eval $FLT | sed 's/^/  /'
echo ""

echo "════ 7/7 回滚命令（备用，勿误执行） ════"
echo "  systemctl disable --now pattern-radar && cp -a /etc/nginx/conf.d/savantcat.conf.bak_radar /etc/nginx/conf.d/savantcat.conf && nginx -t && systemctl reload nginx"
