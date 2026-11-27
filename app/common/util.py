import json
import os
import re
import time
import winreg
from pathlib import Path

import requests
import base64
import subprocess
import psutil
import win32api
import win32gui

from PyQt5.QtCore import QRectF

from app.common.config import cfg, VERSION, GITHUB_USER, GITHUB_REPO
from app.common.logger import logger


TAG = "Util"


class LcuCredentialsUnavailable(RuntimeError):
    """无法从 LeagueClientUx 进程拿到 port / token / server

    历史上这里会退回到调用系统命令行工具查询进程启动参数（需要管理员权限）。
    该工具已被 Win11 24H2 移除，且「起子进程 + 枚举命令行」是杀软启发式的
    典型命中特征，因此**该回退路径已彻底删除，避免杀软误报**。
    现在拿不到就抛这个异常，由调用方决定是提示用户还是当作「没有数据」。
    """


class Github:
    def __init__(self, user=GITHUB_USER, repositories=GITHUB_REPO):
        # 必须是 https: GitHub 对 http 会返回 301, requests 跟随后拿到的仍是 JSON,
        # 但部分网络环境下会被中间设备劫持成登录页
        self.githubApi = "https://api.github.com"

        self.proxyApi = 'https://ghproxy.net'

        self.user = user
        self.repositories = repositories
        self.sess = requests.session()

    @staticmethod
    def __parseVersion(text: str) -> tuple:
        """把 v2.1.0 / 2.1.0 / 2.1 都归一成可比较的元组"""
        parts = []

        for chunk in (text or '').lstrip('vV').split('.'):
            match = re.match(r'\d+', chunk)
            parts.append(int(match.group()) if match else 0)

        return tuple(parts) or (0, )

    def getReleasesInfo(self):
        url = f"{self.githubApi}/repos/{self.user}/{self.repositories}/releases/latest"

        if cfg.get(cfg.enableGithubProxy):
            proxy = {'https': cfg.get(cfg.githubProxyAddr)}
        else:
            proxy = None

        try:
            return self.sess.get(url, proxies=proxy, timeout=12).json()
        except Exception:
            # 断网 / 超时 / DNS 失败都当成"查不到", 不要在启动时抛异常
            return {}

    def checkUpdate(self):
        """
        检查版本更新
        @return: 有更新 -> info, 无更新 -> None

        两条路，按可靠性排序：

        1. GitHub Releases —— 有 Release 时最完整，弹窗能直接下载 zip 自动更新
        2. 仓库里的 `data/update.json` —— 没发 Release 也能通知到用户。
           走 jsDelivr（raw 在国内容易被墙），只是普通文件，不需要 API 权限

        以前只查 Releases，仓库一直没发过 Release，`/releases/latest` 永远 404，
        结果就是「检查更新」永远显示已是最新。
        """
        info = self.getReleasesInfo()

        if isinstance(info, dict) and info.get("tag_name"):
            ver_info = self.__getVersionInfo()
            info["forbidden"] = ver_info.get("forbidden", False)

            # 只有远端**比本地新**才提示。用版本元组比较而不是字符串不等,
            # 否则用户装了更新的内测版后, 反而会被"更新"回旧版本
            remote = self.__parseVersion(info["tag_name"])
            local = self.__parseVersion(VERSION)

            return info if remote > local else None

        # 没有 Release（或 API 连不上）：读仓库清单兜底
        manifest = self.__fetchUpdateManifest()

        if not manifest:
            return None

        remote = self.__parseVersion(manifest.get('version'))
        local = self.__parseVersion(VERSION)

        if remote <= local:
            return None

        # 下游更新弹窗按 Releases 的字段名读，这里补齐成同一种形状
        return {
            'tag_name': f"v{str(manifest.get('version') or '').lstrip('vV')}",
            'body': manifest.get('notes') or '',
            'html_url': (manifest.get('url')
                         or f'https://github.com/{self.user}/{self.repositories}'),
            'assets': manifest.get('assets') or [],
            'forbidden': bool(manifest.get('forbidden', False)),
            'published_at': manifest.get('published') or '',
            'source': 'manifest',
        }

    def __fetchUpdateManifest(self):
        """读仓库里的更新清单 `data/update.json`

        不用 api.github.com：国内经常连不上，而且它要 API 配额。清单就是个
        普通 JSON，jsDelivr 有缓存、raw 备用，和版本公告走同一套路子。

        **两个源都拉，取版本号大的那个**：jsDelivr 对 `@main` 的缓存能滞后
        十几小时（实测推送完 2.2.27，它还在返回 2.2.24）。只看「第一个成功
        的结果」会让用户永远收不到新版本提示 —— 版本号比本地小就当没更新，
        而且是静默的，排查起来很费劲。
        """
        urls = (
            f'https://cdn.jsdelivr.net/gh/{self.user}/{self.repositories}'
            f'@main/data/update.json',
            f'https://raw.githubusercontent.com/{self.user}/{self.repositories}'
            f'/main/data/update.json',
        )

        if cfg.get(cfg.enableGithubProxy):
            proxy = {'https': cfg.get(cfg.githubProxyAddr)}
        else:
            proxy = None

        best = {}
        bestVersion = ()

        for url in urls:
            try:
                resp = self.sess.get(url, proxies=proxy, timeout=8)
                resp.raise_for_status()
                data = resp.json()
            except Exception:
                continue

            # 最少要有 version，否则当成没配
            if not isinstance(data, dict) or not data.get('version'):
                continue

            version = self.__parseVersion(data['version'])

            if not best or version > bestVersion:
                best = data
                bestVersion = version

        return best

    def __getVersionInfo(self):
        url = f'{self.githubApi}/repos/{self.user}/{self.repositories}/contents/document/ver.json'
        if cfg.get(cfg.enableGithubProxy):
            proxy = {'https': cfg.get(cfg.githubProxyAddr)}
        else:
            proxy = None

        res = self.sess.get(url, proxies=proxy).json()

        # [本地补丁] 上游 ver.json 已随仓库清空被删除，404 时返回空字典
        if not isinstance(res, dict) or 'content' not in res:
            return {}

        json_data = json.loads(
            str(base64.b64decode(res['content']), encoding='utf-8'))

        return json_data.get(VERSION, {})

    def getNotice(self):
        url = f'{self.githubApi}/repos/{self.user}/{self.repositories}/contents/document/notice.md'

        if cfg.get(cfg.enableGithubProxy):
            proxy = {'https': cfg.get(cfg.githubProxyAddr)}
        else:
            proxy = None

        try:
            res = self.sess.get(url, proxies=proxy, timeout=12).json()
        except Exception:
            return None

        # 仓库里没放公告文件时返回 404, 这里静默跳过, 不要弹"获取公告失败"
        if not isinstance(res, dict) or 'content' not in res:
            return None

        content = str(base64.b64decode(res['content']), encoding='utf-8')

        return {
            'sha': res['sha'],
            'content': content,
        }


github = Github()


def getLoLPathByRegistry() -> str:
    """
    从注册表获取LOL的安装路径

    ** 只能获取到国服的路径, 外服不支持 **

    无法获取时返回空串
    """
    mainKey = winreg.HKEY_CURRENT_USER
    subKey = r"SOFTWARE\Tencent\LOL"
    valueName = "InstallPath"

    try:
        with winreg.OpenKey(mainKey, subKey) as k:
            installPath, _ = winreg.QueryValueEx(k, valueName)
            path = str(Path(os.path.join(installPath, "TCLS")).absolute()
                       ).replace("\\", "/")
            return f"{path[:1].upper()}{path[1:]}"
    except FileNotFoundError:
        logger.warning("reg path or val does not exist.", TAG)
    except WindowsError as e:
        logger.warning(f"occurred while reading the registry: {e}", TAG)
    except Exception as e:
        logger.exception("unknown error reading registry", e, TAG)

    return ""


def getTasklistPath():
    for path in ['tasklist',
                 'C:/Windows/System32/tasklist.exe']:
        try:
            cmd = f'{path} /FI "imagename eq LeagueClientUx.exe" /NH'
            _ = subprocess.check_output(cmd, shell=True)
            return path
        except:
            pass

    return None


def getLolClientPidSlowly():
    for process in psutil.process_iter():
        if process.name() in ['LeagueClientUx.exe', 'LeagueClientUx']:
            return process.pid

    return -1


def getLolClientPid(path):
    processes = subprocess.check_output(
        f'{path} /FI "imagename eq LeagueClientUx.exe" /NH', shell=True)

    if b'LeagueClientUx.exe' in processes:
        arr = processes.split()
        try:
            pos = arr.index(b"LeagueClientUx.exe")
            return int(arr[pos + 1])
        except ValueError:
            raise ValueError(f"Subprocess return exception: {processes}")
    else:
        return 0


def getLolClientPids(path):
    try:
        processes = subprocess.check_output(
            f'{path} /FI "imagename eq LeagueClientUx.exe" /NH',
            shell=True,
            stderr=subprocess.STDOUT
        )
    except subprocess.CalledProcessError as e:
        logger.error(
            'an error occurred when calling tasklist command, '
            f'original output: {e.output.decode()}'
        )
        raise e

    pids = []

    if not b'LeagueClientUx.exe' in processes:
        return pids

    arr = processes.split()

    for i, s in enumerate(arr):
        if s == b'LeagueClientUx.exe':
            pids.append(int(arr[i + 1]))

    return pids


def getLolClientPidsSlowly():
    pids = []

    for process in psutil.process_iter():
        if process.name() in ['LeagueClientUx.exe', 'LeagueClientUx']:
            pids.append(process.pid)

    return pids


def isLolGameProcessExist(path):
    processes = subprocess.check_output(
        f'{path} /FI "imagename eq League of Legends.exe" /NH', shell=True)

    return b'League of Legends.exe' in processes


def getPortTokenServerByPidViaPsutil(pid):
    port, token, server = None, None, None

    process = psutil.Process(pid)
    cmdline = process.cmdline()

    for cmd in cmdline:

        p = cmd.find("--app-port=")
        if p != -1:
            port = cmd[11:]

        p = cmd.find("--remoting-auth-token=")
        if p != -1:
            token = cmd[22:]

        p = cmd.find("--rso_platform_id=")
        if p != -1:
            server = cmd[18:]

        if port and token and server:
            break

    return port, token, server


def getPortTokenServerByPid(pid):
    '''
    通过进程 id 获得启动命令行参数中的 port、token 以及登录服务器

    psutil 首次枚举进程时客户端可能还没完全就绪，所以失败后等一小会儿再试一次。
    两次都拿不全就抛 LcuCredentialsUnavailable，**不返回 (None, None, None)**，
    避免调用方拼出 `https://riot:None@127.0.0.1:None` 这种请求。

    :raises LcuCredentialsUnavailable: 两次尝试都拿不到完整的三个参数
    '''
    lastError = None

    for attempt in range(2):
        try:
            port, token, server = getPortTokenServerByPidViaPsutil(pid)
        except Exception as e:
            lastError = e
            port, token, server = None, None, None

        if port and token and server:
            return port, token, server

        if attempt == 0:
            # 客户端刚起来时 cmdline 可能还读不到, 短暂等待后重试一次
            time.sleep(0.15)

    raise LcuCredentialsUnavailable(
        f'无法读取进程 {pid} 的客户端启动参数 (port/token/server)') from lastError


def getFileProperties(fname):
    """
    读取给定文件的所有属性, 返回一个字典.

    returns : {'FixedFileInfo': {'Signature': -17890115, 'StrucVersion': 65536, 'FileVersionMS': 917513, 'FileVersionLS':
    38012988, 'ProductVersionMS': 917513, 'ProductVersionLS': 38012988, 'FileFlagsMask': 23, 'FileFlags': 0,
    'FileOS': 4, 'FileType': 1, 'FileSubtype': 0, 'FileDate': None}, 'StringFileInfo': {'Comments': None,
    'InternalName': 'League of Legends (TM) Client', 'ProductName': 'League of Legends (TM) Client', 'CompanyName':
    'Riot Games, Inc.', 'LegalCopyright': 'Copyright (C) 2009', 'ProductVersion': '14.9.580.2108', 'FileDescription':
    'League of Legends (TM) Client', 'LegalTrademarks': None, 'PrivateBuild': None, 'FileVersion': '14.9.580.2108',
    'OriginalFilename': 'League of Legends.exe', 'SpecialBuild': None}, 'FileVersion': '14.9.580.2108'}

    """

    propNames = ('Comments', 'InternalName', 'ProductName',
                 'CompanyName', 'LegalCopyright', 'ProductVersion',
                 'FileDescription', 'LegalTrademarks', 'PrivateBuild',
                 'FileVersion', 'OriginalFilename', 'SpecialBuild')

    props = {'FixedFileInfo': None,
             'StringFileInfo': None, 'FileVersion': None}

    try:
        fixedInfo = win32api.GetFileVersionInfo(fname, '\\')
        props['FixedFileInfo'] = fixedInfo
        props['FileVersion'] = "%d.%d.%d.%d" % (fixedInfo['FileVersionMS'] / 65536,
                                                fixedInfo['FileVersionMS'] % 65536, fixedInfo['FileVersionLS'] / 65536,
                                                fixedInfo['FileVersionLS'] % 65536)

        # \VarFileInfo\Translation returns list of available (language, codepage)
        # pairs that can be used to retreive string info. We are using only the first pair.
        lang, codepage = win32api.GetFileVersionInfo(
            fname, '\\VarFileInfo\\Translation')[0]

        # any other must be of the form \StringfileInfo\%04X%04X\parm_name, middle
        # two are language/codepage pair returned from above

        strInfo = {}
        for propName in propNames:
            strInfoPath = u'\\StringFileInfo\\%04X%04X\\%s' % (
                lang, codepage, propName)
            strInfo[propName] = win32api.GetFileVersionInfo(fname, strInfoPath)

        props['StringFileInfo'] = strInfo
    except:
        return {}
    else:
        return props


def getLolClientVersion():
    gamePath = cfg.get(cfg.lolFolder)[0]

    assert gamePath  # 必须有, 否则就是调用逻辑有问题 -- By Hpero4

    # 特判一下国服 -- By Hpero4
    gamePath = gamePath.replace("/TCLS", "")

    lolExe = f"{gamePath}/Game/League of Legends.exe"
    # 判断一下, 客户端特殊? 为啥会没有LOL的主程序 -- By Hpero4
    if not os.path.exists(lolExe):
        raise FileNotFoundError(lolExe)

    fileInfo = getFileProperties(lolExe).get("StringFileInfo", {})
    lolVer = fileInfo.get("ProductVersion") or fileInfo.get("FileVersion")

    assert lolVer

    # 缩短至大版本号
    return re.search(r"\d+\.\d+", lolVer).group(0)


def getLolClientWindowPos() -> QRectF:
    # 获取客户端窗口句柄
    hwnd = win32gui.FindWindow("RCLIENT", "League of Legends")

    # 如果没客户端，就直接 return 一个 None
    if not hwnd:
        return None

    # 最小化时窗口尺寸不可用，吸附浮窗会算到屏幕外
    if win32gui.IsIconic(hwnd):
        return None

    # 获取客户端窗口位置
    # struct RECT {
    #     LONG left;
    #     LONG top;
    #     LONG right;
    #     LONG bottom;
    # }
    rect = win32gui.GetWindowRect(hwnd)
    width, height = rect[2] - rect[0], rect[3] - rect[1]

    if width < 640 or height < 360:
        return None

    # 原先是 h/w == 0.5625 精确比对，改过分辨率、带边框的客户端会直接判成
    # 不是客户端，BP 浮窗就找不到它。放宽成一个区间。
    ratio = height / width

    if not 0.50 <= ratio <= 0.66:
        return None

    return QRectF(rect[0], rect[1], width, height)
