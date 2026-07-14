# PyMuPDF Bookmark TOC — 零 LLM 目录提取

## 解决的问题

PageIndex 在处理带嵌入式书签的 PDF（技术标准、协议文档）时，会用 LLM 逐页检测
目录、提取标题、验证页码，产生大量无效 token 消耗。

例如 USB4 规范（839 页 / 1062 条目录）：
- 原版需要 ~2000 次 LLM 调用，仅 TOC 环节花费 ~$1 token
- 即使 TOC 已正确提取，后续的 `verify_toc` 和 `check_title_appearance_in_start`
  仍对每条目录调 LLM 验证——准确率只有 ~21%

**核心痛点：** PDF 自带的嵌入式书签已经包含完整的标题 + 页码信息，
LLM 流程完全是冗余的。

## 改动内容

### 核心功能

| 改动 | 效果 |
|---|---|
| `toc_transformer_from_pdf_toc()` | 用 PyMuPDF 直接读取 PDF 书签，替换 LLM 的 `toc_transformer()` |
| `tree_parser()` 书签检测 | PDF 有书签时跳过 `check_toc()`、`find_toc_pages()` 等 LLM 检测 |
| 跳过 `verify_toc` | 书签页码是权威的，无需 LLM 逐条验证 |
| 跳过 `check_title_appearance_in_start` | 同上，节省 ~1000 次 LLM 调用 |
| `process_large_node_recursively()` | 书签路径下跳过 LLM 重提取已有结构的节点 |

### 健壮性修复

| 问题 | 修复 |
|---|---|
| PyMuPDF 页码 +1 偏移 | `get_toc()` 返回 1-based，原代码误当 0-based 多加了 1 |
| 无编号标题 structure 重复 | fallback 加 `_{count}` 唯一键，防止 `list_to_tree` 覆盖 |
| `appear_start='yes'` 导致页码逆序 | 改为 `'no'`，避免 `end_index = next_page - 1` |
| 并发取消 permit 泄漏 | `_llm_semaphore` / `_sync_llm_semaphore` 在 leaf 层限流，取消安全 |

### 可观测性

统一日志目录 `logs/`（相对路径，跟随工作目录）：

| 日志文件 | 内容 |
|---|---|
| `pageindex_progress.log` | 走了哪个分支、跳过哪些步骤 |
| `pageindex_llm.log` | LLM 请求/响应（PyMuPDF 路径下为 0 字节） |

### 适用范围

- **有书签的 PDF：** 零 LLM TOC，只保留 summary 生成（可配置关闭）
- **无书签的 PDF：** 完全回退原有 LLM 路径，行为不变

## 与 OpenKB 集成

[Leooone/OpenKB@pymupdf-toc](https://github.com/Leooone/OpenKB) 依赖本仓库，
修改了 `compiler.py` 添加 compiler 层日志，`pyproject.toml` 指向本仓库：

```bash
pip install git+https://github.com/Leooone/OpenKB.git@pymupdf-toc
# 自动拉取本仓库作为依赖
```

OpenKB 侧的日志：

| 日志文件 | 内容 |
|---|---|
| `openkb_progress.log` | compiler 每步进度 |
| `openkb_llm.log` | compiler LLM 请求/响应（含内容预览） |

## 限速配置

继承自 [PageIndex PR #343](https://github.com/VectifyAI/PageIndex/pull/343)，解决 MiniMax Token Plan 等 API 的速率限制问题。

### 环境变量

```bash
# .env
PAGEINDEX_MAX_CONCURRENCY=1   # 并发上限（默认 5）
PAGEINDEX_RPM_LIMIT=20        # 每分钟请求上限（默认 0=不限）
```

| 参数 | 作用 | 建议值 |
|---|---|---|
| `PAGEINDEX_MAX_CONCURRENCY` | 同时飞行中的 LLM 调用数 | 1–5 |
| `PAGEINDEX_RPM_LIMIT` | 滑动窗口限速（60s 窗口） | 20（MiniMax Token Plan） |

### 新增组件

- **`SlidingWindowRateLimiter`**（`index/utils.py`）— 60 秒滑动窗口，强制最小请求间隔（60/RPM 秒）
- **`_extract_retry_delay()`**（`index/utils.py`）— 429 时解析 Retry-After 响应头，无头时指数退避+抖动
- **`_is_rate_limit_error()`**（`index/utils.py`）— 自动检测 HTTP 429 错误
- **`rpm_limit_scope()`**（`config.py`）— 与 `max_concurrency_scope()` 同机制的作用域管理

### 工作原理

```
RPM=20, 并发=5:

请求1: limiter.wait() → 立即放行 → 抢槽位 → API调用
请求2: limiter.wait() → 等3s → 放行 → 抢槽位 → API调用
请求3: limiter.wait() → 等6s → 放行 → ...
...
limiter 在信号量之前拦截，保证发射频率 ≤ RPM
即使并发设很高，实际飞行中的请求 ≈ RPM × (API耗时/60)
```
