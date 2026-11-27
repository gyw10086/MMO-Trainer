# coding:utf-8
"""客户端凭据（port / token / server）获取失败语义回归

2.2.48 把「起系统命令行工具查进程参数」的回退整个删掉了（那个工具在 Win11
24H2 已被移除，且「起子进程 + 枚举命令行」是杀软启发式的典型命中特征）。
删掉回退以后，失败语义必须是
**明确抛异常**，不能悄悄返回 (None, None, None) —— 否则上层会拼出
`https://riot:None@127.0.0.1:None` 这种请求，报错信息完全指不到病根。

覆盖：
  - 自定义异常 LcuCredentialsUnavailable 是 RuntimeError 子类
  - psutil 版拿全 → 直接用，不重试、不睡
  - 第一次失败（异常）→ 等约 0.15s 再试一次，第二次成功就用
  - 第一次返回 (None,None,None)（没抛异常但参数不全）也算失败，同样重试
  - 两次都失败 → 抛 LcuCredentialsUnavailable，异常链里带着原始异常
  - 只拿到部分参数（有 port 没 token）也算失败
  - connector.getLoginSummonerByPid：拿不到凭据时返回 None 且**不发 HTTP 请求**
  - 请求本身炸了也返回 None，不把异常甩给 UI 线程
  - 拿到凭据时拼出的 URL 里 token / port 是正确的

运行: python _dev_tests/test_lcu_credentials.py
"""
import functools
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

p = functools.partial(print, flush=True)

from app.common import util
from app.common.util import (LcuCredentialsUnavailable, getPortTokenServerByPid)
from app.lol import connector

_failures = []


def check(name, ok, detail=''):
    print(f'{"PASS" if ok else "FAIL"}  {name}' + (f'  ({detail})' if detail else ''))

    if not ok:
        _failures.append(name)


class FakePsutil:
    """按顺序吐出预设结果，记录调用次数"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, pid):
        self.calls += 1

        item = self.results.pop(0) if self.results else Exception('no more')

        if isinstance(item, Exception):
            raise item

        return item


class Patch:
    """临时替换 module 属性"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        self.orig = {k: getattr(util, k) for k in self.kwargs}

        for k, v in self.kwargs.items():
            setattr(util, k, v)

        return self

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(util, k, v)

        return False


OK = ('50001', 'tok-abc', 'HN1')


def testExceptionType():
    check('LcuCredentialsUnavailable 是 RuntimeError 子类',
          issubclass(LcuCredentialsUnavailable, RuntimeError))
    # 名字也拼出来: 这个文件同样要被「全仓无该关键字」的尺子量到
    legacySuffix = 'Via' + 'W' + 'mic'

    check(f'util 里不再有 *{legacySuffix} 函数',
          not [n for n in dir(util) if n.endswith(legacySuffix)])


def testOkNoRetry():
    fake = FakePsutil([OK])

    with Patch(getPortTokenServerByPidViaPsutil=fake):
        start = time.time()
        result = getPortTokenServerByPid(4242)
        elapsed = time.time() - start

    check('拿全了三元组', result == OK, str(result))
    check('只调一次 psutil', fake.calls == 1, str(fake.calls))
    check('不白白 sleep', elapsed < 0.12, f'{elapsed:.3f}s')


def testRetryAfterException():
    fake = FakePsutil([RuntimeError('AccessDenied'), OK])

    with Patch(getPortTokenServerByPidViaPsutil=fake):
        start = time.time()
        result = getPortTokenServerByPid(4242)
        elapsed = time.time() - start

    check('第一次抛异常后重试成功', result == OK, str(result))
    check('调了两次 psutil', fake.calls == 2, str(fake.calls))
    check('两次之间等了约 0.15s', elapsed >= 0.14, f'{elapsed:.3f}s')


def testRetryAfterPartial():
    """没抛异常但参数不全（客户端刚起、cmdline 还没补齐）也要重试"""
    fake = FakePsutil([('50001', None, None), OK])

    with Patch(getPortTokenServerByPidViaPsutil=fake):
        result = getPortTokenServerByPid(4242)

    check('参数不全 → 重试后成功', result == OK, str(result))
    check('调了两次 psutil', fake.calls == 2, str(fake.calls))


def testRaiseAfterTwoFailures():
    fake = FakePsutil([RuntimeError('AccessDenied'), RuntimeError('AccessDenied')])

    with Patch(getPortTokenServerByPidViaPsutil=fake):
        try:
            getPortTokenServerByPid(4242)
        except LcuCredentialsUnavailable as e:
            check('两次失败抛出 LcuCredentialsUnavailable', True)
            check('异常信息带 pid', '4242' in str(e), str(e))
            check('异常链里保留原始异常',
                  isinstance(e.__cause__, RuntimeError), repr(e.__cause__))
        except Exception as e:  # noqa: BLE001
            check('两次失败抛出 LcuCredentialsUnavailable', False, repr(e))
        else:
            check('两次失败抛出 LcuCredentialsUnavailable', False, '没有抛异常')

    check('两次都试过', fake.calls == 2, str(fake.calls))


def testRaiseOnPartialBothTimes():
    fake = FakePsutil([('50001', 'tok-abc', None), ('50001', None, 'HN1')])

    with Patch(getPortTokenServerByPidViaPsutil=fake):
        raised = False

        try:
            getPortTokenServerByPid(4242)
        except LcuCredentialsUnavailable:
            raised = True
        except Exception as e:  # noqa: BLE001
            check('部分参数不算成功', False, repr(e))

    check('部分参数不算成功', raised)


def testNeverReturnsNoneTriple():
    """绝不能退化成 (None, None, None)"""
    fake = FakePsutil([Exception('boom'), Exception('boom')])

    with Patch(getPortTokenServerByPidViaPsutil=fake):
        try:
            result = getPortTokenServerByPid(4242)
        except LcuCredentialsUnavailable:
            result = 'raised'

    check('失败不返回 (None, None, None)', result == 'raised', str(result))


# ------------------------------------------------------- connector 调用方语义
class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.url = None

    def json(self):
        return self.payload


class FakeRequests:
    def __init__(self, payload=None, error=None):
        self.payload = payload or {'gameName': 'Faker', 'displayName': 'Faker'}
        self.error = error
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))

        if self.error:
            raise self.error

        resp = FakeResponse(self.payload)
        resp.url = url

        return resp


def testLoginSummonerWithoutCredentials():
    def boom(pid):
        raise LcuCredentialsUnavailable('no creds')

    fakeRequests = FakeRequests()

    origPort = connector.getPortTokenServerByPid
    origRequests = connector.requests

    connector.getPortTokenServerByPid = boom
    connector.requests = fakeRequests

    try:
        result = connector.connector.getLoginSummonerByPid(4242)
    finally:
        connector.getPortTokenServerByPid = origPort
        connector.requests = origRequests

    check('拿不到凭据时返回 None', result is None, repr(result))
    check('拿不到凭据时一个请求都不发', not fakeRequests.calls, str(fakeRequests.calls))


def testLoginSummonerWithCredentials():
    fakeRequests = FakeRequests()

    origPort = connector.getPortTokenServerByPid
    origRequests = connector.requests

    connector.getPortTokenServerByPid = lambda pid: OK
    connector.requests = fakeRequests

    try:
        result = connector.connector.getLoginSummonerByPid(4242)
    finally:
        connector.getPortTokenServerByPid = origPort
        connector.requests = origRequests

    check('拿到凭据时返回数据', result == {'gameName': 'Faker',
                                          'displayName': 'Faker'}, str(result))
    check('发了一个请求', len(fakeRequests.calls) == 1, str(len(fakeRequests.calls)))

    if fakeRequests.calls:
        url = fakeRequests.calls[0][0]
        check('URL 里 token 正确', 'riot:tok-abc@' in url, url)
        check('URL 里 port 正确', ':50001/lol-summoner/v1/current-summoner' in url, url)
        check('URL 里没有 None', 'None' not in url, url)


def testLoginSummonerRequestError():
    fakeRequests = FakeRequests(error=RuntimeError('connection reset'))

    origPort = connector.getPortTokenServerByPid
    origRequests = connector.requests

    connector.getPortTokenServerByPid = lambda pid: OK
    connector.requests = fakeRequests

    try:
        result = connector.connector.getLoginSummonerByPid(4242)
    finally:
        connector.getPortTokenServerByPid = origPort
        connector.requests = origRequests

    check('请求异常时返回 None 而不是抛出去', result is None, repr(result))


def main():
    testExceptionType()
    testOkNoRetry()
    testRetryAfterException()
    testRetryAfterPartial()
    testRaiseAfterTwoFailures()
    testRaiseOnPartialBothTimes()
    testNeverReturnsNoneTriple()
    testLoginSummonerWithoutCredentials()
    testLoginSummonerWithCredentials()
    testLoginSummonerRequestError()

    print()

    if _failures:
        p(f'{len(_failures)} 项失败: {_failures}')
        sys.exit(1)

    p('全部通过')


if __name__ == '__main__':
    main()
