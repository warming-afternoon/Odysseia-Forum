# 内存泄漏分析报告

## 诊断时间线

| 时间 | RSS | 关键发现 |
|------|-----|---------|
| 重启后 2 min | 326 MB (API) + 247 MB (Bot) | jemalloc 生效，基线正常 |
| ~3 h | 544 MB + 387 MB | 开始爬升 |
| ~5 h | 617 MB | 用 clear-caches 回收 60K 对象，RSS 纹丝不动 |
| ~12 h | 835 MB + 570 MB | 持续线性增长 |

## 定量证据

### 对象分布 (RSS ~835 MB 时)

```
实测对象大小 (sys.getsizeof): 299 MB
RSS:                          836 MB
缺口 (碎片/C扩展内存):        537 MB
```

| 类型 | 数量 | 浅层大小 | 备注 |
|------|------|---------|------|
| dict | 204K | 145 MB | 最大单类消费者 |
| tuple | 604K | 40 MB | SQLAlchemy 内部元组 |
| frozenset | 132K | 28 MB | SQL 编译中间产物 |
| set | 131K | 28 MB | SQL 编译中间产物 |
| list | 140K | 21 MB | |
| `_anonymous_label` | 127K | 14 MB | SQL 列别名 |
| `BindParameter` | 127K | 6 MB | SQL 参数占位符 |
| `BinaryExpression` | 63K | 3 MB | SQL WHERE AST |
| `Comparator` | 63K | 3 MB | SQL 列比较 AST |
| `BigInteger` | 62K | 3 MB | SQL 类型对象 |

### Dict 持有者追踪 (200 采样, gc.get_referrers)

| 引用链 | 样本数 | 占比 |
|--------|--------|------|
| `list → list → dict[~15]` | 140 | **70%** |
| `list_iterator → list → dict[~15]` | 20 | 10% |
| `dict → BindParameter → dict[~15]` | 6 | 3% |
| `set → BindParameter → dict[~15]` | 5 | 3% |
| `Comparator → BindParameter → dict[~15]` | 4 | 2% |

### 清除缓存实验

```
端点: GET /v1/debug/memory/clear-caches
操作: 清除 SQLAlchemy 编译缓存 + gc.collect(2)

结果:
  SQLAlchemy 对象: 18,408 → 2,670 (回收 85%)
  总对象:         82,325 → 22,341 (回收 73%)
  RSS:           617 MB → 619 MB (↓ 0 MB)
```

对象可以被回收，但内存页不归还操作系统 — 碎片化。

### GC 统计

```
Gen 0: 55,525 次回收, 已回收 1,989,716 对象 — GC 一直在拼命工作
Gen 1:  2,647 次回收, 已回收   407,549 对象
Gen 2:     92 次回收, 已回收    68,057 对象
```

## 根因

### 泄漏链路

```
expire_on_commit=False (src/shared/database.py:62)
          │
          ▼
  SQLAlchemy session commit 后保留 identity map
          │
          ▼
  每次搜索查询 → 编译 SQL 语句:
    · BindParameter ×N     (127K 累积)
    · _anonymous_label ×N  (127K 累积)
    · BinaryExpression ×N  (63K 累积)
    · Comparator ×N        (63K 累积)
    · dict[~15] ×N         (97K 累积, 每行结果的键值映射)
          │
          ▼
  查询完成 → session 关闭 → 但 identity map 对象
  被 list → list 引用链持有 → 非纯循环引用 → GC 无法回收
          │
          ▼
  12 小时 × 40K 请求 → 百万级 SQL 编译对象累积 → RSS 线性爬升
```

### 为什么 clear-caches 回收了对象但 RSS 不降

1. Python 释放对象 → 内存还给 pymalloc arena
2. pymalloc 持有 arena（不归还 OS）
3. jemalloc 也持有释放的内存页（取决于 decay 配置）
4. 频繁分配/释放导致的碎片化让整个页无法归还

### 为什么不是简单循环引用

GC 统计显示 Gen 0 已回收 198 万对象、Gen 2 仅回收 68K — 说明对象在 Gen 0 就被回收了（正常生命周期管理），但核心泄漏对象通过外部可达路径（list → list chain）躲过了回收。

## 影响

| 维度 | 现状 |
|------|------|
| 内存 | 5h 从 326 MB → 545 MB，线性爬升，24h 预计 >800 MB，总和 >1.4 GB |
| 性能 | GC Gen 0 55K 次、回收 198 万对象，CPU 持续消耗 |
| 稳定性 | VPS 2.5 GB RAM，持续增长则 OOM 风险 |
| Bot 进程 | 同样涨到 570 MB，两进程合计 >1.1 GB |

## 修复方案

### 核心修复

`src/shared/database.py:62` — `expire_on_commit=False` → `expire_on_commit=True`

### 伴随修复

session 关闭后访问 ORM 属性会导致 `DetachedInstanceError`。

进行相应修复