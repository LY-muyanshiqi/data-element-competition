# 学术文献真实性验证工具 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个完整的学术文献真实性验证工具，通过 CrossRef/OpenAlex API + 规则引擎对文献进行多维度验证，输出 0-100 可信度评分。

**Architecture:** verify_engine.py 为核心验证引擎（纯函数、无状态），app.py 为 Streamlit Web 界面（双Tab：单篇+批量），demo.ipynb 为 Jupyter Notebook 演示。

**Tech Stack:** Python 3.x, Streamlit, Pandas, Requests, Plotly, openpyxl

## Global Constraints

- 不涉及模型训练 / 深度学习
- 仅调用公开 API + 规则匹配
- 批量验证时控制 API 调用频率（1 req/s）
- 输出 0-100 可信度评分，≥80可靠 / 40-79可疑 / <40虚假

---
