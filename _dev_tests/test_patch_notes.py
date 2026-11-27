# coding:utf-8
"""版本改动模块的离线测试

不依赖真实网络：解析用抓下来的公告 HTML 片段，缓存/降级用 mock。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.lol import patch_notes as P  # noqa: E402

SAMPLE = '''
<div class="article" id="article">
<h3><strong>版本要闻</strong></h3><p>皮肤上架</p>
<h3><strong>英雄</strong></h3>
<h4 style="text-align: left;"><img src="a.jpg" width="60" /><em>黑暗之女 安妮</em></h4>
<blockquote><p>安妮最近表现低迷。</p></blockquote>
<p><img src="b.jpg" width="30" />R - 提伯斯之怒<br />
提伯斯生命值：1150 - 3500 (随英雄等级提升) (+50%法术强度) &rArr; 1275 - 4000 (随英雄等级提升) (+50%法术强度) (+150%额外生命值)</p>
<h4><em>沙漠皇帝 阿兹尔</em></h4>
<blockquote><p>给他一些关照。</p></blockquote>
<p>基础属性<br />攻击距离：525 &rArr; 550</p>
<h3><strong>装备</strong></h3>
<h4><em>电震涡流剑</em></h4>
<p>唯一被动-苍穹<br />额外伤害： [9% || 7%]目标当前生命值 &rArr; [8% || 6%]目标当前生命值</p>
<h3><strong>海克斯大乱斗</strong></h3>
<p>英雄</p><p>布兰德</p><p>承受伤害：110% &rArr; 105%</p>
<h3><strong>即将到来的皮肤和炫彩</strong></h3><p>新皮肤</p>
</div>
'''


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.items = P._entries('26.23', '2026-11-18', 'https://x', SAMPLE)

    def test_three_kinds(self):
        kinds = {item['kind'] for item in self.items}
        self.assertEqual(kinds, {'英雄', '装备', '系统'})

    def test_hero_title_has_skill_and_change(self):
        annie = next(i for i in self.items if i['name'] == '安妮')
        # 技能只保留 Q/W/E/R 标记（设计稿「亚索：Q 基础伤害」的写法）
        self.assertTrue(annie['title'].startswith('安妮：R '), annie['title'])
        self.assertIn('1150 → 1275', annie['title'])
        self.assertNotIn('·', annie['title'])

    def test_item_title(self):
        item = next(i for i in self.items if i['kind'] == '装备')
        self.assertIn('电震涡流剑', item['title'])
        self.assertIn('9% → 8%', item['title'])

    def test_system_title_has_name(self):
        system = next(i for i in self.items if i['kind'] == '系统')
        self.assertIn('布兰德', system['title'])
        self.assertIn('110% → 105%', system['title'])

    def test_no_html_left(self):
        for item in self.items:
            self.assertNotIn('<', item['title'])
            self.assertNotIn('&', item['title'])

    def test_skin_section_skipped(self):
        names = {item['name'] for item in self.items}
        self.assertNotIn('即将到来的皮肤和炫彩', names)

    def test_empty_body(self):
        self.assertEqual(P._entries('1.0', '', '', '<div class="article"></div>'), [])


class DiffPairTest(unittest.TestCase):
    def test_series_picks_first_change(self):
        self.assertEqual(
            P._diffPair('伤害：80 / 120 / 160 → 80 / 125 / 170'), '120 → 125')

    def test_series_to_single_value(self):
        self.assertEqual(P._diffPair('法力值：45 / 50 / 55 / 60 / 65 → 45'), '45…65 → 45')

    def test_single_side_series(self):
        self.assertEqual(P._diffPair('冷却时间：225 → 225 / 225 / 150'), '225 → 150')

    def test_no_arrow(self):
        self.assertEqual(P._diffPair('没有数值'), '')

    def test_identical(self):
        self.assertEqual(P._diffPair('伤害：10 → 10'), '10 → 10')


class CacheTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cache = Path(self.temp.name) / 'patch.json'
        self.patcher = mock.patch.object(P, 'CACHE_FILE', self.cache)
        self.patcher.start()

        # 这一组测的是缓存本身，把远端那一路按住，免得真的去 GitHub 拿数据
        self.remote = mock.patch.object(P, 'fetchRemote', return_value=None)
        self.remote.start()

    def tearDown(self):
        self.remote.stop()
        self.patcher.stop()
        self.temp.cleanup()

    def test_write_then_read(self):
        P.writeCache({'version': '26.23', 'items': [{'title': 'x'}], 'fetchedAt': 1})
        self.assertEqual(P.readCache()['version'], '26.23')

    def test_read_missing(self):
        self.assertIsNone(P.readCache())

    def test_read_broken_json(self):
        self.cache.write_text('{not json', encoding='utf-8')
        self.assertIsNone(P.readCache())

    def test_network_failure_without_cache_raises(self):
        with mock.patch.object(P, 'fetchPatchNotes', side_effect=P.PatchNotesUnavailable('boom')):
            with self.assertRaises(P.PatchNotesUnavailable):
                P.loadPatchNotes(force=True)

    def test_network_failure_with_cache_marks_stale(self):
        P.writeCache({'version': '26.22', 'items': [{'title': 'x'}], 'fetchedAt': 0})
        with mock.patch.object(P, 'fetchPatchNotes', side_effect=RuntimeError('offline')):
            data = P.loadPatchNotes(force=True)
        self.assertTrue(data['stale'])
        self.assertEqual(data['version'], '26.22')

    def test_fresh_cache_skips_network(self):
        import time
        P.writeCache({'version': '26.23', 'items': [{'title': 'x'}],
                      'fetchedAt': int(time.time())})
        with mock.patch.object(P, 'fetchPatchNotes') as fetch:
            data = P.loadPatchNotes()
        fetch.assert_not_called()
        self.assertFalse(data['stale'])


def note(version='26.23', fetchedAt=1000, items=None, **extra):
    data = {
        'version': version,
        'date': '2026-11-18',
        'url': 'https://lol.qq.com/x.html',
        'fetchedAt': fetchedAt,
        'items': items if items is not None else [
            {'kind': '英雄', 'name': '安妮', 'title': '安妮：R 伤害 1 → 2'},
        ],
    }
    data.update(extra)
    return data


class RemoteSourceTest(unittest.TestCase):
    """远端成品 JSON：校验 + 与官方页的取舍"""

    def test_version_key(self):
        self.assertEqual(P._versionKey('26.23'), (26, 23))
        self.assertEqual(P._versionKey(' 9.1 '), (9, 1))
        self.assertEqual(P._versionKey(''), (0, 0))
        self.assertEqual(P._versionKey('v26.23'), (0, 0))
        self.assertEqual(P._versionKey(None), (0, 0))

    def test_accept_valid_payload(self):
        self.assertTrue(P._validRemote(note(schema=1)))

    def test_reject_broken_payload(self):
        self.assertFalse(P._validRemote(None))
        self.assertFalse(P._validRemote('{}'))
        self.assertFalse(P._validRemote(note(version='2026')))
        self.assertFalse(P._validRemote(note(items=[])))
        self.assertFalse(P._validRemote(note(items=[{'kind': '英雄'}])))
        # 远端格式比客户端新：宁可不用，也不猜
        self.assertFalse(P._validRemote(note(schema=P.REMOTE_SCHEMA + 1)))

    def test_prefer_remote_on_same_version(self):
        official = note(fetchedAt=99999, source='official')
        remote = note(fetchedAt=1, source='remote')

        with mock.patch.object(P, 'fetchPatchNotes', return_value=official), \
                mock.patch.object(P, 'fetchRemote', return_value=remote):
            self.assertEqual(P._fetchFresh()['source'], 'remote')

    def test_prefer_official_when_newer_version(self):
        official = note(version='26.24', source='official')
        remote = note(version='26.23', source='remote')

        with mock.patch.object(P, 'fetchPatchNotes', return_value=official), \
                mock.patch.object(P, 'fetchRemote', return_value=remote):
            self.assertEqual(P._fetchFresh()['version'], '26.24')

    def test_remote_rescues_broken_parser(self):
        remote = note(source='remote')

        with mock.patch.object(P, 'fetchPatchNotes',
                               side_effect=P.PatchNotesUnavailable('正文结构无法识别')), \
                mock.patch.object(P, 'fetchRemote', return_value=remote):
            self.assertEqual(P._fetchFresh()['version'], '26.23')

    def test_both_failed_raises(self):
        with mock.patch.object(P, 'fetchPatchNotes',
                               side_effect=P.PatchNotesUnavailable('网站挂了')), \
                mock.patch.object(P, 'fetchRemote', return_value=None):
            with self.assertRaises(P.PatchNotesUnavailable) as ctx:
                P._fetchFresh()
            self.assertIn('网站挂了', str(ctx.exception))

    def test_cache_age_uses_checked_at(self):
        old = int(__import__('time').time()) - 5 * 3600
        data = note(fetchedAt=old)

        # 远端数据可能是几小时前生成的，但刚刚才确认过 → 不该判成过期
        self.assertGreater(P.cacheAge(data), 4 * 3600)
        self.assertLess(P.cacheAge(dict(data, checkedAt=int(__import__('time').time()))), 5)


class CacheWriteTest(unittest.TestCase):
    """拉回来的数据要记 checkedAt，否则每次切页都会重新打接口"""

    def test_load_writes_checked_at(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'patch_notes.json'
            payload = note(source='remote', fetchedAt=int(time.time()))

            with mock.patch.object(P, 'CACHE_FILE', cache), \
                    mock.patch.object(P, '_fetchFresh', return_value=dict(payload)) as loader:
                data = P.loadPatchNotes(force=True)

                self.assertTrue(cache.exists())
                self.assertTrue(data.get('checkedAt'))
                self.assertFalse(data['stale'])

                # 第二次不该再联网
                P.loadPatchNotes()
                self.assertEqual(loader.call_count, 1)


class StaleMarkTest(unittest.TestCase):
    """远端被冻结时要标出来，否则界面会把旧版本当成当前版本"""

    @staticmethod
    def age(days):
        return int(time.time()) - int(days * 86400)

    def test_remote_fresh_is_clean(self):
        self.assertEqual(P._staleReason(note(source='remote', fetchedAt=self.age(0))), '')

    def test_remote_frozen_marks_outdated(self):
        old = self.age(P.REMOTE_STALE_DAYS + 1)
        self.assertEqual(P._staleReason(note(source='remote', fetchedAt=old)), 'outdated')

    def test_official_never_outdated(self):
        # 官方页每次抓都是当次的时间戳，即便时间戳很旧也不该按远端那套判
        self.assertEqual(P._staleReason(note(source='official', fetchedAt=self.age(30))), '')
        self.assertEqual(P._staleReason(note(source='', fetchedAt=self.age(30))), '')

    def test_load_propagates_outdated(self):
        old = self.age(P.REMOTE_STALE_DAYS + 2)
        payload = note(source='remote', fetchedAt=old)

        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(P, 'CACHE_FILE', Path(folder) / 'p.json'), \
                    mock.patch.object(P, '_fetchFresh', return_value=dict(payload)):
                data = P.loadPatchNotes(force=True)

        self.assertTrue(data['stale'])
        self.assertEqual(data['staleReason'], 'outdated')

    def test_load_offline_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = Path(folder) / 'p.json'

            with mock.patch.object(P, 'CACHE_FILE', cache):
                P.writeCache(note(source='official', fetchedAt=self.age(1)))

                with mock.patch.object(P, 'fetchPatchNotes',
                                       side_effect=P.PatchNotesUnavailable('boom')), \
                        mock.patch.object(P, 'fetchRemote', return_value=None):
                    data = P.loadPatchNotes(force=True)

        self.assertTrue(data['stale'])
        self.assertEqual(data['staleReason'], 'offline')


class LiveParsingTest(unittest.TestCase):
    """真实公告页结构检查；网络不可用时跳过，不让它挂 CI"""

    @classmethod
    def setUpClass(cls):
        try:
            cls.data = P.fetchPatchNotes()
        except Exception as error:
            raise unittest.SkipTest(f'no network: {error}')

    def test_has_items(self):
        self.assertGreater(len(self.data['items']), 8)

    def test_version_format(self):
        self.assertRegex(self.data['version'], r'^\d{1,2}\.\d{1,2}$')

    def test_titles_are_readable(self):
        for item in self.data['items']:
            self.assertIn('：', item['title'], item['title'])
            self.assertLess(len(item['title']), 80, item['title'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
