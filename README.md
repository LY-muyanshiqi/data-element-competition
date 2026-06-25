# 学术文献真实性验证工具

> 2026年（第三届）大学生数据要素素质大赛参赛项目

基于 CrossRef / OpenAlex 公开 API + 规则引擎，自动识别 AI 生成的虚假文献。

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 启动 Web 界面
streamlit run app.py

# 或使用 Jupyter Notebook 演示
jupyter notebook demo.ipynb
```

## 项目结构

```
├── app.py                 # Streamlit Web 界面
├── verify_engine.py       # 核心验证引擎
├── demo.ipynb             # Jupyter Notebook 演示
├── requirements.txt       # 依赖清单
├── docs/specs/            # 设计文档
└── data/
    ├── template.csv       # 批量验证模板
    └── demo_data.csv      # 演示数据（含真实+虚假文献）
```

## 验证维度

| 维度 | 权重 | 得分条件 |
|------|------|----------|
| DOI 格式校验 | 10分 | 符合标准 DOI 正则 |
| 年份合理性 | 10分 | 1900-2026 范围内 |
| CrossRef 验证 | 40分 | DOI 精确匹配 40 分，标题搜索 25 分 |
| OpenAlex 复核 | 30分 | 确认存在 30 分，数据不一致 15 分 |
| 元数据一致性 | 10分 | 标题相似度 >0.7 |

## 评分与分类

- **≥80**: ✅ 可靠
- **40–79**: ⚠️ 可疑
- **<40**: ❌ 虚假

## API

- [CrossRef API](https://api.crossref.org/) — 免费，无需 Key
- [OpenAlex API](https://api.openalex.org/) — 免费，无需 Key
