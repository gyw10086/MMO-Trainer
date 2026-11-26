# MMO-Trainer · 线上数据

这个仓库托管 **MMO-Trainer 客户端运行时要用、但不适合写死在 exe 里的数据**。
客户端用固定的 Raw 地址读，**更新数据不需要发新版本**。

## 现在托管什么

| 文件 | 内容 | 谁在刷 |
|---|---|---|
| `data/patch_notes.json` | 英雄联盟国服「版本更新公告」解析结果（英雄 / 装备 / 系统改动） | GitHub Actions，每 6 小时 |

## 客户端怎么读

```
https://raw.githubusercontent.com/gyw10086/MMO-Trainer/main/data/patch_notes.json
https://cdn.jsdelivr.net/gh/gyw10086/MMO-Trainer@main/data/patch_notes.json
```

Raw 不通时自动走 jsDelivr，两个地址内容完全一致。

客户端的行为：

1. 本地缓存 45 分钟内有效，命中直接用，不打网络
2. 过期后**并发**请求「官方公告页」和「本仓库 JSON」，按 (版本号, 来源) 取新的
3. 两个都失败 → 退回本地旧缓存，界面标「缓存」
4. 一个都没拿到 → 界面显示「暂时连不上官方公告」

为什么要有这一路：解析规则写在客户端里，官网一改版，已经发出去的旧版本就再也
解析不出来，只能等发版修。有了这份提前解析好的 JSON，官网改版时只要更新仓库
里的数据（必要时连带改 `tools/refresh_patch_notes.py`），所有旧客户端立刻恢复。

## 数据结构

```json
{
  "schema": 1,
  "version": "26.23",
  "date": "2026-11-18",
  "url": "https://lol.qq.com/gicp/news/410/37102797.html",
  "fetchedAt": 1795721955,
  "items": [
    {
      "kind": "英雄",
      "name": "安妮",
      "title": "安妮：R 提伯斯生命值 1150 → 1275",
      "detail": "提伯斯生命值：1150 - 3500 …",
      "version": "26.23",
      "date": "2026-11-18",
      "url": "https://lol.qq.com/…"
    }
  ]
}
```

- `schema`：格式版本，客户端只认 `<=` 自己支持的那个，加字段时递增
- `kind`：`英雄` / `装备` / `系统`
- `title`：面板上显示的那一行；`detail`：展开后的原文数值行
- `items` 已按「英雄 → 装备 → 系统」交替排序，保证首屏三类都能看到

## 手动刷新

```bash
pip install requests
python tools/refresh_patch_notes.py --out data/patch_notes.json
```

脚本会自己判断：抓不到、或解析出来的条目少于 3 条，就非零退出并**保留旧文件**，
不会把仓库写成空数据。内容没变化时不写文件，避免无意义的提交。

## 官网改版了怎么办

解析规则存了两份：客户端 `app/lol/patch_notes.py`、本仓库 `tools/refresh_patch_notes.py`。
官网一改结构，两边会**同时**失效，表现是 Actions 变红、`data/patch_notes.json` 停在旧版本。
按改动大小分两条路。

### 小改：改选择器（最常见）

典型情况是正文容器 `<div class="article">` 换了 class，或者条目从 `<h3>/<h4>` 换成了别的标签。
这种情况下不用手工整理数据，改脚本就行：

1. 改 `tools/refresh_patch_notes.py` 里的 `_extractArticle` / `_entries`
2. 本地 `python tools/refresh_patch_notes.py --out data/patch_notes.json`，能出条目就提交
3. **客户端不用动** —— 它读的是成品 JSON，不参与解析

### 大改：手工上传一份整理好的文件

整页重构成图片、或者数据挪进了接口，脚本短期写不出来，就人工整理：

1. 打开公告页，按下面的最小结构抄成 JSON
2. 直接覆盖并提交 `data/patch_notes.json`
3. 客户端下一轮检查（≤ 45 分钟）就会读到

```json
{
  "schema": 1,
  "version": "26.24",
  "date": "2026-12-02",
  "url": "https://lol.qq.com/gicp/news/410/xxxxx.html",
  "fetchedAt": 1796000000,
  "items": [
    {
      "kind": "英雄",
      "name": "安妮",
      "title": "安妮：R 提伯斯生命值 1150 → 1275",
      "detail": "提伯斯生命值：1150 - 3500 … → 1275 - 4000 …"
    }
  ]
}
```

几条硬性要求：

- `fetchedAt` 填当前时间戳（秒），客户端用它做新旧比较
- `items` 至少 3 条，少于 3 条会被判成解析失败
- `kind` 只能是 `英雄` / `装备` / `系统`
- **抓取失败的那次运行不会覆盖这个文件**（脚本失败即退出，不写盘），所以手工版是安全的
- 自动解析修好后，脚本发现版本更新会自然覆盖掉手工版

手工版生效期间 Actions 会一直是红的，那是预期现象 —— 红着说明还没修好，别去点重跑。

## 注意

- 数据全部来自官方公告页（`lol.qq.com`），不做二次加工、不改数值
- 官网版本号是 `26.x`，101 数据站是 `16.x`，两套编号不要混
- 改解析规则时，`tools/refresh_patch_notes.py` 和客户端里的
  `app/lol/patch_notes.py` 要一起改
