# -*- coding: utf-8 -*-
"""检查 AndroidManifest.xml（UTF-16 AXML）里的包名与组件名。"""
import zipfile
import re

z = zipfile.ZipFile('patch/kd163_v2.apk')
x = z.read('AndroidManifest.xml')

# AXML 字符串池：先试 UTF-16LE
def utf16_strings(b):
    out = []
    i = 0
    while i < len(b) - 1:
        if 0x20 <= b[i] < 0x7f and b[i + 1] == 0:
            j = i
            s = bytearray()
            while j < len(b) - 1 and 0x20 <= b[j] < 0x7f and b[j + 1] == 0:
                s.append(b[j]); j += 2
            if len(s) >= 4:
                out.append((i, s.decode('ascii')))
                i = j
            else:
                i += 2
        else:
            i += 1
    return out

ss = utf16_strings(x)
print('UTF-16 字符串 %d 个:' % len(ss))
for off, s in ss:
    if '.' in s or s in ('string', 'activity', 'application', 'provider'):
        print('  off=%-6d %s' % (off, s))

print()
print('包名出现次数 com.kdxx.online:', sum(1 for _, s in ss if s == 'com.kdxx.online'))
print('UTF-8 出现次数:', x.count(b'com.kdxx.online'))
