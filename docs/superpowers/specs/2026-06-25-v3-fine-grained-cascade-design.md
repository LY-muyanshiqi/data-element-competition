# 验证引擎 v3 — 精细分类 + 级联验证 + 多源扩展

## 概述

在 v2（8维度评分）基础上进行 5 项迭代优化，对标 RefChecker / FiCi / CiteTracer 等业界最佳实践。

## 约束

- 不涉及模型训练 / 深度学习
- 仅调用公开免费 API（CrossRef / OpenAlex / Semantic Scholar / arXiv）
- 输出 0-100 可信度评分
- Python + Streamlit，无额外服务依赖

## 1. 6档精细分类

| 阈值 | 标签 | 含义 |
|------|------|------|
| ≥85 | 确定真实 | 双API确认 + 作者/期刊/年份全匹配 |
| 70-84 | 高度可信 | 单API确认 + 元数据大体一致 |
| 50-69 | 存疑-可能为真 | 标题搜到但部分字段不一致 |
| 30-49 | 存疑-可能为假 | 仅格式正确，无API验证记录 |
| 15-29 | 高度存疑 | API歧义 + AI特征命中 |
| <15 | 确定虚假 | 多维度全面失败 |

原 3 档（可靠/可疑/虚假）保留为兼容映射。

## 2. 级联验证策略

验证后端按顺序尝试，一旦确认就停止，不再浪费请求：

```
DOI 存在?
├─ YES → CrossRef DOI 查询 → 成功(30分) → NEXT DIMENSION
│       └─ 失败 → OpenAlex DOI 查询 → 成功(25分)
│               └─ 失败 → 进入标题搜索
└─ NO  → 进入标题搜索

标题搜索:
  CrossRef 标题搜索 → 成功(20分) → NEXT
  └─ 失败 → OpenAlex 标题搜索 → 成功(20分)
          └─ 失败 → Semantic Scholar 搜索 → 成功(15分)
                  └─ 失败 → 未找到(0分)
```

## 3. RapidFuzz 模糊匹配

- 安装依赖: `rapidfuzz>=3.0`
- 替换 `SequenceMatcher` 为 `rapidfuzz.fuzz.token_sort_ratio`
- 适用场景: 标题匹配、期刊匹配、作者姓氏匹配

## 4. Semantic Scholar 第三数据源

- API: `https://api.semanticscholar.org/graph/v1/paper/search?query={title}`
- DOI 查询: `https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}`
- 免费、无需 Key、速率限制 100 req/5min

## 5. PDF / BibTeX 导入

- PDF: PyMuPDF (fitz) 提取文本 → 正则切分参考文献段落
- BibTeX: bibtexparser 解析 → 提取 title/author/journal/doi/year
- `extract_references_from_pdf(filepath) -> list[ReferenceRecord]`
- `extract_references_from_bibtex(text) -> list[ReferenceRecord]`
- UI 新增 Tab 4 "文件导入" 支持上传 PDF/BibTeX

## 技术栈变更

- 新增: `rapidfuzz>=3.0`, `pymupdf>=1.23`, `bibtexparser>=1.4`
