# 学术文献真实性验证工具

> 2026年（第三届）大学生数据要素素质大赛参赛项目

基于 CrossRef / OpenAlex 公开 API + 规则引擎，自动识别 AI 生成的虚假文献。

## 快速开始

```bash
pip install -r requirements.txt
streamlit run app.py        # Web 界面
jupyter notebook demo.ipynb  # Jupyter 演示
```

## 项目结构

```
├── app.py                 # Streamlit Web 界面（3 Tab）
├── verify_engine.py       # 8 维度验证引擎
├── demo.ipynb             # Jupyter Notebook 演示
├── tests/                 # pytest 单元测试
├── .github/workflows/     # GitHub Actions CI
├── docs/specs/            # 设计文档
└── data/
    ├── template.csv       # 批量验证模板
    └── demo_data.csv      # 演示数据（真实+虚假文献）
```

## 验证维度（8 维度 / 100 分）

| 维度 | 权重 | 得分条件 |
|------|------|----------|
| DOI 格式校验 | 8分 | 符合标准 DOI 正则 |
| 语义一致性 | 10分 | 标题长度合理(中5-300/英10-500) + 无乱码 + 年份合理(中文1980-2026, 英文1900-2026) |
| CrossRef 验证 | 30分 | DOI 精确匹配 30 分，标题搜索 20 分 |
| OpenAlex 复核 | 25分 | 确认存在 25 分，年份不一致 12 分 |
| 作者匹配 | 10分 | 输入作者与 API 记录匹配 |
| AI 标题特征 | 7分 | 未检测到夸张/AI模板词得满分 |
| 期刊匹配 | 5分 | 输入期刊与 API 记录一致 |
| 元数据一致性 | 5分 | 标题相似度 >0.7 |

## 评分与分类

- **≥80**: ✅ 可靠
- **40–79**: ⚠️ 可疑
- **<40**: ❌ 虚假

## 功能特性

- **单篇验证**：输入标题/DOI/作者/期刊/年份，逐维度评分
- **批量验证**：上传 CSV，3 线程并发 + DOI 缓存，支持导出结果
- **中文文献**：粘贴知网/万方引用格式，自动解析并验证
- **AI 特征检测**：识别过于夸张的标题（"100% accuracy"、"linear time NP-complete" 等）

## 运行测试

```bash
pytest tests/ -v
```

## API

- [CrossRef API](https://api.crossref.org/) — 免费，无需 Key
- [OpenAlex API](https://api.openalex.org/) — 免费，无需 Key