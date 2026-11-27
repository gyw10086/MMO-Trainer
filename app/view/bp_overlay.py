# coding: utf-8
"""BP 对位浮窗：5 张卡片横排，一个敌人一张

选人阶段贴在客户端上方：对面 5 个人各占一张卡，每张卡把「他打不过谁」
（绿色，可针对）和「他压制谁」（红色，小心）各铺满 5 条。

为什么不是「一个面板里 5 行」：一行只能塞 1+1 条数据，第 2~5 名全看不见。
拆成 5 张卡之后每张都能铺 5+5，信息量是原来的 5 倍。

几处刻意的取舍：

- **一个顶层窗口画 5 张卡**，不是 5 个 QWidget 顶层窗口：拖动、跟随、
  折叠、显示/隐藏都只操作一个对象，5 张的相对位置永远不会错位。卡片之间
  的空隙用 `setMask()` 打掉，点击会穿透到下面的客户端，不会挡操作。
- **全部自绘**：窗口按客户端宽度等比缩放（0.82~1.22），坐标都按 1314 基准写。
- **上方放不下就贴客户端内侧顶部**，不再自动折叠成一条 —— 折叠等于把数据
  全藏起来，和「看得更多」的初衷正好相反。折叠保留，但只由用户主动触发。
- **不抢焦点**：BP 时用户正在点英雄，浮窗弹出来绝不能把客户端焦点夺走。
- **位置记相对客户端的偏移**：客户端挪了、换分辨率了，浮窗还贴着原来那只角。
"""
import time

from PyQt5.QtCore import QPoint, QRect, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap, QRegion
from PyQt5.QtWidgets import QApplication, QMenu, QWidget

from app.common.config import cfg
from app.common import theme
from app.common.util import getLolClientWindowPos

# ---------------------------------------------------------------- 几何
# 基准：1600 宽的客户端，k = 1
#
# 2.2.46 整体放大过一档：原来 CARD_W=258 / LINE_H=17 / 正文 8.5px，
# 是为了「塞得下 5 张」压出来的，实际在 1080p 上字小到看不清。
# 现在 312×165，正文 10px、标题 12px，5 张卡刚好铺满 1600 宽客户端。
CARD_W = 312
CARD_GAP = 6
CARD_COUNT = 5
BASE_W = CARD_W * CARD_COUNT + CARD_GAP * (CARD_COUNT - 1)        # 1584

# 卡片竖着切三段：标题 / 两列各自的表头 / 5 行明细
CARD_HEAD_H = 34
SEC_H = 18
LINE_H = 21
ROWS_PER_SIDE = 5
PANEL_H = CARD_HEAD_H + SEC_H + LINE_H * ROWS_PER_SIDE + 8         # 165

# 折叠态：5 条细条，只留头像 + 名字
BAR_W = 184
BAR_H = 40
BAR_TOTAL_W = BAR_W * CARD_COUNT + CARD_GAP * (CARD_COUNT - 1)     # 944

# 吸附与缩放
# MIN_K 从 0.82 降到 0.78：卡片基准宽度 1584，0.82 时是 1299，
# 1280 宽的客户端就放不下了。0.78 保证 1280 档不溢出。
BASE_CLIENT_W = 1600
MIN_K, MAX_K = 0.78, 1.22
GAP = 8

# 位置偏移的版次。2.2.43 及以前是 360×246 的单面板，2.2.44 是 1314×134 的
# 5 张卡，2.2.46 放大成 1584×165 —— 老偏移照搬会把窗口甩到客户端外面，
# 读到旧版次就整条丢弃。
OFFSET_SCHEMA = 3

# 跟随 / 重绘节拍（ms）：加载态要转圈，节拍提快一点才顺
TICK_IDLE = 120
TICK_LOADING = 60

# 机能风令牌（和主界面 / OP.GG 窗口同一套）
SURFACE = QColor('#101215')
HEAD = QColor('#0E1012')
LINE = QColor('#2C3138')
LINE2 = QColor('#1F2329')
TEXT = QColor('#EAECEF')
TEXT2 = QColor('#9BA3AD')
TEXT3 = QColor('#8D949C')
DIM = QColor('#4A5058')
ACCENT = QColor('#E3B23C')
OK = QColor('#4FA07A')
DAN = QColor('#E05A4F')

ICON_DIR = 'app/resource/game/champion icons/%d.png'

LANE_NAMES = {
    'TOP': '上路', 'JUNGLE': '打野', 'MIDDLE': '中路',
    'BOTTOM': '下路', 'SUPPORT': '辅助',
}

# LCU 的 assignedPosition -> 101 的分路写法
LANE_101 = {
    'TOP': 'TOP', 'JUNGLE': 'JUNGLE', 'MIDDLE': 'MIDDLE', 'MID': 'MIDDLE',
    'BOTTOM': 'BOTTOM', 'BOT': 'BOTTOM', 'ADC': 'BOTTOM',
    'UTILITY': 'SUPPORT', 'SUPPORT': 'SUPPORT', 'SUP': 'SUPPORT',
}


def lane101(lane):
    return LANE_101.get(str(lane or '').strip().upper(), '')


def laneName(lane):
    return LANE_NAMES.get(lane101(lane) or '', '')


def font(size, bold=False):
    f = QFont('Microsoft YaHei UI')
    f.setPixelSize(max(8, int(round(size))))
    f.setBold(bold)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


class BpOverlay(QWidget):
    """5 张敌方对位卡片的容器

    对外仍然是一个「浮窗」：控制器只认 setRoster / setEnemies / showOverlay
    这几个方法，不关心里面是 1 张卡还是 5 张。
    """
    refreshAllRequested = pyqtSignal()
    refreshOneRequested = pyqtSignal(int)
    collapsedChanged = pyqtSignal(bool)
    dismissRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        # 卡片之间要透明，否则 5 张卡会被一整块深色底连起来，看着又像一个面板
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.k = 1.0
        self.collapsed = bool(cfg.get(cfg.bpOverlayCollapsed))

        # 固定 5 格，没锁的位置是 pending
        self.enemies = []
        self.roster = []         # 敌方已锁的（最多 5 条）
        self.lane = ''
        self.tier = cfg.get(cfg.opggTier)
        self.version = ''
        self.source = '国服 101'
        self.state = 'waiting'   # waiting / ready
        self.flashUntil = 0.0
        self.loadingSince = 0.0
        self._cachedAt = 0

        self.__icons = {}
        self.__names = {}
        self.__client = None
        self.__offset = self.__loadOffset()
        self.__dragFrom = None
        self.__dragOrigin = None
        self.__dragged = False
        self.__pressHit = ''

        self.__timer = QTimer(self)
        self.__timer.setInterval(TICK_IDLE)
        self.__timer.timeout.connect(self.__tick)

        # 锁人时的黄框闪 0.6s，到点得重绘一次，否则框一直亮着
        self.__flashTimer = QTimer(self)
        self.__flashTimer.setSingleShot(True)
        self.__flashTimer.timeout.connect(self.update)

        self.resize(self.sizeFor())
        self.__updateMask()

        # 这块浮窗是整块自绘的（颜色用的是本文件里写死的深色令牌，不跟主题），
        # 所以组件 qss 挂上来基本是空的 —— 登记它是为了以后加 qss 子控件时不漏；
        # 真正需要的是重绘：它是独立顶层窗口，不在主窗口子树里，没人通知它
        theme.registerQssRoot(self)
        theme.registerPaintRoot(self)

    # ---------------------------------------------------------------- 几何
    def sizeFor(self):
        w = BAR_TOTAL_W if self.collapsed else BASE_W
        h = BAR_H if self.collapsed else PANEL_H

        return QSize(int(round(w * self.k)), int(round(h * self.k)))

    def resizeToFit(self):
        self.resize(self.sizeFor())
        self.__updateMask()

    def __updateMask(self):
        """把 5 张卡以外的部分从窗口里挖掉

        两个作用：卡片间隙不会画出一块深色底；鼠标点在间隙上会直接穿透到
        下面的客户端 —— 5 张卡横在屏幕上方，挡到英雄选择栏就很难受了。
        """
        k = self.k or 1.0
        region = QRegion()

        if self.collapsed:
            cw, ch, step = BAR_W, BAR_H, BAR_W + CARD_GAP
        else:
            cw, ch, step = CARD_W, PANEL_H, CARD_W + CARD_GAP

        for i in range(CARD_COUNT):
            region = region.united(QRegion(
                int(round(i * step * k)), 0,
                int(round(cw * k)), int(round(ch * k))))

        self.setMask(region)

    def __loadOffset(self):
        # 一次性迁移：老版次记的位置对不上新尺寸，直接清掉回到默认（上方居中）
        if int(cfg.get(cfg.bpOverlayOffsetSchema) or 0) < OFFSET_SCHEMA:
            if cfg.get(cfg.bpOverlayOffset):
                cfg.set(cfg.bpOverlayOffset, '')

            cfg.set(cfg.bpOverlayOffsetSchema, OFFSET_SCHEMA)
            return None

        raw = cfg.get(cfg.bpOverlayOffset) or ''

        try:
            dx, dy = raw.split(',')
            return (float(dx), float(dy))
        except (ValueError, AttributeError):
            return None

    def hasCustomOffset(self):
        return self.__offset is not None

    def resetPosition(self):
        self.__offset = None
        cfg.set(cfg.bpOverlayOffset, '')
        self.__follow(force=True)

    def setCollapsed(self, collapsed, persist=True):
        collapsed = bool(collapsed)

        if collapsed == self.collapsed:
            return

        self.collapsed = collapsed

        if persist:
            cfg.set(cfg.bpOverlayCollapsed, collapsed)

        self.resizeToFit()
        self.__follow(force=True)
        self.collapsedChanged.emit(collapsed)
        self.update()

    # ---------------------------------------------------------------- 数据
    def setLane(self, lane):
        self.lane = lane101(lane) or ''
        self.update()

    def setTier(self, tier):
        self.tier = tier or 'emerald_plus'
        self.update()

    def setWaiting(self):
        self.state = 'waiting'
        self.enemies = []
        self.__timer.setInterval(TICK_IDLE)
        self.update()

    def setRoster(self, roster):
        """敌方已锁的阵容，第 i 个占第 i 张卡

        传进来的每项建议带 `name`（controller 那边查得到，浮窗这边只有
        兜底查询）；不带的话那张卡只显示图标。
        """
        self.roster = list(roster or [])[:CARD_COUNT]
        self.__syncEnemies()
        self.update()

    def __syncEnemies(self):
        """把 roster 摊成固定 5 格

        已经拉过数据的格子要保留 —— 对面又锁一个新人时，不能把前面
        几个人的结果冲掉。
        """
        previous = {e['id']: e for e in self.enemies if e.get('id')}
        slots = []

        for i in range(CARD_COUNT):
            slot = self.roster[i] if i < len(self.roster) else None

            if not slot:
                slots.append({'id': 0, 'name': '', 'lane': '', 'mine': False,
                              'state': 'pending', 'weak': [], 'strong': []})
                continue

            old = previous.get(slot['id']) or {}

            slots.append({
                'id': slot['id'],
                'name': slot.get('name') or old.get('name') or '',
                'lane': slot.get('lane') or '',
                'mine': bool(slot.get('mine')),
                # 新锁的人默认就该转圈；已经在表里的维持原状态
                'state': old.get('state') or 'loading',
                'weak': old.get('weak') or [],
                'strong': old.get('strong') or [],
            })

        self.enemies = slots

    def markEnemiesLoading(self, ids):
        """把这几个人标成「拉取中」，其余已锁的保持原样"""
        wanted = {int(i) for i in ids or []}

        for enemy in self.enemies:
            if enemy.get('id') in wanted:
                enemy['state'] = 'loading'

        if any(e.get('state') == 'loading' for e in self.enemies):
            self.loadingSince = time.time()
            self.__timer.setInterval(TICK_LOADING)
        else:
            self.__timer.setInterval(TICK_IDLE)

        self.update()

    def setEnemies(self, rows, version='', source=''):
        """填数据，按 id 对号入座

        @param rows: [{'id', 'weak', 'strong'}]，顺序无关
        """
        byId = {int(r['id']): r for r in (rows or []) if r.get('id')}

        for enemy in self.enemies:
            if not enemy.get('id'):
                continue

            row = byId.get(enemy['id'])

            if not row:
                continue

            enemy['weak'] = list(row.get('weak') or [])[:ROWS_PER_SIDE]
            enemy['strong'] = list(row.get('strong') or [])[:ROWS_PER_SIDE]
            enemy['state'] = ('ready'
                              if (enemy['weak'] or enemy['strong']) else 'empty')

        if version:
            self.version = version

        if source:
            self.source = source

        locked = [e for e in self.enemies if e.get('id')]
        self.state = 'ready' if locked else 'waiting'
        self.flashUntil = time.time() + 0.6
        self.__timer.setInterval(TICK_IDLE)
        self.__flashTimer.start(700)
        self.update()

    def clearAll(self):
        """跨局清理：阵容、对位、闪烁全清，回到等待态"""
        self.enemies = []
        self.roster = []
        self.lane = ''
        self.version = ''
        self.state = 'waiting'
        self.flashUntil = 0
        self.__timer.setInterval(TICK_IDLE)
        self.update()

    def setCachedAt(self, ts):
        """记下这批数据的取到时间（右键菜单里显示新旧）"""
        self._cachedAt = ts or 0

    def __cacheAt(self):
        return getattr(self, '_cachedAt', 0) or 0

    def __ageText(self):
        ts = self.__cacheAt()

        if not ts:
            return ''

        minutes = int((time.time() - ts) / 60)

        if minutes < 1:
            return '刚刚更新'

        return f'{minutes} 分钟前'

    # ---------------------------------------------------------------- 跟随
    def startFollow(self):
        self.__timer.start()
        self.__follow(force=True)

    def stopFollow(self):
        self.__timer.stop()

    def __tick(self):
        if any(e.get('state') == 'loading' for e in self.enemies):
            self.update()

        self.__follow()

    def __clientRect(self):
        """客户端窗口（逻辑像素）"""
        rect = getLolClientWindowPos()

        if not rect:
            return None

        dpi = self.devicePixelRatioF() or 1.0

        return QRect(int(rect.x() / dpi), int(rect.y() / dpi),
                     int(rect.width() / dpi), int(rect.height() / dpi))

    def __follow(self, force=False):
        if self.__dragFrom is not None:
            return

        client = self.__clientRect()

        if not client:
            if self.isVisible():
                self.hide()
            self.__client = None
            return

        self.__client = client

        k = max(MIN_K, min(MAX_K, client.width() / BASE_CLIENT_W))

        if abs(k - self.k) > 0.001:
            self.k = k
            self.resizeToFit()

        pos = self.__positionFor(client)

        if force or self.pos() != pos:
            self.move(pos)

    def __positionFor(self, client):
        size = self.size()
        W, H = size.width(), size.height()
        wa = QApplication.desktop().availableGeometry()

        if self.__offset:
            dx, dy = self.__offset
            x = int(round(client.x() + dx * self.k))
            y = int(round(client.y() + dy * self.k))
            return QPoint(self.__clampX(x, W, wa), self.__clampY(y, H, wa))

        # 没拖过：横排居中贴客户端上方
        x = client.x() + (client.width() - W) // 2
        y = client.y() - H - int(GAP * self.k)

        if y < wa.top():
            # 上方真放不下（1080p 下客户端基本都贴顶）就压到客户端内侧顶部。
            # 这里**故意不自动折叠** —— 折起来就只剩 5 个头像，用户要的
            # 「看得更多」直接归零，不如让它盖住客户端最上面 134px。
            y = client.y()

        return QPoint(self.__clampX(x, W, wa), self.__clampY(y, H, wa))

    @staticmethod
    def __clampX(x, w, wa):
        return max(wa.left(), min(x, wa.right() - w + 1))

    @staticmethod
    def __clampY(y, h, wa):
        return max(wa.top(), min(y, wa.bottom() - h + 1))

    # ---------------------------------------------------------------- 绘制
    def __icon(self, championId):
        if championId in self.__icons:
            return self.__icons[championId]

        pm = QPixmap(ICON_DIR % championId)
        self.__icons[championId] = pm

        return pm

    def __championName(self, championId):
        """对位列表里的英雄名

        浮窗自己拿不到英雄表，问一下 connector；查不到就留空（不显示 id
        数字，那对用户没意义）。结果按 id 缓存，一局里不会重复问。
        """
        cid = int(championId or 0)

        if cid in self.__names:
            return self.__names[cid]

        name = ''

        try:
            from app.lol.connector import connector
            manager = getattr(connector, 'manager', None)

            if manager:
                name = manager.getChampionNameById(cid) or ''
        except Exception:
            name = ''

        self.__names[cid] = name

        return name

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.scale(self.k, self.k)

        if self.collapsed:
            for i in range(CARD_COUNT):
                self.__paintBar(p, i * (BAR_W + CARD_GAP), i)
        else:
            for i in range(CARD_COUNT):
                self.__paintCard(p, i * (CARD_W + CARD_GAP), i)

        p.end()

    # ------------------------------------------------------ 单张卡片 258×134
    def __paintCard(self, p, x, idx):
        w, h = CARD_W, PANEL_H
        slot = self.enemies[idx] if idx < len(self.enemies) else None
        active = bool(slot and slot.get('id'))
        mine = bool(slot and slot.get('mine'))

        p.fillRect(x, 0, w, h, SURFACE)

        flash = time.time() < self.flashUntil
        p.setPen(QPen(ACCENT if (mine or flash) else LINE, 1))
        p.drawRect(x, 0, w - 1, h - 1)

        if mine:
            # 我的对位那张卡顶上加一道金条，5 张一起看时最容易定位
            p.fillRect(x, 0, w, 2, ACCENT)

        p.fillRect(x + 1, 1, w - 2, CARD_HEAD_H - 1, HEAD)
        p.setPen(QPen(LINE2, 1))
        p.drawLine(x + 1, CARD_HEAD_H, x + w - 2, CARD_HEAD_H)

        if not active:
            self.__paintPending(p, x, slot, mine)
            return

        self.__paintCardHead(p, x, slot, mine)
        self.__paintCardBody(p, x, slot)

    def __paintPending(self, p, x, slot, mine):
        """还没锁人的格子：虚线占位，让人知道这张卡在等谁"""
        w, h = CARD_W, PANEL_H

        p.setPen(QPen(QColor('#2A2F36'), 1, Qt.DashLine))
        p.drawRect(x + 12, 8, 22, 22)

        p.setPen(DIM)
        p.setFont(font(11))
        p.drawText(QRect(x + 42, 2, w - 56, CARD_HEAD_H - 4),
                   Qt.AlignVCenter, '等待敌方锁定')

        p.setPen(QColor('#2A2F36'))
        p.setFont(font(11))
        p.drawText(QRect(x + 8, CARD_HEAD_H + 2, w - 16, h - CARD_HEAD_H - 8),
                   Qt.AlignCenter, '—')

    def __paintCardHead(self, p, x, slot, mine):
        w = CARD_W
        pm = self.__icon(slot['id'])

        if not pm.isNull():
            p.drawPixmap(QRect(x + 12, 6, 22, 22), pm)

        p.setPen(TEXT)
        p.setFont(font(12, True))
        p.drawText(QRect(x + 42, 2, 92, CARD_HEAD_H - 4), Qt.AlignVCenter,
                   self.__elide(p, slot.get('name') or f"英雄 {slot['id']}", 92))

        lane = laneName(slot.get('lane'))
        p.setPen(TEXT3)
        p.setFont(font(10))
        p.drawText(QRect(x + 138, 2, 50, CARD_HEAD_H - 4),
                   Qt.AlignVCenter, lane or '—')

        if mine:
            r = QRect(x + 190, 9, 58, 17)
            p.fillRect(r, QColor(227, 178, 60, 26))
            p.setPen(QPen(QColor(138, 114, 51, 200), 1))
            p.drawRect(r)
            p.setPen(ACCENT)
            p.setFont(font(9.5))
            p.drawText(r, Qt.AlignCenter, '你的对位')

        # 单张卡的刷新键：5 个人里只有一个人数据不对时，不用整批重拉
        p.setPen(TEXT3)
        p.setFont(font(12))
        p.drawText(QRect(x + w - 34, 2, 22, CARD_HEAD_H - 4),
                   Qt.AlignCenter, '⟳')

    def __paintCardBody(self, p, x, slot):
        state = slot.get('state') or 'ready'
        top = CARD_HEAD_H + 3
        bodyH = PANEL_H - top - 2

        if state == 'loading':
            # 圆环 + 文字当成一组在内容区里居中，不然看着像掉到底部去了
            groupH = 20 + 5 + 16
            gy = top + max(0, (bodyH - groupH) // 2)
            cx = x + CARD_W // 2
            self.__paintSpinner(p, QRect(cx - 10, gy, 20, 20))
            p.setPen(TEXT3)
            p.setFont(font(11))
            p.drawText(QRect(x + 8, gy + 25, CARD_W - 16, 16),
                       Qt.AlignCenter, '拉取中…')
            return

        if state == 'empty':
            p.setPen(TEXT3)
            p.setFont(font(11))
            p.drawText(QRect(x + 8, top, CARD_W - 16, bodyH),
                       Qt.AlignCenter, '国服暂无这个对位')
            return

        self.__paintColumn(p, x + 12, top, '他打不过', OK,
                           slot.get('weak') or [])
        self.__paintColumn(p, x + 160, top, '他压制', DAN,
                           slot.get('strong') or [])

    def __paintColumn(self, p, x, y, title, color, rows):
        """一列 5 条：英雄头像 + 名字 + 对位胜率"""
        p.fillRect(x, y + 7, 5, 5, color)

        p.setPen(TEXT2)
        p.setFont(font(10))
        p.drawText(QRect(x + 9, y, 124, SEC_H), Qt.AlignVCenter, title)

        y += SEC_H

        if not rows:
            p.setPen(QColor('#3A4048'))
            p.setFont(font(11))
            p.drawText(QRect(x, y, 130, LINE_H), Qt.AlignVCenter, '—')
            return

        for i in range(min(ROWS_PER_SIDE, len(rows))):
            row = rows[i]
            ry = y + i * LINE_H
            pm = self.__icon(row['championId'])

            if not pm.isNull():
                p.drawPixmap(QRect(x, ry + 2, 18, 18), pm)

            name = self.__championName(row['championId'])

            if name:
                p.setPen(TEXT)
                p.setFont(font(10))
                p.drawText(QRect(x + 23, ry, 72, LINE_H), Qt.AlignVCenter,
                           self.__elide(p, name, 72))

            p.setPen(color)
            p.setFont(font(10, True))
            p.drawText(QRect(x + 95, ry, 42, LINE_H),
                       Qt.AlignVCenter | Qt.AlignRight,
                       f"{row['winRate'] * 100:.1f}%")

    def __paintSpinner(self, p, rect):
        """行内转圈：国服接口偶尔要一两秒，光写「拉取中」看不出是真在跑"""
        p.setPen(QPen(QColor(255, 255, 255, 28), 2))
        p.drawEllipse(rect)

        angle = int((time.time() - self.loadingSince) * 320) % 360
        p.setPen(QPen(ACCENT, 2, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, int(-angle * 16), int(100 * 16))

    # -------------------------------------------------------- 折叠条 152×34
    def __paintBar(self, p, x, idx):
        w, h = BAR_W, BAR_H
        slot = self.enemies[idx] if idx < len(self.enemies) else None
        active = bool(slot and slot.get('id'))
        mine = bool(slot and slot.get('mine'))

        p.fillRect(x, 0, w, h, SURFACE)

        flash = time.time() < self.flashUntil
        p.setPen(QPen(ACCENT if (mine or flash) else LINE, 1))
        p.drawRect(x, 0, w - 1, h - 1)

        if mine:
            p.fillRect(x, 9, 3, 22, ACCENT)

        if not active:
            p.setPen(QPen(QColor('#2A2F36'), 1, Qt.DashLine))
            p.drawRect(x + 10, 9, 20, 20)
            p.setPen(DIM)
            p.setFont(font(11))
            p.drawText(QRect(x + 38, 0, w - 46, h), Qt.AlignVCenter, '待锁定')
            return

        pm = self.__icon(slot['id'])

        if not pm.isNull():
            p.drawPixmap(QRect(x + 10, 9, 22, 22), pm)

        p.setPen(TEXT)
        p.setFont(font(12, True))
        p.drawText(QRect(x + 40, 0, w - 50, h), Qt.AlignVCenter,
                   self.__elide(p, slot.get('name') or f"英雄 {slot['id']}",
                                w - 54))

    @staticmethod
    def __elide(p, text, width):
        fm = QFontMetrics(p.font())

        return fm.elidedText(text, Qt.ElideRight, max(10, width))

    # ---------------------------------------------------------------- 交互
    def __hitTest(self, pos):
        x, y = pos.x() / self.k, pos.y() / self.k

        if self.collapsed:
            if y > BAR_H:
                return ''

            idx = int(x // (BAR_W + CARD_GAP))

            if idx >= CARD_COUNT or x - idx * (BAR_W + CARD_GAP) > BAR_W:
                return ''

            return f'bar:{idx}'

        if y > PANEL_H:
            return ''

        idx = int(x // (CARD_W + CARD_GAP))

        if idx >= CARD_COUNT:
            return ''

        localX = x - idx * (CARD_W + CARD_GAP)

        if localX > CARD_W:
            return ''

        slot = self.enemies[idx] if idx < len(self.enemies) else None

        if (y <= CARD_HEAD_H and slot and slot.get('id')
                and localX >= CARD_W - 34):
            return f'refresh:{idx}'

        return f'card:{idx}'

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            self.__showMenu(event.globalPos())
            return

        if event.button() != Qt.LeftButton:
            return

        self.__pressHit = self.__hitTest(event.pos())
        self.__dragFrom = event.globalPos()
        self.__dragOrigin = self.pos()
        self.__dragged = False

    def mouseMoveEvent(self, event):
        if self.__dragFrom is None:
            return

        delta = event.globalPos() - self.__dragFrom

        if not self.__dragged and delta.manhattanLength() < 4:
            return

        self.__dragged = True
        self.move(self.__dragOrigin + delta)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self.__dragFrom is None:
            return

        hit = self.__pressHit
        dragged = self.__dragged

        self.__dragFrom = None
        self.__dragOrigin = None
        self.__dragged = False
        self.__pressHit = ''

        if dragged:
            self.__saveOffset()
            return

        if hit.startswith('refresh:'):
            index = int(hit.split(':', 1)[1])
            slot = self.enemies[index] if index < len(self.enemies) else None

            if slot and slot.get('id'):
                self.refreshOneRequested.emit(slot['id'])
        elif hit.startswith('bar:'):
            # 折叠态点哪一条都是展开（单独展开某张卡没意义：宽度按 5 张算的）
            self.setCollapsed(False)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.resetPosition()

    def __saveOffset(self):
        client = self.__client or self.__clientRect()

        if not client or not self.k:
            return

        dx = (self.x() - client.x()) / self.k
        dy = (self.y() - client.y()) / self.k
        self.__offset = (dx, dy)
        cfg.set(cfg.bpOverlayOffset, f'{dx:.1f},{dy:.1f}')

    def __showMenu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(
            'QMenu{background:#101215;color:#EAECEF;border:1px solid #2C3138;'
            'padding:4px;}'
            'QMenu::item{padding:5px 18px;font-size:12px;}'
            'QMenu::item:selected{background:#262C33;}'
            'QMenu::item:disabled{color:#5A626C;}'
            'QMenu::separator{height:1px;background:#1F2329;margin:4px 6px;}')

        # 数据新旧放在最上面，看一眼就知道要不要刷
        age = self.__ageText()
        src = self.source + (f' · {self.version}' if self.version else '')
        head = f'{src} · {age}' if age else src

        if head:
            info = menu.addAction(head)
            info.setEnabled(False)
            menu.addSeparator()

        menu.addAction('刷新全部（5 人）', self.refreshAllRequested.emit)

        if self.collapsed:
            menu.addAction('展开 5 张卡', lambda: self.setCollapsed(False))
        else:
            menu.addAction('收起成 5 条', lambda: self.setCollapsed(True))

        menu.addSeparator()
        menu.addAction('复位位置', self.resetPosition)
        menu.addAction('关闭浮窗（本局）', self.dismissRequested.emit)

        menu.exec_(pos)

    # ---------------------------------------------------------------- 显示
    def showOverlay(self):
        self.show()
        self.raise_()
        self.startFollow()

    def hideOverlay(self):
        self.stopFollow()
        self.hide()
