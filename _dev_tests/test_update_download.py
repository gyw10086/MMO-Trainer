# coding:utf-8
"""在线更新回归：下载地址展开、镜像互换、安装包完整性校验

不联网：只测纯函数，网络那段靠人肉在真实环境验（脚本打印的地址可以直接点）。

覆盖：
  - raw <-> jsDelivr 地址互换（国内两条路一条通一条不通，必须能互相推导）
  - assets 三种写法都能展成候选地址（dict / 纯字符串 / 顶层 download）
  - 候选顺序 = 尝试顺序，且不重复
  - 截断的 zip、大小不符的包必须被拦下（半个文件会毁掉旧版本）
  - 设置里的代理地址要补 scheme —— aiohttp 不认 `127.0.0.1:10809`
  - 发布脚本写出来的地址，客户端能推导出镜像（两边口径一致）
  - 设置页有「立即检查」入口，主窗口有非阻塞入口

运行: python _dev_tests/test_update_download.py
"""
import functools
import os
import sys
import tempfile
import types
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

p = functools.partial(print, flush=True)

from app.common import update

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


JSD = ('https://cdn.jsdelivr.net/gh/gyw10086/MMO-Trainer@release/'
       'MMO-Trainer-2.2.41.zip')
RAW = ('https://raw.githubusercontent.com/gyw10086/MMO-Trainer/release/'
       'MMO-Trainer-2.2.41.zip')


# ---------------------------------------------------------------- 地址互换
def testMirror():
    check('raw -> jsDelivr', update.mirrorOf(RAW) == JSD, update.mirrorOf(RAW))
    check('jsDelivr -> raw', update.mirrorOf(JSD) == RAW, update.mirrorOf(JSD))
    check('来回互推是恒等', update.mirrorOf(update.mirrorOf(RAW)) == RAW)

    check('别的站点不瞎猜',
          update.mirrorOf('https://example.com/a.zip') == '',
          update.mirrorOf('https://example.com/a.zip'))
    check('空串不炸', update.mirrorOf('') == '' and update.mirrorOf(None) == '')

    # 已知限制：分支名里带斜杠（feat/x）会把 ref 切成 `feat`，
    # 推出一个 404 地址。我们的发布分支叫 `release`，不带斜杠，
    # 所以这里只把行为钉住 —— 哪天改成带斜杠的分支名，这条会提醒你。
    deep = ('https://raw.githubusercontent.com/u/r/feat/x/'
            'dist/pkg-1.zip')
    check('分支名带斜杠时不崩（已知切分限制，发布分支不带斜杠）',
          update.mirrorOf(deep).endswith('/pkg-1.zip'), update.mirrorOf(deep))


# ---------------------------------------------------------------- 候选地址
def testCandidates():
    info = {'assets': [{'browser_download_url': JSD, 'mirrors': [RAW]}]}
    urls = update.candidateUrls(info)

    check('dict 资产展开成 2 条（原址 + 推导镜像）', len(urls) == 2, str(urls))
    check('第一条就是清单里给的地址', urls[0] == JSD, urls[0])
    check('第二条是镜像', urls[1] == RAW, urls[1])

    # 已经给了 mirror，不该再推出重复项
    check('候选不重复', len(urls) == len(set(urls)), str(urls))

    strings = update.candidateUrls({'assets': [JSD]})
    check('纯字符串写法也认', strings[0] == JSD and len(strings) == 2, str(strings))

    legacy = update.candidateUrls({'download': RAW})
    check('顶层 download 也认', legacy[0] == RAW, str(legacy))

    check('没有地址时返回空表', update.candidateUrls({}) == [])
    check('None 不炸', update.candidateUrls(None) == [])
    check('脏数据被跳过',
          update.candidateUrls({'assets': [None, 1, {}, {'name': 'x'}]}) == [],
          str(update.candidateUrls({'assets': [None, 1, {}, {'name': 'x'}]})))

    # 顺序即优先级：先给的先试
    two = update.candidateUrls(
        {'assets': [{'browser_download_url': RAW}, {'browser_download_url': JSD}]})
    check('先写的地址排在前面', two[0] == RAW and two[1] == JSD, str(two))


# ---------------------------------------------------------------- 包校验
def makeZip(path, entries=8):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for i in range(entries):
            z.writestr(f'app/file{i}.py', 'x = %d\n' % i * 400)

    return path


def testVerify():
    tmp = tempfile.mkdtemp(prefix='mmo_upd_')

    good = os.path.join(tmp, 'good.zip')
    makeZip(good)
    size = os.path.getsize(good)

    ok, why = update.verifyPackage(good, size)
    check('正常包通过', ok, why)

    ok, _ = update.verifyPackage(good, 0)
    check('清单没写 size 时只验 zip 完整性', ok)

    # 截断：模拟下载中断
    cut = os.path.join(tmp, 'cut.zip')

    with open(good, 'rb') as src, open(cut, 'wb') as dst:
        dst.write(src.read(size // 2))

    ok, why = update.verifyPackage(cut, 0)
    check('截断的包被拦下', not ok, why)

    ok, why = update.verifyPackage(good, size + 200000)
    check('大小与清单不符被拦下', not ok, why)

    empty = os.path.join(tmp, 'empty.zip')
    open(empty, 'wb').close()
    ok, why = update.verifyPackage(empty, 0)
    check('空文件被拦下', not ok, why)

    ok, why = update.verifyPackage(os.path.join(tmp, '不存在.zip'), 0)
    check('文件不存在给出原因', not ok and '读不到' in why, why)

    # 不是 zip 的普通文件
    txt = os.path.join(tmp, 'note.txt')

    with open(txt, 'w', encoding='utf-8') as f:
        f.write('这其实是网页错误信息' * 100)

    ok, why = update.verifyPackage(txt, 0)
    check('不是 zip 被拦下', not ok, why)


# ---------------------------------------------------------------- 代理
def testProxy():
    real = update.cfg

    class FakeCfg:
        """proxyUrl() 是拿 cfg.enableGithubProxy 这个 ConfigItem 当字典键的，
        所以假对象也得有同名属性当哨兵，不能只实现 get()"""

        def __init__(self, on, addr):
            self.enableGithubProxy = object()
            self.githubProxyAddr = object()
            self.values = {self.enableGithubProxy: on,
                           self.githubProxyAddr: addr}

        def get(self, item):
            return self.values[item]

    try:
        update.cfg = FakeCfg(False, '127.0.0.1:7890')
        check('开关关着就不返回代理', update.proxyUrl() == '', update.proxyUrl())

        update.cfg = FakeCfg(True, '127.0.0.1:7890')
        check('没写 scheme 时补 http://', update.proxyUrl() == 'http://127.0.0.1:7890',
              update.proxyUrl())

        update.cfg = FakeCfg(True, 'http://127.0.0.1:7890')
        check('已经有 scheme 就不重复加',
              update.proxyUrl() == 'http://127.0.0.1:7890', update.proxyUrl())

        update.cfg = FakeCfg(True, '')
        check('开着但地址为空时不崩', update.proxyUrl() == '', update.proxyUrl())

        update.cfg = FakeCfg(True, '  socks5://127.0.0.1:1080  ')
        check('别的协议原样保留（去掉首尾空格）',
              update.proxyUrl() == 'socks5://127.0.0.1:1080', update.proxyUrl())
    finally:
        update.cfg = real


# ---------------------------------------------------------------- 发布脚本口径
_publish = []


def loadPublish():
    """导入发布脚本（只导一次）。data 仓库路径是模块级常量，导入本身无副作用"""
    if _publish:
        return _publish[0]

    # E:\seraphine\--Seraphine-main\--Seraphine-main -> E:\seraphine\launcher
    launcher = os.path.join(os.path.dirname(os.path.dirname(ROOT)), 'launcher')

    if launcher not in sys.path:
        sys.path.insert(0, launcher)

    import publish_update

    _publish.append(publish_update)
    return publish_update


def testPublishScript():
    try:
        publish_update = loadPublish()
    except Exception as e:
        check('能导入发布脚本', False, f'{type(e).__name__}: {e}')
        return

    assets = publish_update.buildAssets('2.2.41', 10877724)
    check('发布脚本产出一条资产', len(assets) == 1, str(len(assets)))

    asset = assets[0]
    check('资产里有下载地址和大小',
          bool(asset.get('browser_download_url')) and asset.get('size') == 10877724,
          str(asset))

    # 关键一致性：脚本写的镜像，客户端要能自己推出来
    check('客户端能从主地址推出脚本给的镜像',
          update.mirrorOf(asset['browser_download_url']) == asset['mirrors'][0],
          f"{update.mirrorOf(asset['browser_download_url'])} vs {asset['mirrors'][0]}")

    check('脚本给的地址能被客户端展成候选',
          update.candidateUrls({'assets': assets}) ==
          [asset['browser_download_url'], asset['mirrors'][0]],
          str(update.candidateUrls({'assets': assets})))


def testReleaseCommitName():
    """发布提交里的文件名必须干净

    Windows 下 subprocess 文本模式会把 `\\n` 翻成 `\\r\\n`，`git mktree`
    读到的文件名末尾就会多一个 `\\r`，推上去的下载地址变成 `...zip%0D`，
    用户点开就是 404 —— 而且只有等真去下载才会发现。
    """
    import shutil
    import subprocess
    from pathlib import Path

    try:
        publish_update = loadPublish()
    except Exception as e:
        check('能导入发布脚本', False, f'{type(e).__name__}: {e}')
        return

    tmp = tempfile.mkdtemp(prefix='mmo_git_')

    try:
        r = subprocess.run(['git', 'init', '-q', tmp],
                           capture_output=True, text=True)

        if r.returncode != 0:
            check('本机有 git（跳过文件名检查）', False, r.stderr[:120])
            return

        zipPath = Path(tmp) / 'MMO-Trainer-9.9.9.zip'
        makeZip(zipPath)

        try:
            commit = publish_update.buildReleaseCommit(zipPath, '9.9.9',
                                                       repo=Path(tmp))
        except Exception as e:
            check('能造出发布提交', False, f'{type(e).__name__}: {e}')
            return

        check('能造出发布提交', bool(commit), commit[:8])

        listing = subprocess.run(
            ['git', '-C', tmp, 'ls-tree', '-r', commit],
            capture_output=True, text=True).stdout.strip()
        inTree = listing.split('\t', 1)[-1] if '\t' in listing else ''

        check('提交里的文件名和源文件一致', inTree == zipPath.name, repr(inTree))
        check('文件名里没有回车/换行',
              '\r' not in inTree and '\n' not in inTree, repr(inTree))
        check('提交里只有这一个文件', len(listing.splitlines()) == 1,
              str(len(listing.splitlines())))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- UI 入口
def testEntryPoints():
    src = os.path.join(ROOT, 'app', 'view', 'setting_interface.py')
    text = open(src, encoding='utf-8').read()

    check('设置页有「在线更新」卡片', 'updateCheckCard' in text)
    check('卡片挂在「更新」分组下',
          'updateGroup.addSettingCard(self.updateCheckCard)' in text)
    check('卡片点击有连接',
          'self.updateCheckCard.clicked.connect' in text)

    win = open(os.path.join(ROOT, 'app', 'view', 'main_window.py'),
               encoding='utf-8').read()
    check('主窗口提供非阻塞入口 requestCheckUpdate',
          'def requestCheckUpdate' in win)
    check('入口里开了线程（不阻塞 UI）',
          'threading.Thread' in win.split('def requestCheckUpdate')[1][:600])

    box = open(os.path.join(ROOT, 'app', 'components', 'message_box.py'),
               encoding='utf-8').read()
    check('下载走候选地址列表', 'candidateUrls(self.info)' in box)
    check('下载前做完整性校验', 'verifyPackage(zipPath' in box)
    check('下载带代理', 'proxy=proxy' in box)
    check('全失败时把手动下载放回来', 'self.manuallyButton.setEnabled(True)' in box)


# ---------------------------------------------------------------- main
def main():
    testMirror()
    testCandidates()
    testVerify()
    testProxy()
    testPublishScript()
    testReleaseCommitName()
    testEntryPoints()

    print()

    if _failures:
        print(f'失败 {len(_failures)} 项: {_failures}')
        return 1

    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
