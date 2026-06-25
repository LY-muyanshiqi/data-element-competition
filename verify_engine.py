"""学术文献真实性验证引擎 v3

通过 CrossRef / OpenAlex / Semantic Scholar 公开 API + 规则引擎 + 级联验证策略，
对输入的文献信息进行多维度验证，输出 0-100 可信度评分及 6 档细粒度分类。
"""

from __future__ import annotations
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import requests
from rapidfuzz import fuzz

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_engine")

CROSSREF_WORK_URL = "https://api.crossref.org/works/{doi}"
OPENALEX_DOI_URL = "https://api.openalex.org/works/doi:{doi}"
OPENALEX_TITLE_URL = "https://api.openalex.org/works?search={title}"
SEMANTIC_SCHOLAR_DOI_URL = "https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search?query={title}&limit=5"

DOI_REGEX = re.compile(r"10\.\d{4,}/[^\s]+")
REQUEST_TIMEOUT = 15


@dataclass
class ReferenceRecord:
    title: str
    authors: str
    journal: Optional[str] = None
    doi: Optional[str] = None
    year: Optional[int] = None


@dataclass
class VerificationResult:
    status: str   # 6档: "确定真实"|"高度可信"|"存疑-可能为真"|"存疑-可能为假"|"高度存疑"|"确定虚假"
    score: int  # 0-100
    details: list = field(default_factory=list)
    raw_data: dict = field(default_factory=dict)


# ── helpers ───────────────────────────────────────────────────────────

def _title_similarity(a: str, b: str) -> float:
    """使用 RapidFuzz token_sort_ratio 做标题相似度"""
    if not a or not b:
        return 0.0
    return fuzz.token_sort_ratio(a.strip().lower(), b.strip().lower()) / 100.0


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
    now = time.time()
    if key in _doi_cache and now - _doi_cache[key].get("_ts", 0) < ttl:
        return _doi_cache[key]
    result = fetcher()
    result["_ts"] = now
    _doi_cache[key] = result
    return result


def clear_cache():
    _doi_cache.clear()


# ── 级联验证：多数据源串行查询 ──────────────────────────────────────

def _normalize_crossref_authors(msg: dict) -> list:
    """标准化 CrossRef 作者格式"""
    authors = msg.get("author", [])
    result = []
    for a in authors:
        family = (a.get("family") or "").lower()
        given = (a.get("given") or "").lower()
        if family:
            result.append({"family": family, "given": given})
    return result


def _try_crossref_doi(ref: ReferenceRecord) -> tuple[int, str, dict] | None:
    """尝试 CrossRef DOI 精确匹配，成功返回结果，失败返回 None"""
    if not ref.doi:
        return None

    def _lookup():
        try:
            resp = requests.get(CROSSREF_WORK_URL.format(doi=ref.doi.strip()), timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                msg = resp.json().get("message", {})
                return {
                    "backend": "crossref_doi",
                    "title": msg.get("title"),
                    "doi": msg.get("DOI"),
                    "author": _normalize_crossref_authors(msg),
                    "container-title": msg.get("container-title", []),
                }
            return {"backend": "crossref_doi", "error": f"HTTP {resp.status_code}" if resp.status_code != 404 else "DOI未找到"}
        except requests.RequestException as e:
            return {"backend": "crossref_doi", "error": str(e)}

    cached = _cached_api_call(f"cr_doi_{ref.doi.strip()}", _lookup)
    if "title" in cached:
        # 检查元数据一致性以给予更高分
        cr_title = cached.get("title", "")
        if isinstance(cr_title, list) and cr_title:
            cr_title = cr_title[0]
        title_sim = _title_similarity(ref.title, cr_title) if cr_title else 0
        if title_sim > 0.9:
            return 35, "CrossRef DOI匹配成功（标题高度一致）", {"crossref": cached}
        if title_sim > 0.7:
            return 30, "CrossRef DOI匹配成功", {"crossref": cached}
        return 25, "CrossRef DOI匹配但标题有差异", {"crossref": cached}
    return None


def _try_openalex_doi(ref: ReferenceRecord) -> tuple[int, str, dict] | None:
    """尝试 OpenAlex DOI 精确匹配"""
    if not ref.doi:
        return None

    def _lookup():
        try:
            resp = requests.get(OPENALEX_DOI_URL.format(doi=ref.doi.strip()), timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "backend": "openalex_doi",
                    "title": data.get("title"),
                    "doi": data.get("doi"),
                    "author": data.get("authorships", []),
                    "publication_year": data.get("publication_year"),
                }
            return {"backend": "openalex_doi", "error": f"HTTP {resp.status_code}" if resp.status_code != 404 else "DOI未找到"}
        except requests.RequestException as e:
            return {"backend": "openalex_doi", "error": str(e)}

    cached = _cached_api_call(f"oa_doi_{ref.doi.strip()}", _lookup)
    if "title" in cached:
        oa_title = cached.get("title", "")
        sim = _title_similarity(ref.title, oa_title) if oa_title else 0
        if sim > 0.9:
            oa_year = cached.get("publication_year")
            if ref.year and oa_year and ref.year != oa_year:
                return 12, f"OpenAlex DOI匹配但年份不一致（输入{ref.year} vs API{oa_year}）", {"openalex": cached}
            return 25, "OpenAlex DOI匹配成功", {"openalex": cached}
        if sim > 0.7:
            return 12, f"OpenAlex DOI匹配但标题不完全一致（相似度{sim:.2f}）", {"openalex": cached}
        return 12, "OpenAlex DOI匹配存在", {"openalex": cached}
    return None


def _try_crossref_title(ref: ReferenceRecord, doi_failed: bool = False) -> tuple[int, str, dict] | None:
    """尝试 CrossRef 标题搜索。doi_failed=True 时降低得分（DOI查不到说明可疑）"""
    try:
        resp = requests.get(
            "https://api.crossref.org/works",
            params={"query.title": ref.title, "rows": 3},
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            items = resp.json().get("message", {}).get("items", [])
            if items:
                best = items[0]
                best_title = best.get("title", [""])[0]
                sim = _title_similarity(ref.title, best_title)
                if sim > 0.7:
                    score = 20 if not doi_failed else 10
                    detail = f"CrossRef标题搜索匹配（相似度{sim:.2f}）" + (" [DOI查不到，降权]" if doi_failed else "")
                    return score, detail, {
                        "crossref": {
                            "backend": "crossref_title",
                            "title": best.get("title"),
                            "author": _normalize_crossref_authors(best),
                            "container-title": best.get("container-title", []),
                            "doi": best.get("DOI"),
                        }
                    }
                return 0, f"CrossRef标题搜索不匹配（最高相似度{sim:.2f}）", {"crossref": {"backend": "crossref_title", "results": items}}
            return None
    except requests.RequestException as e:
        logger.warning("CrossRef标题搜索失败: %s", e)
    return None


def _try_openalex_title(ref: ReferenceRecord) -> tuple[int, str, dict] | None:
    """尝试 OpenAlex 标题搜索"""
    try:
        resp = requests.get(
            OPENALEX_TITLE_URL.format(title=requests.utils.quote(ref.title)),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                best = results[0]
                sim = _title_similarity(ref.title, best.get("title", ""))
                if sim > 0.7:
                    oa_year = best.get("publication_year")
                    if ref.year and oa_year and ref.year != oa_year:
                        return 12, f"OpenAlex标题匹配但年份不一致（输入{ref.year} vs API{oa_year}）", {
                            "openalex": {"backend": "openalex_title", "title": best.get("title"), "author": best.get("authorships", []), "publication_year": oa_year}
                        }
                    return 20, f"OpenAlex标题搜索匹配（相似度{sim:.2f}）", {
                        "openalex": {"backend": "openalex_title", "title": best.get("title"), "author": best.get("authorships", []), "publication_year": oa_year}
                    }
                return 0, f"OpenAlex标题搜索不匹配（相似度{sim:.2f}）", {"openalex": {"backend": "openalex_title", "results": results}}
    except requests.RequestException as e:
        logger.warning("OpenAlex标题搜索失败: %s", e)
    return None


def _try_semantic_scholar(ref: ReferenceRecord) -> tuple[int, str, dict] | None:
    """尝试 Semantic Scholar（DOI + 标题搜索）"""
    raw = {"backend": "semantic_scholar"}

    # DOI 查询
    if ref.doi:
        try:
            resp = requests.get(SEMANTIC_SCHOLAR_DOI_URL.format(doi=ref.doi.strip()), timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                ss_title = data.get("title", "")
                sim = _title_similarity(ref.title, ss_title) if ss_title else 0
                raw["title"] = ss_title
                raw["authors"] = data.get("authors", [])
                raw["venue"] = _safe_get(data, "journal", "name", default="")
                if sim > 0.9:
                    return 15, "Semantic Scholar DOI确认存在", {"semantic_scholar": raw}
                elif sim > 0.7:
                    return 10, f"Semantic Scholar DOI匹配但标题有差异（相似度{sim:.2f}）", {"semantic_scholar": raw}
                return 5, "Semantic Scholar DOI存在但标题不匹配", {"semantic_scholar": raw}
        except requests.RequestException:
            pass

    # 标题搜索
    try:
        resp = requests.get(
            SEMANTIC_SCHOLAR_SEARCH_URL.format(title=requests.utils.quote(ref.title)),
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                best = data[0]
                ss_title = best.get("title", "")
                sim = _title_similarity(ref.title, ss_title)
                if sim > 0.8:
                    raw["title"] = ss_title
                    raw["authors"] = best.get("authors", [])
                    return 10, f"Semantic Scholar标题搜索匹配（相似度{sim:.2f}）", {"semantic_scholar": raw}
                return 0, f"Semantic Scholar搜索结果不匹配", {"semantic_scholar": raw}
    except requests.RequestException as e:
        logger.warning("Semantic Scholar搜索失败: %s", e)
    return None


# ── 级联主函数 ───────────────────────────────────────────────────────

def _cascade_verify(ref: ReferenceRecord) -> tuple[int, str, dict]:
    """级联验证：逐个数据源尝试，一旦确认即停止"""
    raw: dict = {}
    combined_raw: dict = {}

    # Stage 1: DOI 级联
    doi_stage_tried = False
    doi_stage_failed = False
    if ref.doi:
        doi_stage_tried = True
        # 1a. CrossRef DOI
        result = _try_crossref_doi(ref)
        if result and result[0] >= 30:
            combined_raw.update(result[2])
            return result[0], result[1], combined_raw
        if result:
            combined_raw.update(result[2])

        # 1b. OpenAlex DOI
        result = _try_openalex_doi(ref)
        if result and result[0] >= 25:
            combined_raw.update(result[2])
            return result[0], result[1], combined_raw
        if result:
            combined_raw.update(result[2])

        doi_stage_failed = True  # DOI有但两个都没确认

    # Stage 2: 标题搜索级联
    # 2a. CrossRef 标题
    result = _try_crossref_title(ref, doi_failed=doi_stage_failed)
    if result and result[0] >= (20 if not doi_stage_failed else 10):
        combined_raw.update(result[2])
        return result[0], result[1], combined_raw
    if result:
        combined_raw.update(result[2])

    # 2b. OpenAlex 标题
    result = _try_openalex_title(ref)
    if result and result[0] >= 20:
        combined_raw.update(result[2])
        return result[0], result[1], combined_raw
    if result:
        combined_raw.update(result[2])

    # Stage 3: Semantic Scholar (最后手段)
    result = _try_semantic_scholar(ref)
    if result:
        combined_raw.update(result[2])
        return result[0], result[1], combined_raw

    return 0, "所有数据源均未找到该文献", combined_raw


# ── 独立验证维度 ────────────────────────────────────────────────────

def _check_doi_format(doi: Optional[str]) -> tuple[int, str]:
    if not doi:
        return 0, "DOI缺失"
    if DOI_REGEX.match(doi.strip()):
        return 8, "DOI格式有效"
    return 0, "DOI格式无效"


def _check_doi_validity(ref: ReferenceRecord, raw: dict, cascade_score: int) -> tuple[int, str]:
    """DOI格式有效但API查不到 = 强烈造假信号"""
    if not ref.doi or not DOI_REGEX.match(ref.doi.strip()):
        return 5, "无有效DOI（跳过）"

    # 检查是否有DOI级的匹配（不是标题搜索）
    has_doi_match = False
    for api_key in ["crossref", "openalex", "semantic_scholar"]:
        api_data = raw.get(api_key, {})
        backend = api_data.get("backend", "")
        if "doi" in backend and "error" not in api_data:
            has_doi_match = True
            break

    if has_doi_match:
        return 5, "DOI已验证存在"

    # DOI查不到：检查是否标题搜索找到了（可疑）
    has_title_match = cascade_score >= 15
    if has_title_match:
        return -3, "DOI无记录但标题搜到相似文献（可能为嵌合体引用）"

    return -8, "DOI格式正确但所有数据源均无记录"


def _check_author_match(ref: ReferenceRecord, raw: dict) -> tuple[int, str]:
    if not ref.authors:
        return 0, "作者未提供（无法校验）"

    input_authors = [a.strip().lower() for a in ref.authors.split(',') if a.strip()]
    if not input_authors:
        return 0, "作者未提供（无法校验）"

    for api_key in ["crossref", "openalex", "semantic_scholar"]:
        api_data = raw.get(api_key, {})
        api_authors_raw = api_data.get("author", [])
        if not api_authors_raw:
            api_authors_raw = api_data.get("authorships", [])
        if not api_authors_raw:
            api_authors_raw = api_data.get("authors", [])

        if not api_authors_raw:
            continue

        api_author_names = set()
        for a in api_authors_raw:
            family = (a.get("family") or a.get("last") or "").lower()
            given = (a.get("given") or a.get("first") or "").lower()
            name = a.get("name", "").lower()
            if isinstance(a.get("author"), dict):
                name = name or a["author"].get("display_name", "").lower()
            if family:
                api_author_names.add(family)
            if given:
                api_author_names.add(given)
            if name:
                api_author_names.add(name)

        if not api_author_names:
            continue

        # 使用 RapidFuzz 部分匹配
        matched = 0
        for input_a in input_authors:
            best = max((fuzz.partial_ratio(input_a, api_a) for api_a in api_author_names), default=0)
            if best >= 80:
                matched += 1

        match_rate = matched / len(input_authors)
        if match_rate >= 0.5:
            return 10, f"作者匹配通过（{matched}/{len(input_authors)}）"
        elif match_rate > 0:
            return 5, f"作者部分匹配（{matched}/{len(input_authors)}）"
        else:
            return 0, f"作者不匹配（输入{len(input_authors)}人，无一命中API记录）"

    return 0, "无API作者数据（无法校验）"


def _check_journal_match(ref: ReferenceRecord, raw: dict) -> tuple[int, str]:
    if not ref.journal:
        return 0, "期刊未提供（无法校验）"

    for api_key in ["crossref", "openalex", "semantic_scholar"]:
        api_data = raw.get(api_key, {})
        api_journal = ""

        ct = api_data.get("container-title", "")
        if isinstance(ct, list) and ct:
            api_journal = ct[0]
        oa_journal = api_data.get("journal", "")
        if oa_journal:
            api_journal = api_journal or oa_journal
        venue = api_data.get("venue", "")
        if venue:
            api_journal = api_journal or venue

        if api_journal:
            sim = fuzz.token_sort_ratio(ref.journal.lower(), api_journal.lower()) / 100.0
            if sim > 0.7:
                return 5, f"期刊名匹配通过（相似度{sim:.2f}）"
            else:
                return 0, f"期刊名不匹配（输入「{ref.journal}」vs API「{api_journal[:40]}」）"

    return 0, "无API期刊数据（无法校验）"


def _check_metadata_consistency(ref: ReferenceRecord, raw: dict) -> tuple[int, str]:
    for api_key in ["crossref", "openalex", "semantic_scholar"]:
        api_data = raw.get(api_key, {})
        api_title = api_data.get("title", "")
        if isinstance(api_title, list):
            api_title = api_title[0] if api_title else ""
        if api_title:
            sim = _title_similarity(ref.title, api_title)
            if sim < 0.7:
                return 0, f"标题与API记录不一致（相似度{sim:.2f}）"
    return 5, "元数据一致性检查通过"


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


# ── 6档分类 ─────────────────────────────────────────────────────────

def _classify_6level(score: int) -> str:
    """6档细粒度分类"""
    if score >= 80:
        return "确定真实"
    elif score >= 65:
        return "高度可信"
    elif score >= 50:
        return "存疑-可能为真"
    elif score >= 30:
        return "存疑-可能为假"
    elif score >= 15:
        return "高度存疑"
    else:
        return "确定虚假"


# ── main ────────────────────────────────────────────────────────────

def verify_record(ref: ReferenceRecord) -> VerificationResult:
    total, details = 0, []
    raw: dict = {}

    lang = SemanticChecker.classify_language(ref.title)

    # 维度1: DOI格式 (8分)
    s, d = _check_doi_format(ref.doi)
    total += s
    details.append(f"[DOI格式] {d} (+{s})")

    # 维度2: 语义一致性 (10分)
    s1, d1 = SemanticChecker.check_title_length(ref.title)
    s2, d2 = SemanticChecker.check_garbage_chars(ref.title)
    s3, d3 = SemanticChecker.check_year_range(ref.year, lang)
    total += s1 + s2 + s3
    details.append(f"[标题长度] {d1} (+{s1})")
    details.append(f"[乱码检测] {d2} (+{s2})")
    details.append(f"[年份] {d3} (+{s3})")

    # 维度3: 级联验证
    s, d, cascade_raw = _cascade_verify(ref)
    total += s
    details.append(f"[数据源] {d} (+{s})")
    raw.update(cascade_raw)

    # 维度3b: DOI真实性 (DOI格式对但查不到=强烈造假信号)
    s_doi_val, d_doi_val = _check_doi_validity(ref, raw, s)
    total += s_doi_val
    details.append(f"[DOI真实性] {d_doi_val} ({'+' if s_doi_val>=0 else ''}{s_doi_val})")

    api_confirmed = s >= 20  # 至少标题搜索匹配

    # 维度4: 作者匹配 (10分)
    s, d = _check_author_match(ref, raw)
    total += s
    details.append(f"[作者匹配] {d} (+{s})")

    # 维度5: AI标题特征 (7分)
    s, d = _check_title_ai_fingerprints(ref.title)
    ai_fp_hits = 0 if s == 7 else (1 if s == 5 else (2 if s == 3 else 3))
    total += s
    details.append(f"[AI特征] {d} (+{s})")

    # 维度6: 期刊匹配 (5分)
    s, d = _check_journal_match(ref, raw)
    total += s
    details.append(f"[期刊匹配] {d} (+{s})")

    # 维度7: 元数据一致性 (5分)
    s, d = _check_metadata_consistency(ref, raw)
    metadata_ok = s >= 5
    total += s
    details.append(f"[一致性] {d} (+{s})")

    score = max(0, min(100, total))
    status = _classify_6level(score)

    return VerificationResult(status=status, score=score, details=details, raw_data=raw)


# ── 批量验证 ────────────────────────────────────────────────────────

def verify_batch(records: list[ReferenceRecord]) -> list[VerificationResult]:
    results = []
    for i, ref in enumerate(records):
        logger.info("验证 %d/%d: %s", i + 1, len(records), ref.title[:60])
        results.append(verify_record(ref))
        if i < len(records) - 1:
            time.sleep(1)
    return results


def verify_batch_concurrent(records: list[ReferenceRecord], max_workers: int = 3) -> list[VerificationResult]:
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


# ── SemanticChecker ─────────────────────────────────────────────────

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

_REF_TYPE_CODES = 'J|N|D|M|C|R|P'
_TITLE_PATTERN = re.compile(r'([.。])\s*([^.。]+?)\s*\[(' + _REF_TYPE_CODES + r')\]')
_YEAR_PATTERN = re.compile(r'(19\d{2}|20\d{2})')


def parse_single_reference(text: str) -> dict:
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
    records = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parsed = parse_single_reference(line)
        records.append(parsed)
    return records


# ── PDF / BibTeX 导入 ────────────────────────────────────────────────

def extract_references_from_pdf(filepath: str) -> list[ReferenceRecord]:
    """从PDF中提取参考文献列表，返回 ReferenceRecord 列表"""
    import fitz
    doc = fitz.open(filepath)
    full_text = ""
    for page in doc:
        full_text += page.get_text()

    # 定位参考文献段落
    ref_section_patterns = [
        r'(?i)\n(?:References|Bibliography|参考文献)\s*\n',
        r'(?i)\n\[\d+\]',
    ]

    ref_start = len(full_text)
    for pat in ref_section_patterns:
        m = re.search(pat, full_text)
        if m:
            ref_start = min(ref_start, m.start())
            break

    if ref_start < len(full_text):
        ref_text = full_text[ref_start:]
    else:
        ref_text = full_text

    # 按 [N] 格式切分
    ref_entries = re.split(r'\n(?=\[\d+\])', ref_text)
    records = []
    for entry in ref_entries:
        entry = entry.strip()
        if not entry or len(entry) < 20:
            continue

        # 提取DOI
        doi_match = DOI_REGEX.search(entry)
        doi = doi_match.group(0) if doi_match else None

        # 提取年份
        year_match = re.search(r'(19\d{2}|20\d{2})', entry)
        year = int(year_match.group(1)) if year_match else None

        # 标题：取 [N] 之后、第一个句号之前的主要部分
        title = ""
        cleaned = re.sub(r'^\[\d+\]\s*', '', entry)
        parts = re.split(r'[.。]', cleaned)
        if len(parts) >= 2:
            title = parts[1].strip()
        else:
            title = parts[0].strip()[:200]

        records.append(ReferenceRecord(title=title[:300], authors="", doi=doi, year=year))

    doc.close()
    return records


def extract_references_from_bibtex(text: str) -> list[ReferenceRecord]:
    """从 BibTeX 文本中解析参考文献"""
    import bibtexparser
    from bibtexparser.bparser import BibTexParser

    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False
    bib_db = bibtexparser.loads(text, parser=parser)

    records = []
    for entry in bib_db.entries:
        title = entry.get("title", "").replace("{", "").replace("}", "")
        authors = entry.get("author", "").replace("{", "").replace("}", "")
        journal = entry.get("journal", "") or entry.get("booktitle", "") or entry.get("publisher", "")
        doi = entry.get("doi", None)
        year_str = entry.get("year", None)
        year = int(year_str) if year_str and year_str.isdigit() else None

        records.append(ReferenceRecord(
            title=title, authors=authors, journal=journal or None, doi=doi, year=year
        ))

    return records
