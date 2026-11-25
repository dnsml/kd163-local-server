#!/bin/bash
# 雷电模拟器（192.168.0.111:5555）重新登录本地服：重启客户端 → 账号密码 → 开始游戏。
# 每次改完服务端代码都要走这一遍，固化下来省事。
#   用法：bash work/relogin.sh
# 前置：local_server3.py 已在 :9000 监听；账号框已记住 900000102，密码 123456。
ADB="D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
DEV="-s 192.168.0.111:5555"

# ★ 2026-11-26 01:22 踩坑：adb 一重连（雷电重启/掉线重连）reverse 就全丢，
#   但 9000 那条有时还在、8080 那条先没 —— 客户端于是弹「检测资源版本信息失败」，
#   表现就是「本地服进不去」，而服务端日志一条连接都没有。
#   进游戏前先把三条都补齐（reverse 幂等，重复加没事）：
#     8080 -> PC:80    资源服 local_http.py（assets.txt / server-list.xml）
#     9000 -> PC:9000  游戏服 local_server3.py（TLS /auth + 明文协议）
#     19000 -> PC:19000 relay.py 线上服抓包
for p in "tcp:8080 tcp:80" "tcp:9000 tcp:9000" "tcp:19000 tcp:19000"; do
    "$ADB" $DEV reverse $p
done
echo "[relogin] reverse: $("$ADB" $DEV reverse --list | tr '\n' ' ')"

"$ADB" $DEV shell am force-stop com.kdxx.local1
sleep 2
"$ADB" $DEV shell input tap 894 250        # 桌面「口袋修仙 本机服务版」图标
sleep 34
"$ADB" $DEV shell input tap 800 480        # 密码框
sleep 1
"$ADB" $DEV shell input text 123456
sleep 1
"$ADB" $DEV shell input tap 669 585        # 登录
sleep 14
"$ADB" $DEV shell input tap 800 585        # 开始游戏（已续登时点到空白，无害）
sleep 12
echo "[relogin] 已提交登录"
