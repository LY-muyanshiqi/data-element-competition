"""学术文献真实性验证引擎

通过 CrossRef/OpenAlex 公开 API + 规则引擎，
对输入的文献信息进行多维度验证，输出 0-100 可信度评分及分类。
"""

from __future__ import annotations
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional
from difflib import SequenceMatcher

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_engine")

CROSSREF_WORK_URL = "https://api.crossref.org/works/{doi}"
OPENALEX_DOI_URL = "https://api.openalex.org/works/doi:{doi}"
OPENALEX_TITLE_URL = "https://api.openalex.org/works?search={title}"

DOI_REGEX = re.compile(r"10\.\d{4,}/[^\s]+")
REQUEST_TIMEOUT = 15
MIN_YEAR, MAX_YEAR = 1900, 2026


@dataclass
class ReferenceRecord:
    title: str
    authors: str
    journal: Optional[str] = None
    doi: Optional[str] = None
    year: Optional[int] = None


@dataclass
class VerificationResult:
    status: str  # "可靠" | "可疑" | "虚假"
    score: int  # 0-100
    details: list = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


# ── helper ──────────────────────────────────────────────────────────

def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def _extract_year_from_crossref(msg: dict) -> Optional[int]:
    for key in ("published-print", "published-online", "issued", "created"):
        try:
            return msg[key]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            continue
    return None


def _safe_get(obj: dict, *keys, default=None):
    for k in keys:
        try:
            obj = obj[k]
        except (KeyError, IndexError, TypeError):
            return default
    return obj


# ── 内存缓存 ──────────────────────────────────────────────────────────

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


# ── dimensions ──────────────────────────────────────────────────────

def _check_doi_format(doi: Optional[str]) -> tuple[int, str]:
    if not doi:
        return 0, "DOI缺失"
    if DOI_REGEX.match(doi.strip()):
        return 8, "DOI格式有效"
    return 0, "DOI格式无效"


def _check_year(year: Optional[int]) -> tuple[int, str]:
    if year is None:
        return 10, "年份未提供（跳过校验）"
    if MIN_YEAR <= year <= MAX_YEAR:
        return 10, "年份在合理范围内"
    return 0, f"年份{year}超出合理范围({MIN_YEAR}-{MAX_YEAR})"


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
                    return {
                        "source": "doi",
                        "title": msg.get("title"),
                        "doi": msg.get("DOI"),
                        "author": msg.get("author", []),
                        "container-title": msg.get("container-title", []),
                    }
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
            raw["crossref"] = {"source": "title_search", "results": items}
            if items:
                best_sim = _title_similarity(ref.title, items[0].get("title", [""])[0])
                if best_sim > 0.7:
                    msg = items[0]
                    raw["crossref"] = {
                        "source": "title_search",
                        "title": msg.get("title"),
                        "author": msg.get("author", []),
                        "container-title": msg.get("container-title", []),
                        "doi": msg.get("DOI"),
                    }
                    return 20, f"CrossRef标题搜索匹配（相似度{best_sim:.2f}）", raw
                return 0, f"CrossRef标题搜索结果不匹配（最高相似度{best_sim:.2f}）", raw
            return 0, "CrossRef标题搜索无结果", raw
    except requests.RequestException as e:
        raw.setdefault("crossref", {})["search_error"] = str(e)
        logger.warning("CrossRef标题搜索失败: %s", e)

    return 0, "CrossRef未找到该文献", raw


def _check_openalex(ref: ReferenceRecord) -> tuple[int, str, dict]:
    raw = {}
    if ref.doi:
        def _oa_doi_lookup():
            try:
                resp = requests.get(
                    OPENALEX_DOI_URL.format(doi=ref.doi.strip()),
                    timeout=REQUEST_TIMEOUT,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return {
                        "source": "doi",
                        "title": data.get("title"),
                        "doi": data.get("doi"),
                        "author": data.get("authorships", []),
                        "publication_year": data.get("publication_year"),
                    }
                elif resp.status_code == 404:
                    return {"source": "doi", "error": "DOI未找到"}
                else:
                    return {"source": "doi", "error": f"HTTP {resp.status_code}"}
            except requests.RequestException as e:
                return {"source": "doi", "error": str(e)}

        key = f"oa_doi_{ref.doi.strip()}"
        cached = _cached_api_call(key, _oa_doi_lookup)
        if "title" in cached and cached.get("source") == "doi":
            raw["openalex"] = cached
            return _openalex_confirm(cached, ref, raw)
        raw["openalex"] = cached

    # 标题搜索
    try:
        resp = requests.get(
            OPENALEX_TITLE_URL.format(title=requests.utils.quote(ref.title)),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            raw.setdefault("openalex", {})["title_search"] = results
            if results:
                best = results[0]
                sim = _title_similarity(ref.title, best.get("title", ""))
                if sim > 0.7:
                    return _openalex_confirm(best, ref, raw)
                return 0, f"OpenAlex标题搜索结果不匹配（相似度{sim:.2f}）", raw
            return 0, "OpenAlex未找到该文献", raw
    except requests.RequestException as e:
        raw.setdefault("openalex", {})["search_error"] = str(e)

    return 0, "OpenAlex未找到该文献", raw


def _openalex_confirm(data: dict, ref: ReferenceRecord, raw: dict) -> tuple[int, str, dict]:
    """确认 OpenAlex 返回数据与输入一致，返回 (得分, 详情, raw)"""
    oa_title = data.get("title", "")
    sim = _title_similarity(ref.title, oa_title) if oa_title else 0

    if sim > 0.9:
        # 检查年份一致性
        oa_year = data.get("publication_year")
        if ref.year and oa_year and ref.year != oa_year:
            return 12, f"OpenAlex确认存在但年份不一致（输入{ref.year} vs API{oa_year}）", raw
        return 25, "OpenAlex确认文献存在", raw
    if sim > 0.7:
        return 12, f"OpenAlex找到但标题不完全匹配（相似度{sim:.2f}）", raw
    return 12, "OpenAlex文献存在", raw


def _check_metadata_consistency(ref: ReferenceRecord, raw: dict) -> tuple[int, str]:
    """元数据一致性检查"""
    for api_key, field_name in [("crossref", "CrossRef"), ("openalex", "OpenAlex")]:
        api_data = raw.get(api_key, {})
        api_title = api_data.get("title", "")
        if isinstance(api_title, list):
            api_title = api_title[0] if api_title else ""
        if api_title:
            sim = _title_similarity(ref.title, api_title)
            if sim < 0.7:
                return 0, f"标题与{field_name}记录不一致（相似度{sim:.2f}）"
    return 5, "元数据一致性检查通过"


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
            # OpenAlex 可能用 authorships
            api_authors_raw = api_data.get("authorships", [])
        if not api_authors_raw:
            continue

        api_author_names = set()
        for a in api_authors_raw:
            # CrossRef: family/given; OpenAlex: author.display_name
            family = (a.get("family") or a.get("last") or "").lower()
            given = (a.get("given") or a.get("first") or "").lower()
            display = ""
            if isinstance(a.get("author"), dict):
                display = a["author"].get("display_name", "").lower()
            if family:
                api_author_names.add(family)
            if given:
                api_author_names.add(given)
            if display:
                api_author_names.add(display)

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


# AI 生成文献的典型标题特征
_AI_FINGERPRINT_PATTERNS = [
    (re.compile(r'\b100%?\s*(accuracy|precision|recall|success)', re.IGNORECASE), "过度夸张声称（100% accuracy）"),
    (re.compile(r'\bsolv(e[ds]?|ing)\s+(NP[- ]?(complete|hard|all|Problems)|all)\b', re.IGNORECASE), "声称解决NP完全/难问题"),
    (re.compile(r'\b(always|never|perfect|infallible|flawless)\b', re.IGNORECASE), "绝对化词汇（always/never/perfect）"),
    (re.compile(r'\bthe\s+(first|only)\b', re.IGNORECASE), "'the first/only' 宣称首创"),
    (re.compile(r'\bnovel\s+(approach|method|framework|technique|Quantum)', re.IGNORECASE), "novel approach 模式（AI高频模板词）"),
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


def _check_journal_match(ref: ReferenceRecord, raw: dict) -> tuple[int, str]:
    """检查输入期刊名是否与API返回一致"""
    if not ref.journal:
        return 5, "期刊未提供（跳过校验）"

    for api_key in ["crossref", "openalex"]:
        api_data = raw.get(api_key, {})
        api_journal = ""
        ct = api_data.get("container-title", "")
        if isinstance(ct, list) and ct:
            api_journal = ct[0]
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


# ── main ────────────────────────────────────────────────────────────

def verify_record(ref: ReferenceRecord) -> VerificationResult:
    total, details = 0, []
    raw: dict = {}

    lang = SemanticChecker.classify_language(ref.title)

    # 维度1: DOI格式 (8分)
    s, d = _check_doi_format(ref.doi)
    total += s
    details.append(f"[DOI格式] {d} (+{s})")

    # 维度2: 语义一致性 (10分) — 整合标题长度+乱码+年份
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


def verify_batch(records: list[ReferenceRecord]) -> list[VerificationResult]:
    results = []
    for i, ref in enumerate(records):
        logger.info("验证 %d/%d: %s", i + 1, len(records), ref.title[:60])
        results.append(verify_record(ref))
        if i < len(records) - 1:
            time.sleep(1)  # 1 req/s
    return results


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
        if not title:
            return False
        chinese_chars = sum(1 for c in title if '一' <= c <= '鿿')
        return chinese_chars > len(title) * 0.3

    @classmethod
    def classify_language(cls, title: str) -> str:
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
        if not title or not title.strip():
            return 0, "标题为空"
        title = title.strip()
        if cls._GARBAGE_PATTERN.search(title):
            return 0, "标题包含不可打印字符"
        if cls._ALL_SPECIAL_PATTERN.match(title):
            return 0, "标题全由特殊符号组成"
        if cls._REPEAT_PATTERN.search(title):
            return 0, "标题包含连续重复字符"
        normal = set(_string.ascii_letters + _string.digits + _string.whitespace)
        abnormal = sum(
            1 for c in title
            if c not in normal
            and not ('一' <= c <= '鿿')
            and c not in '，。！？；：""''（）【】《》、…—·'
        )
        if len(title) > 0 and abnormal / len(title) > 0.5:
            return 0, "标题中异常字符占比过高"
        return 3, "标题无乱码"

    @classmethod
    def check_year_range(cls, year, lang: str = "en") -> tuple[int, str]:
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


# ── 知网/万方引用解析 ────────────────────────────────────────────────


# 中文学术引用类型码
_REF_TYPE_CODES = 'J|N|D|M|C|R|P'
_TITLE_PATTERN = re.compile(r'([.。])\s*([^.。]+?)\s*\[(' + _REF_TYPE_CODES + r')\]')
_YEAR_PATTERN = re.compile(r'(19\d{2}|20\d{2})')


def parse_single_reference(text: str) -> dict:
    """从单条知网引用文本中提取标题和年份"""
    raw = text.strip()
    title = None
    year = None

    matches = list(_TITLE_PATTERN.finditer(raw))
    if matches:
        title = matches[-1].group(2).strip()
        after_type = raw[matches[-1].end():]
    else:
        after_type = raw

    year_match = _YEAR_PATTERN.search(after_type)
    if year_match:
        year = int(year_match.group(1))

    return {'title': title, 'year': year, 'raw': raw}


def parse_references_batch(text: str) -> list:
    """批量解析引用文本（每行一条），自动跳过空行"""
    records = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parsed = parse_single_reference(line)
        records.append(parsed)
    return records
