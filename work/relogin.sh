#!/bin/bash
# 雷电模拟器（192.168.0.111:5555）重新登录本地服：重启客户端 → 账号密码 → 开始游戏。
# 每次改完服务端代码都要走这一遍，固化下来省事。
#   用法：bash work/relogin.sh
# 前置：local_server3.py 已在 :9000 监听；账号框已记住 900000102，密码 123456。
ADB="D:/66666/tools/scrcpy-win64-v5.0.1/adb.exe"
DEV="-s 192.168.0.111:5555"

"$ADB" $DEV shell am force-stop com.kdxx.local1
sleep 2
"$ADB" $DEV shell input tap 894 250        # 桌面「口袋修仙 本机私服版」图标
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
