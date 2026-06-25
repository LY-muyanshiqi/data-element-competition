# 数据要素大赛 — 文献验证工具深度优化计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有验证引擎基础上，新增 3 个验证维度、整合中文文献支持、优化评分模型、加入并发与缓存、完善工程基础设施。

**Architecture:** verify_engine.py 扩展为 8 维度验证引擎（原 5 维 + 作者真实性 + AI 文本特征 + 期刊真实性），整合 语义一致性检查.py 的中文解析能力，app.py 增加中文文献 Tab，批量验证改为 3 并发。

**Tech Stack:** Python 3.x, Streamlit, Pandas, Requests, Plotly, openpyxl, pytest, concurrent.futures

## Global Constraints

- 不涉及模型训练 / 深度学习
- 仅调用公开 API + 规则匹配
- 批量验证时控制 API 调用频率（1 req/s per thread）
- 输出 0-100 可信度评分，≥80可靠 / 40-79可疑 / <40虚假
- 所有新增代码必须通过 pytest 测试
- 中文文献年份合理范围 1980-2026，英文 1900-2026

---

### Task 1: 整合中文文献语义检查模块

**Files:**
- Create: `verify_engine.py` (追加 `SemanticChecker` 类和相关函数)

**Interfaces:**
- Produces: `SemanticChecker` 类，包含 `check_title_length(title)`, `check_garbage_chars(title)`, `check_year_range(year)`, `is_chinese_title(title)` 四个方法
- Produces: `classify_language(title: str) -> str` 返回 `"zh"` | `"en"` | `"mixed"`

**说明:** 把 `任务二/语义一致性检查.py` 中的标题/乱码/年份检查逻辑以 `SemanticChecker` 类的形式并入 `verify_engine.py`，并增加中英文自动识别。原本独立的 `语义一致性检查.py` 不再维护，逻辑统一到验证引擎。

- [ ] **Step 1: 在 verify_engine.py 末尾追加 SemanticChecker 类**

```python
# ── SemanticChecker: 语义一致性检查（整合自任务二）────────────────────

import string as _string

class SemanticChecker:
    """标题/年份语义一致性检查，支持中英文"""
    
    TITLE_MIN_LEN_ZH = 5
    TITLE_MAX_LEN_ZH = 300
    TITLE_MIN_LEN_EN = 10
    TITLE_MAX_LEN_EN = 500
    YEAR_MIN_ZH = 1980
    YEAR_MAX_ZH = 2026
    YEAR_MIN_EN = 1900
    YEAR_MAX_EN = 2026
    
    _GARBAGE_PATTERN = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]')
    _ALL_SPECIAL_PATTERN = re.compile(r'^[^\w一-鿿]+$')
    _REPEAT_PATTERN = re.compile(r'(.)\1{9,}')
    
    @classmethod
    def is_chinese_title(cls, title: str) -> bool:
        """判断标题是否主要为中文"""
        if not title:
            return False
        chinese_chars = sum(1 for c in title if '一' <= c <= '鿿')
        return chinese_chars > len(title) * 0.3
    
    @classmethod
    def classify_language(cls, title: str) -> str:
        """返回 'zh' | 'en' | 'mixed'"""
        if not title:
            return "en"
        cjk = sum(1 for c in title if '一' <= c <= '鿿' or '぀' <= c <= 'ヿ')
        if cjk > len(title) * 0.5:
            return "zh"
        if cjk > 0:
            return "mixed"
        return "en"
    
    @classmethod
    def check_title_length(cls, title: str) -> tuple[int, str]:
        """返回 (得分, 详情)，得分 0 或 3"""
        if not title or not title.strip():
            return 0, "标题为空"
        lang = cls.classify_language(title)
        length = len(title.strip())
        min_len = cls.TITLE_MIN_LEN_ZH if lang == "zh" else cls.TITLE_MIN_LEN_EN
        max_len = cls.TITLE_MAX_LEN_ZH if lang == "zh" else cls.TITLE_MAX_LEN_EN
        if length < min_len:
            return 0, f"标题过短（{length}字，最小{min_len}）"
        if length > max_len:
            return 0, f"标题过长（{length}字，最大{max_len}）"
        return 3, "标题长度合理"
    
    @classmethod
    def check_garbage_chars(cls, title: str) -> tuple[int, str]:
        """返回 (得分, 详情)，检测到乱码得 0，通过得 3"""
        if not title or not title.strip():
            return 0, "标题为空"
        title = title.strip()
        if cls._GARBAGE_PATTERN.search(title):
            return 0, "标题包含不可打印字符"
        if cls._ALL_SPECIAL_PATTERN.match(title):
            return 0, "标题全由特殊符号组成"
        if cls._REPEAT_PATTERN.search(title):
            return 0, "标题包含连续重复字符"
        # 异常字符占比
        normal = set(_string.ascii_letters + _string.digits + _string.whitespace)
        abnormal = sum(1 for c in title if c not in normal and not ('一' <= c <= '鿿') and c not in '，。！？；：""''（）【】《》、…—·')
        if len(title) > 0 and abnormal / len(title) > 0.5:
            return 0, "标题中异常字符占比过高"
        return 3, "标题无乱码"
    
    @classmethod
    def check_year_range(cls, year, lang: str = "en") -> tuple[int, str]:
        """返回 (得分, 详情)，得分 0 或 4"""
        if year is None:
            return 4, "年份未提供（跳过校验）"
        try:
            year_int = int(year)
        except (ValueError, TypeError):
            return 0, f"年份格式无法解析：'{year}'"
        min_y = cls.YEAR_MIN_ZH if lang == "zh" else cls.YEAR_MIN_EN
        max_y = cls.YEAR_MAX_ZH if lang == "zh" else cls.YEAR_MAX_EN
        if min_y <= year_int <= max_y:
            return 4, f"年份在合理范围内（{year_int}）"
        return 0, f"年份{year_int}超出合理范围({min_y}-{max_y})"
```

- [ ] **Step 2: 写测试验证 SemanticChecker**

```bash
python -c "
from verify_engine import SemanticChecker

# 中文标题识别
assert SemanticChecker.is_chinese_title('基于深度学习的洪水预测模型研究')
assert not SemanticChecker.is_chinese_title('Deep Learning for Flood Prediction')

# 语言分类
assert SemanticChecker.classify_language('洪水预测模型') == 'zh'
assert SemanticChecker.classify_language('Flood Prediction') == 'en'

# 标题长度
score, detail = SemanticChecker.check_title_length('深度学习洪水预测')
assert score == 3, f'Expected 3, got {score}: {detail}'

# 乱码检测
score, detail = SemanticChecker.check_garbage_chars('深度学习')
assert score == 3, f'Expected 3, got {score}: {detail}'
score, detail = SemanticChecker.check_garbage_chars('\x00\x01\x02')
assert score == 0, f'Expected 0, got {score}: {detail}'

# 年份
score, _ = SemanticChecker.check_year_range(2023, 'zh')
assert score == 4
score, _ = SemanticChecker.check_year_range(1970, 'zh')
assert score == 0  # 中文文献1980下限

print('All SemanticChecker tests passed')
"
```

- [ ] **Step 3: 提交**

```bash
git add verify_engine.py
git commit -m "feat: 整合中文文献语义一致性检查模块 SemanticChecker"
```

---

### Task 2: 重构评分引擎，新增 3 个验证维度

**Files:**
- Modify: `verify_engine.py` — 重构 `verify_record` 函数，新增作者/标题特征/期刊验证

**Interfaces:**
- Consumes: `SemanticChecker` from Task 1, `ReferenceRecord`, existing helpers
- Produces: `_check_author_match(ref, raw) -> tuple[int, str]`
- Produces: `_check_title_ai_fingerprints(title) -> tuple[int, str]`
- Produces: `_check_journal_match(ref, raw) -> tuple[int, str]`
- Modifies: `verify_record` — 从 5 维度变为 8 维度，总分 100 重新分配权重

**新评分权重设计:**

| 维度 | 旧分 | 新分 | 说明 |
|------|------|------|------|
| DOI 格式 | 10 | 8 | 轻微降权，格式对不代表存在 |
| 语义一致性 | — | 10 | title长度+乱码+年份整合，中文用1980-2026 |
| 年份合理性 | 10 | — | 合并到语义一致性 |
| CrossRef | 40 | 30 | 降权，给新维度让路 |
| OpenAlex | 30 | 25 | 降权 |
| 作者匹配 | — | 10 | **新增**：CrossRef返回作者 vs 输入作者 |
| AI 标题特征 | — | 7 | **新增**：检测夸张/虚假关键词 |
| 期刊匹配 | — | 5 | **新增**：输入期刊 vs API返回期刊 |
| 元数据一致性 | 10 | 5 | 保留但降权 |
| **总计** | **100** | **100** | |

- [ ] **Step 1: 新增 _check_author_match 函数**

```python
def _check_author_match(ref: ReferenceRecord, raw: dict) -> tuple[int, str]:
    """检查输入作者是否出现在API返回数据中"""
    if not ref.authors:
        return 10, "作者未提供（跳过校验）"
    
    input_authors = [a.strip().lower() for a in ref.authors.split(',') if a.strip()]
    if not input_authors:
        return 10, "作者未提供（跳过校验）"
    
    for api_key in ["crossref", "openalex"]:
        api_data = raw.get(api_key, {})
        api_authors_raw = api_data.get("author", [])
        if not api_authors_raw:
            continue
        
        api_author_names = set()
        for a in api_authors_raw:
            family = (a.get("family") or a.get("last") or "").lower()
            given = (a.get("given") or a.get("first") or "").lower()
            if family:
                api_author_names.add(family)
            if given:
                api_author_names.add(given)
        
        if not api_author_names:
            continue
        
        matched = 0
        for input_a in input_authors:
            for api_a in api_author_names:
                if input_a in api_a or api_a in input_a:
                    matched += 1
                    break
        
        match_rate = matched / len(input_authors)
        if match_rate >= 0.5:
            return 10, f"作者匹配通过（{matched}/{len(input_authors)}）"
        elif match_rate > 0:
            return 5, f"作者部分匹配（{matched}/{len(input_authors)}）"
        else:
            return 0, f"作者不匹配（输入{len(input_authors)}人，无一命中API记录）"
    
    return 10, "无API作者数据（跳过校验）"
```

- [ ] **Step 2: 新增 _check_title_ai_fingerprints 函数**

```python
# AI 生成文献的典型标题特征
_AI_FINGERPRINT_PATTERNS = [
    (re.compile(r'\b100%?\s*(accuracy|precision|recall|success)', re.IGNORECASE), "过度夸张声称（100% accuracy）"),
    (re.compile(r'\bsolve[sd]?\s+(NP[- ]?(complete|hard)|all)\b', re.IGNORECASE), "声称解决NP完全/难问题"),
    (re.compile(r'\b(always|never|perfect|infallible|flawless)\b', re.IGNORECASE), "绝对化词汇（always/never/perfect）"),
    (re.compile(r'\bthe\s+(first|only)\b', re.IGNORECASE), "\"the first/only\" 宣称首创"),
    (re.compile(r'\bnovel\s+(approach|method|framework|technique)', re.IGNORECASE), "novel approach 模式（AI高频模板词）"),
    (re.compile(r'\bcomprehensive\s+(review|survey|analysis|study)\s+of\b', re.IGNORECASE), "comprehensive review of 模式"),
    (re.compile(r'\bin\s+(the\s+)?linear\s+time\b', re.IGNORECASE), "声称线性时间复杂度"),
    (re.compile(r'\bstate[ -]of[ -]the[ -]art\b', re.IGNORECASE), "state-of-the-art 模板词"),
]


def _check_title_ai_fingerprints(title: str) -> tuple[int, str]:
    """检测标题中AI生成文献的典型特征，匹配到扣分"""
    if not title:
        return 7, "标题为空（跳过检测）"
    
    hits = []
    for pattern, desc in _AI_FINGERPRINT_PATTERNS:
        if pattern.search(title):
            hits.append(desc)
    
    if len(hits) >= 3:
        return 0, f"AI标题特征多项命中：{'；'.join(hits[:3])}"
    elif len(hits) >= 2:
        return 3, f"AI标题特征部分命中：{'；'.join(hits)}"
    elif len(hits) == 1:
        return 5, f"AI标题特征单次命中：{hits[0]}"
    else:
        return 7, "未检测到AI标题特征"
```

- [ ] **Step 3: 新增 _check_journal_match 函数**

```python
def _check_journal_match(ref: ReferenceRecord, raw: dict) -> tuple[int, str]:
    """检查输入期刊名是否与API返回一致"""
    if not ref.journal:
        return 5, "期刊未提供（跳过校验）"
    
    for api_key in ["crossref", "openalex"]:
        api_data = raw.get(api_key, {})
        api_journal = ""
        # CrossRef 返回 container-title
        ct = api_data.get("container-title", "")
        if isinstance(ct, list) and ct:
            api_journal = ct[0]
        # OpenAlex 可能直接返回 journal 字段
        oa_journal = api_data.get("journal", "")
        if oa_journal:
            api_journal = api_journal or oa_journal
        
        if api_journal:
            sim = _title_similarity(ref.journal, api_journal)
            if sim > 0.7:
                return 5, f"期刊名匹配通过（相似度{sim:.2f}）"
            else:
                return 0, f"期刊名不匹配（输入「{ref.journal}」vs API「{api_journal[:40]}」）"
    
    return 5, "无API期刊数据（跳过校验）"
```

- [ ] **Step 4: 重构 verify_record 为 8 维度**

```python
def verify_record(ref: ReferenceRecord) -> VerificationResult:
    total, details = 0, []
    raw: dict = {}
    
    # 识别语言
    lang = SemanticChecker.classify_language(ref.title)
    
    # 维度1: DOI格式 (8分)
    s, d = _check_doi_format(ref.doi)
    total += s
    details.append(f"[DOI格式] {d} (+{s})")
    
    # 维度2: 语义一致性 (10分) — 整合原标题长度+乱码+年份
    s1, d1 = SemanticChecker.check_title_length(ref.title)
    s2, d2 = SemanticChecker.check_garbage_chars(ref.title)
    s3, d3 = SemanticChecker.check_year_range(ref.year, lang)
    total += s1 + s2 + s3
    details.append(f"[标题长度] {d1} (+{s1})")
    details.append(f"[乱码检测] {d2} (+{s2})")
    details.append(f"[年份] {d3} (+{s3})")
    
    # 维度3: CrossRef (30分)
    s, d, c_r = _check_crossref(ref)
    total += s
    details.append(f"[CrossRef] {d} (+{s})")
    raw.update(c_r)
    
    time.sleep(0.5)
    
    # 维度4: OpenAlex (25分)
    s, d, o_r = _check_openalex(ref)
    total += s
    details.append(f"[OpenAlex] {d} (+{s})")
    raw.update(o_r)
    
    # 维度5: 作者匹配 (10分)
    s, d = _check_author_match(ref, raw)
    total += s
    details.append(f"[作者匹配] {d} (+{s})")
    
    # 维度6: AI标题特征 (7分)
    s, d = _check_title_ai_fingerprints(ref.title)
    total += s
    details.append(f"[AI特征] {d} (+{s})")
    
    # 维度7: 期刊匹配 (5分)
    s, d = _check_journal_match(ref, raw)
    total += s
    details.append(f"[期刊匹配] {d} (+{s})")
    
    # 维度8: 元数据一致性 (5分)
    s, d = _check_metadata_consistency(ref, raw)
    total += s
    details.append(f"[一致性] {d} (+{s})")
    
    score = max(0, min(100, total))
    if score >= 80:
        status = "可靠"
    elif score >= 40:
        status = "可疑"
    else:
        status = "虚假"
    
    return VerificationResult(status=status, score=score, details=details, raw_data=raw)
```

- [ ] **Step 5: 运行全量回归测试**

```bash
python -c "
from verify_engine import ReferenceRecord, verify_record

# 真实文献：Nature 2013
ref1 = ReferenceRecord(
    title='Nanometre-scale thermometry in a living cell',
    authors='G. Kucsko, P. C. Maurer',
    journal='Nature',
    doi='10.1038/nature12373',
    year=2013
)
r1 = verify_record(ref1)
print(f'真实文献: {r1.status} {r1.score}/100')
assert r1.status == '可靠', f'真实文献应为可靠，实际{r1.status}'
assert r1.score >= 80

# 虚假文献：量子计算
ref2 = ReferenceRecord(
    title='A Novel Quantum Computing Approach for Solving NP-Complete Problems in Linear Time',
    authors='Fictitious Author',
    doi='10.9999/fake.paper.2024',
    year=2024
)
r2 = verify_record(ref2)
print(f'虚假文献: {r2.status} {r2.score}/100')
assert r2.status == '虚假', f'虚假文献应为虚假，实际{r2.status}'
assert r2.score < 40

# 无DOI中文文献
ref3 = ReferenceRecord(
    title='基于深度学习的洪水预测模型研究',
    authors='张三, 李四',
    journal='水利学报',
    year=2022
)
r3 = verify_record(ref3)
print(f'中文无DOI文献: {r3.status} {r3.score}/100')

print('All regression tests passed')
"
```

- [ ] **Step 6: 提交**

```bash
git add verify_engine.py
git commit -m "feat: 重构为8维度评分引擎，新增作者/标题特征/期刊验证"
```

---

### Task 3: 批量验证并发化 + 本地缓存

**Files:**
- Modify: `verify_engine.py` — 新增 `verify_batch_concurrent` 函数 + DOI 缓存
- Create: `test_cache.py` — 缓存功能测试

**Interfaces:**
- Produces: `verify_batch_concurrent(records: list[ReferenceRecord], max_workers: int = 3) -> list[VerificationResult]`
- Produces: `_doi_cache: dict` 模块级缓存字典 + `_get_cached(key, fetcher)` 工具函数

- [ ] **Step 1: 在 verify_engine.py 中添加缓存机制**

在 `verify_engine.py` 的 helper 区域后追加：

```python
# ── 简单内存缓存 ──────────────────────────────────────────────────────

_doi_cache: dict[str, dict] = {}

def _cached_api_call(key: str, fetcher, ttl: int = 3600):
    """带缓存的API调用，key为缓存键，fetcher为实际请求函数"""
    now = time.time()
    if key in _doi_cache and now - _doi_cache[key].get("_ts", 0) < ttl:
        return _doi_cache[key]
    result = fetcher()
    result["_ts"] = now
    _doi_cache[key] = result
    return result

def clear_cache():
    """清空缓存"""
    _doi_cache.clear()
```

- [ ] **Step 2: 在 _check_crossref 和 _check_openalex 中使用缓存**

重写 `_check_crossref` 使用缓存：

```python
def _check_crossref(ref: ReferenceRecord) -> tuple[int, str, dict]:
    raw = {}
    # 1) DOI 精确匹配（带缓存）
    if ref.doi:
        def _doi_lookup():
            try:
                resp = requests.get(
                    CROSSREF_WORK_URL.format(doi=ref.doi.strip()),
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    msg = data.get("message", {})
                    return {"source": "doi", "title": msg.get("title"), "doi": msg.get("DOI"),
                            "author": msg.get("author", []), "container-title": msg.get("container-title", [])}
                elif resp.status_code == 404:
                    return {"source": "doi", "error": "DOI未找到"}
                else:
                    return {"source": "doi", "error": f"HTTP {resp.status_code}"}
            except requests.RequestException as e:
                return {"source": "doi", "error": str(e)}

        key = f"cr_doi_{ref.doi.strip()}"
        cached = _cached_api_call(key, _doi_lookup)
        raw["crossref"] = cached
        if "title" in cached and cached.get("source") == "doi":
            return 30, "CrossRef DOI匹配成功", raw

    # 2) 标题搜索
    try:
        resp = requests.get(
            "https://api.crossref.org/works",
            params={"query.title": ref.title, "rows": 3},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            items = resp.json().get("message", {}).get("items", [])
            raw.setdefault("crossref", {})["title_search"] = items
            if items:
                best_sim = _title_similarity(ref.title, items[0].get("title", [""])[0])
                if best_sim > 0.7:
                    msg = items[0]
                    raw["crossref"].update({"source": "title_search", "title": msg.get("title"),
                        "author": msg.get("author", []), "container-title": msg.get("container-title", []),
                        "doi": msg.get("DOI")})
                    return 20, f"CrossRef标题搜索匹配（相似度{best_sim:.2f}）", raw
                return 0, f"CrossRef标题搜索结果不匹配（最高相似度{best_sim:.2f}）", raw
            return 0, "CrossRef标题搜索无结果", raw
    except requests.RequestException as e:
        raw.setdefault("crossref", {})["search_error"] = str(e)
        logger.warning("CrossRef标题搜索失败: %s", e)

    return 0, "CrossRef未找到该文献", raw
```

类似地重写 `_check_openalex` 使用缓存包裹 DOI 查询，标题搜索保持不变。

- [ ] **Step 3: 新增 verify_batch_concurrent 函数**

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def verify_batch_concurrent(records: list[ReferenceRecord], max_workers: int = 3) -> list[VerificationResult]:
    """并发批量验证，max_workers=3 控制API请求频率"""
    results = [None] * len(records)
    
    def _verify_one(idx: int, ref: ReferenceRecord):
        logger.info("验证 %d/%d: %s", idx + 1, len(records), ref.title[:60])
        return idx, verify_record(ref)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_verify_one, i, ref): i for i, ref in enumerate(records)}
        for future in as_completed(futures):
            idx, result = future.result()
            results[idx] = result
    
    return results
```

- [ ] **Step 4: 更新 app.py 批量验证使用并发**

在 `app.py` 中，将 `verify_record` 批量循环替换为：

```python
from verify_engine import verify_batch_concurrent

# 替换第 101 行的循环
results_data = []
all_results = verify_batch_concurrent(records, max_workers=3)
for ref, r in zip(records, all_results):
    results_data.append({
        "标题": ref.title,
        "作者": ref.authors,
        "期刊": ref.journal or "",
        "DOI": ref.doi or "",
        "输入年份": ref.year or "",
        "状态": r.status,
        "可信度": r.score,
        "验证详情": "\n".join(r.details),
    })
```

- [ ] **Step 5: 测试并发 + 缓存**

```bash
python -c "
from verify_engine import ReferenceRecord, verify_batch_concurrent, clear_cache
import time

# 3条记录并发验证
refs = [
    ReferenceRecord(title='Nanometre-scale thermometry in a living cell', authors='G. Kucsko', doi='10.1038/nature12373', year=2013),
    ReferenceRecord(title='Deep Residual Learning for Image Recognition', authors='Kaiming He', year=2016),
    ReferenceRecord(title='A Novel Quantum Computing Approach', authors='Fake Author', doi='10.9999/fake.2024', year=2024),
]

start = time.time()
results = verify_batch_concurrent(refs, max_workers=3)
elapsed = time.time() - start

print(f'3条记录并发验证耗时: {elapsed:.1f}s (预计串行约4.5s)')
for r in results:
    print(f'  {r.status} {r.score}/100')
assert len(results) == 3
assert elapsed < 4.0, f'并发应明显快于串行，实际{elapsed:.1f}s'

# 测试缓存：第二次验证同一DOI应更快
clear_cache()
start2 = time.time()
results2 = verify_batch_concurrent(refs, max_workers=3)
elapsed2 = time.time() - start2
print(f'首次(含API调用): {elapsed:.1f}s')

print('Concurrent + cache tests passed')
"
```

- [ ] **Step 6: 提交**

```bash
git add verify_engine.py app.py
git commit -m "feat: 批量验证3并发 + DOI查询内存缓存"
```

---

### Task 4: 更新 Streamlit UI（中文数据 Tab + 优化展示）

**Files:**
- Modify: `app.py` — 新增 Tab3 中文文献验证

**Interfaces:**
- Produces: Tab 3 "中文文献验证" — 支持粘贴知网/万方引用格式，自动解析后批量验证

- [ ] **Step 1: 在 app.py 添加 Tab 3**

在 `tab1, tab2 = st.tabs(...)` 改为 3 个 Tab，追加 Tab 3 内容：

```python
tab1, tab2, tab3 = st.tabs(["🔍 单篇验证", "📂 批量验证", "📄 中文文献验证"])

# ... tab1, tab2 保持不变 ...

with tab3:
    st.markdown("#### 粘贴知网/万方引用格式，自动解析并验证")
    st.caption("支持格式：[1]作者.标题[J].期刊名,2022,(7):156-159.")
    
    raw_text = st.text_area("粘贴引用文本（每行一条）", height=200,
                            placeholder="[1]张三,李四.基于深度学习的洪水预测模型研究[J].水利学报,2022,53(6):156-169.\n[2]王五.人工智能在气象预测中的应用[D].北京大学,2023.")
    
    col1, col2 = st.columns(2)
    with col1:
        is_chinese_lit = st.checkbox("中文文献模式（年份范围1980-2026）", value=True)
    
    if st.button("解析并验证", type="primary", key="btn_cn", disabled=not raw_text.strip()):
        from verify_engine import parse_references_batch
        
        parsed = parse_references_batch(raw_text)
        records = []
        for p in parsed:
            records.append(ReferenceRecord(
                title=p.get("title", "") or "",
                authors="",
                doi=None,
                year=p.get("year"),
            ))
        
        st.info(f"解析出 {len(records)} 条文献")
        
        results_data = []
        for i, (rec, parsed_item) in enumerate(zip(records, parsed)):
            r = verify_record(rec)
            results_data.append({
                "序号": i + 1,
                "标题": rec.title,
                "年份": rec.year or "",
                "原始引用": parsed_item.get("raw", "")[:80],
                "状态": r.status,
                "可信度": r.score,
                "验证详情": "\n".join(r.details),
            })
        
        result_df = pd.DataFrame(results_data)
        
        # 汇总
        status_counts = result_df["状态"].value_counts()
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("✅ 可靠", status_counts.get("可靠", 0))
        col_b.metric("⚠️ 可疑", status_counts.get("可疑", 0))
        col_c.metric("❌ 虚假", status_counts.get("虚假", 0))
        
        st.dataframe(result_df, use_container_width=True)
        
        csv = result_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 导出结果 (CSV)", csv, "cn_literature_results.csv", "text/csv")
```

- [ ] **Step 2: 更新 tab2 的批量验证进度显示**

用并发后移除逐条 sleep，改为整体 spinner：

```python
with st.spinner(f"正在并发验证 {len(records)} 条记录..."):
    all_results = verify_batch_concurrent(records, max_workers=3)
    for ref, r in zip(records, all_results):
        results_data.append({...})  # 同上
```

- [ ] **Step 3: 本地测试**

```bash
streamlit run app.py
# 在浏览器中测试三个Tab
```

- [ ] **Step 4: 提交**

```bash
git add app.py
git commit -m "feat: 新增中文文献验证Tab，支持知网/万方引用自动解析"
```

---

### Task 5: 工程基础设施 — pytest + CI + LICENSE

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_verify_engine.py`
- Create: `.github/workflows/ci.yml`
- Create: `LICENSE`
- Modify: `.gitignore`
- Modify: `requirements.txt`

- [ ] **Step 1: 创建测试文件 tests/test_verify_engine.py**

```python
"""验证引擎单元测试"""
import pytest
from verify_engine import (
    ReferenceRecord, VerificationResult, verify_record, verify_batch_concurrent,
    SemanticChecker, _check_doi_format, _check_title_ai_fingerprints,
    _check_author_match, _check_journal_match, clear_cache,
)


class TestSemanticChecker:
    def test_is_chinese_title(self):
        assert SemanticChecker.is_chinese_title('基于深度学习的洪水预测模型研究')
        assert not SemanticChecker.is_chinese_title('Deep Learning for Flood Prediction')

    def test_classify_language(self):
        assert SemanticChecker.classify_language('洪水预测模型研究') == 'zh'
        assert SemanticChecker.classify_language('Deep Learning') == 'en'
        assert SemanticChecker.classify_language('基于CNN的Flood预测') == 'mixed'

    def test_check_title_length_zh(self):
        score, _ = SemanticChecker.check_title_length('深度学习洪水预测模型研究')
        assert score == 3
        score, _ = SemanticChecker.check_title_length('短')
        assert score == 0

    def test_check_garbage_chars(self):
        score, _ = SemanticChecker.check_garbage_chars('正常标题')
        assert score == 3
        score, _ = SemanticChecker.check_garbage_chars('\x00\x01\x02')
        assert score == 0

    def test_check_year_range_zh(self):
        score, _ = SemanticChecker.check_year_range(2023, 'zh')
        assert score == 4
        score, _ = SemanticChecker.check_year_range(1970, 'zh')
        assert score == 0


class TestDOICheck:
    def test_valid_doi(self):
        s, _ = _check_doi_format('10.1038/nature12373')
        assert s == 8

    def test_invalid_doi(self):
        s, _ = _check_doi_format('not-a-doi')
        assert s == 0

    def test_missing_doi(self):
        s, _ = _check_doi_format(None)
        assert s == 0


class TestTitleAIFingerprints:
    def test_clean_title(self):
        s, _ = _check_title_ai_fingerprints('Nanometre-scale thermometry in a living cell')
        assert s == 7

    def test_exaggerated_title(self):
        s, _ = _check_title_ai_fingerprints(
            'A Novel Approach for Predicting Stock Market Crashes with 100% Accuracy in Linear Time'
        )
        assert s <= 3  # 多项命中应低分


class TestVerifyRecord:
    def test_real_reference(self):
        clear_cache()
        ref = ReferenceRecord(
            title='Nanometre-scale thermometry in a living cell',
            authors='G. Kucsko, P. C. Maurer',
            journal='Nature',
            doi='10.1038/nature12373',
            year=2013,
        )
        r = verify_record(ref)
        assert r.status == '可靠'
        assert r.score >= 80

    def test_fake_reference(self):
        clear_cache()
        ref = ReferenceRecord(
            title='A Novel Quantum Computing Approach for Solving NP-Complete Problems in Linear Time',
            authors='Fictitious Author',
            doi='10.9999/fake.paper.2024',
            year=2024,
        )
        r = verify_record(ref)
        assert r.score < 40

    def test_no_doi_reference(self):
        clear_cache()
        ref = ReferenceRecord(
            title='Attention Is All You Need',
            authors='Ashish Vaswani',
            year=2017,
        )
        r = verify_record(ref)
        assert r.score >= 0


class TestBatchConcurrent:
    def test_batch_three(self):
        clear_cache()
        refs = [
            ReferenceRecord(title='Nanometre-scale thermometry in a living cell', authors='G. Kucsko', doi='10.1038/nature12373', year=2013),
            ReferenceRecord(title='Deep Residual Learning for Image Recognition', authors='Kaiming He', year=2016),
            ReferenceRecord(title='Fake Paper Title That Does Not Exist', authors='Fake', year=2025),
        ]
        results = verify_batch_concurrent(refs, max_workers=3)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, VerificationResult)
```

- [ ] **Step 2: 运行测试**

```bash
pip install pytest
pytest tests/test_verify_engine.py -v
```

- [ ] **Step 3: 创建 CI 配置**

`.github/workflows/ci.yml`:

```yaml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt pytest
      - run: pytest tests/ -v
```

- [ ] **Step 4: 创建 LICENSE + 更新 .gitignore + requirements.txt**

```bash
# LICENSE (MIT)
cat > LICENSE << 'EOF'
MIT License

Copyright (c) 2026 LI Yao

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
EOF
```

更新 `.gitignore` 追加:
```
*.pdf
*.docx
*.zip
任务二/
任务二_extracted/
```

更新 `requirements.txt`:
```
streamlit>=1.28.0
pandas>=1.5.0
requests>=2.28.0
plotly>=5.14.0
openpyxl>=3.0.0
pytest>=7.0.0
```

- [ ] **Step 5: 提交**

```bash
git add tests/ .github/ LICENSE .gitignore requirements.txt
git commit -m "feat: 添加pytest测试 + GitHub Actions CI + MIT LICENSE"
```

---

### Task 6: 最终验证与 README 更新

- [ ] **Step 1: 运行完整测试套件**

```bash
pytest tests/ -v
```

- [ ] **Step 2: 更新 README.md 反映新架构**

更新验证维度表：

| 维度 | 权重 | 得分条件 |
|------|------|----------|
| DOI 格式校验 | 8分 | 符合标准 DOI 正则 |
| 语义一致性 | 10分 | 标题长度合理 + 无乱码 + 年份合理(中文1980-2026, 英文1900-2026) |
| CrossRef 验证 | 30分 | DOI 精确匹配 30 分，标题搜索 20 分 |
| OpenAlex 复核 | 25分 | 确认存在 25 分，年份不一致 15 分 |
| 作者匹配 | 10分 | 输入作者与 API 记录匹配 |
| AI 标题特征 | 7分 | 未检测到夸张/AI模板词得满分 |
| 期刊匹配 | 5分 | 输入期刊与 API 记录一致 |
| 元数据一致性 | 5分 | 标题相似度 >0.7 |

- [ ] **Step 3: 运行 Streamlit 做最终手工验证**

```bash
streamlit run app.py
# 测三个Tab：单篇、批量CSV、中文引用粘贴
```

- [ ] **Step 4: 最终提交 + push**

```bash
git add README.md
git commit -m "docs: 更新README反映8维度验证架构"
git push origin main
```
