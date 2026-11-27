# coding:utf-8
"""更新检查回归：Releases 优先、仓库清单兜底

不联网：把两个取数入口都换成假数据，只验比较逻辑和字段映射。

覆盖：
  - 只有清单时：远端更新 -> 有提示；相同/更旧 -> 无提示
  - 清单字段映射成更新弹窗认识的形状（tag_name / body / html_url / assets）
  - 有 Release 时优先用 Release，不被清单覆盖
  - 两边都没有 -> 无提示，且不抛异常
  - forbidden 透传（禁用当前版本时不允许忽略）

运行: python _dev_tests/test_update_manifest.py
"""
import os
import sys
import functools

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

p = functools.partial(print, flush=True)

from app.common.config import VERSION
from app.common.util import Github

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


def build(manifest=None, release=None):
    """造一个 Github 实例，把网络入口全换成假数据"""
    g = Github()
    g.getReleasesInfo = lambda: (release or {})
    g._Github__fetchUpdateManifest = lambda: (manifest or {})
    g._Github__getVersionInfo = lambda: {}

    return g


def testManifestOnly():
    g = build(manifest={
        'version': '9.9.9',
        'notes': '## 本次更新\n\n- 修了个东西',
        'url': 'https://example.com/dl',
        'published': '2026-12-01',
    })

    info = g.checkUpdate()

    check('清单有更新时返回信息', isinstance(info, dict) and bool(info), str(info))
    check('版本号补成 v 前缀', info.get('tag_name') == 'v9.9.9', str(info.get('tag_name')))
    check('更新说明透传', info.get('body', '').startswith('## 本次更新'),
          str(info.get('body'))[:20])
    check('下载地址用清单里的', info.get('html_url') == 'https://example.com/dl',
          str(info.get('html_url')))
    check('没有 assets 时是空列表', info.get('assets') == [], str(info.get('assets')))
    check('来源标记为清单', info.get('source') == 'manifest', str(info.get('source')))


def testSameOrOlder():
    for version in (VERSION, '0.0.1'):
        g = build(manifest={'version': version, 'notes': 'old'})
        check(f'清单 {version} 不比本地新 -> 无提示', g.checkUpdate() is None)


def testVersionCompare():
    """版本按元组比，不能按字符串：2.10 > 2.9"""
    g = build(manifest={'version': '2.10.0'})

    local = VERSION.split('.')

    if len(local) >= 2 and int(local[0]) == 2 and int(local[1]) < 10:
        check('2.10.0 比 2.9.x 新（元组比较）', g.checkUpdate() is not None)
    else:
        check('当前版本已高于 2.10.0，跳过该断言', True)


def testReleaseFirst():
    """有 Release 时优先用它，清单不参与"""
    g = build(
        manifest={'version': '9.9.9', 'notes': '清单里的'},
        release={'tag_name': 'v8.8.8', 'body': 'Release 里的',
                 'html_url': 'https://example.com/release', 'assets': [{'x': 1}]},
    )

    info = g.checkUpdate()

    check('有 Release 时用 Release 的信息',
          info.get('tag_name') == 'v8.8.8' and info.get('body') == 'Release 里的',
          str((info.get('tag_name'), info.get('body'))))
    check('Release 分支不带 source 标记', 'source' not in info,
          str(info.keys()))


def testReleaseOlderFallsBackToNothing():
    """Release 存在但版本旧 -> 无提示，不会被清单救回来"""
    g = build(
        manifest={'version': '9.9.9'},
        release={'tag_name': 'v0.0.1', 'body': ''},
    )

    check('Release 更旧时视为无更新', g.checkUpdate() is None)


def testNothing():
    g = build()
    check('两边都没有 -> 无提示', g.checkUpdate() is None)


def testForbidden():
    g = build(manifest={'version': '9.9.9', 'forbidden': True})
    info = g.checkUpdate()

    check('清单 forbidden 透传', info.get('forbidden') is True,
          str(info.get('forbidden')))


def testBadManifest():
    """清单缺 version 时视为没配，不炸"""
    g = build(manifest={'notes': '没有版本号'})
    check('清单缺 version -> 无提示', g.checkUpdate() is None)

    g2 = build(manifest={'version': ''})
    check('清单 version 为空 -> 无提示', g2.checkUpdate() is None)


def testManifestPickNewest():
    """两个源版本不一致时取大的

    jsDelivr 对 `@main` 的缓存能滞后十几小时：实测推送完 2.2.27，它还在返回
    2.2.24。如果只认「第一个成功的」，用户永远收不到新版本提示。
    """

    class Resp:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    class Sess:
        def __init__(self, pages):
            self.pages = pages
            self.hits = []

        def get(self, url, **kwargs):
            self.hits.append(url)

            for host, data in self.pages.items():
                if host in url:
                    if data is None:
                        raise RuntimeError('blocked')
                    return Resp(data)

            raise RuntimeError(url)

    # 1. jsDelivr 是旧缓存，raw 是新的 -> 取新的
    g = Github()
    g._Github__getVersionInfo = lambda: {}
    g.sess = Sess({
        'cdn.jsdelivr.net': {'version': '2.0.0', 'notes': '旧缓存'},
        'raw.githubusercontent.com': {'version': '9.9.9', 'notes': '新的'},
    })

    info = g.checkUpdate()

    check('jsDelivr 缓存旧时取 raw 的新版本',
          info is not None and info.get('tag_name') == 'v9.9.9', str(info))

    # 只看清单的两个源（前面还会问一次 GitHub Releases）
    manifestHits = [u for u in g.sess.hits if 'update.json' in u]
    check('两个源都问过（不是首个成功就返回）', len(manifestHits) == 2,
          str(manifestHits))

    # 2. raw 被墙（国内常态）-> 用 jsDelivr 的
    g = Github()
    g._Github__getVersionInfo = lambda: {}
    g.sess = Sess({
        'cdn.jsdelivr.net': {'version': '9.9.9', 'notes': '只有这个能用'},
        'raw.githubusercontent.com': None,
    })

    info = g.checkUpdate()
    check('raw 拉不到时退回 jsDelivr',
          info is not None and info.get('tag_name') == 'v9.9.9', str(info))

    # 3. 两个都挂 -> 无更新、不抛
    g = Github()
    g._Github__getVersionInfo = lambda: {}
    g.sess = Sess({
        'cdn.jsdelivr.net': None,
        'raw.githubusercontent.com': None,
    })

    check('两个源都挂时无更新且不抛', g.checkUpdate() is None)


def main():
    testManifestOnly()
    testSameOrOlder()
    testVersionCompare()
    testReleaseFirst()
    testReleaseOlderFallsBackToNothing()
    testNothing()
    testForbidden()
    testBadManifest()
    testManifestPickNewest()

    print()

    if _failures:
        print(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    print('更新检查全部通过')


if __name__ == '__main__':
    main()
