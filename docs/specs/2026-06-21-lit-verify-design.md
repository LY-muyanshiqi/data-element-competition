# 学术文献真实性验证工具 — 设计文档

## 概述

用Python实现的学术文献真实性自动验证工具。通过CrossRef/OpenAlex公开API + 规则引擎，对输入的文献信息进行多维度验证，输出0-100可信度评分及分类。

## 项目结构

```
数据要素大赛/
├── app.py                    # Streamlit Web界面
├── verify_engine.py          # 核心验证引擎
├── demo.ipynb                # Jupyter Notebook演示
├── requirements.txt          # 依赖清单
├── README.md                 # 使用说明
├── docs/specs/               # 设计文档
└── data/
    ├── template.csv          # 批量验证模板
    └── demo_data.csv         # 演示数据（含真+假文献）
```

## 架构

线性流水线，4步顺序执行：

```
ReferenceRecord → 格式校验 → CrossRef验证 → OpenAlex复核 → 综合评分 → VerificationResult
```

## 数据模型

### 输入 `ReferenceRecord`
- title: str — 文献标题
- authors: str — 作者（逗号分隔）
- journal: str | None — 期刊/出版社
- doi: str | None — DOI
- year: int | None — 发表年份

### 输出 `VerificationResult`
- status: str — "可靠" | "可疑" | "虚假"
- score: int — 0-100可信度评分
- details: list[str] — 各维度验证详情（如"DOI格式有效"、"CrossRef中未找到该文献"）
- raw_data: dict — API返回的原始数据（用于前端展示）

## 验证维度与权重

| 维度 | 权重 | 得分条件 |
|------|------|----------|
| DOI格式校验 | 10分 | 符合标准DOI正则得10分，缺失或无效得0分 |
| 年份合理性 | 10分 | 1900-2026范围内得10分，否则0分 |
| CrossRef验证 | 40分 | DOI精确匹配40分，标题搜索匹配25分，未找到0分 |
| OpenAlex复核 | 30分 | 确认存在30分，数据不一致15分，未找到0分 |
| 元数据一致性 | 10分 | 标题相似度>0.7得10分，否则0分 |

## 评分阈值

- ≥80分 → 可靠 (绿色)
- 40-79分 → 可疑 (黄色)  
- <40分 → 虚假 (红色)

## API

- **CrossRef**: `https://api.crossref.org/works/{doi}` — 免费，无需Key
- **OpenAlex**: `https://api.openalex.org/works/doi:{doi}` — 免费，无需Key
- 无DOI时用标题搜索：`https://api.openalex.org/works?search={title}`

## UI

Streamlit双Tab布局：单篇验证（表单+结果卡片）+ 批量验证（文件上传+汇总图表+导出CSV）

## 技术栈

Python 3.x, Streamlit, Pandas, Requests, Plotly, openpyxl

## 约束

- 不涉及模型训练
- 不涉及深度学习
- 仅调用公开API + 规则匹配
- 批量验证时控制API调用频率（1 req/s），避免被限流
