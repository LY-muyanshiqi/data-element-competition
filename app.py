"""学术文献真实性验证工具 — Streamlit Web 界面 v3"""

from __future__ import annotations
import importlib
import io
import os
import sys
import tempfile

import pandas as pd
import streamlit as st
import plotly.express as px

import verify_engine as ve

st.set_page_config(page_title="文献真实性验证", page_icon="📚", layout="wide")
st.title("📚 学术文献真实性验证工具 v3")
st.caption("基于 CrossRef / OpenAlex / Semantic Scholar 公开 API + 级联验证引擎，自动识别 AI 生成的虚假文献")

# 6档分类颜色映射
COLOR_MAP = {
    "确定真实": "green",
    "高度可信": "green",
    "存疑-可能为真": "orange",
    "存疑-可能为假": "orange",
    "高度存疑": "red",
    "确定虚假": "red",
}

STATUS_ORDER = ["确定真实", "高度可信", "存疑-可能为真", "存疑-可能为假", "高度存疑", "确定虚假"]
STATUS_DISPLAY = {
    "确定真实": "🟢 确定真实",
    "高度可信": "🟢 高度可信",
    "存疑-可能为真": "🟠 存疑-可能为真",
    "存疑-可能为假": "🟠 存疑-可能为假",
    "高度存疑": "🔴 高度存疑",
    "确定虚假": "🔴 确定虚假",
}

# ── Tab 1: 单篇验证 ────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs(["🔍 单篇验证", "📂 批量验证", "📄 中文文献验证", "📁 文件导入"])

with tab1:
    col1, col2 = st.columns(2)
    with col1:
        title = st.text_input("文献标题 *", placeholder="请输入完整标题")
        authors = st.text_input("作者", placeholder="逗号分隔，如 Zhang San, Li Si")
    with col2:
        doi = st.text_input("DOI", placeholder="如 10.1038/nature12373")
        journal = st.text_input("期刊 / 出版社", placeholder="如 Nature")
    year = st.number_input("发表年份", min_value=0, max_value=2100, value=0, step=1)

    if st.button("开始验证", type="primary", disabled=not title.strip()):
        ref = ve.ReferenceRecord(
            title=title.strip(),
            authors=authors.strip(),
            journal=journal.strip() or None,
            doi=doi.strip() or None,
            year=year if year else None,
        )

        with st.spinner("正在级联验证（CrossRef → OpenAlex → Semantic Scholar）..."):
            result = ve.verify_record(ref)

        st.markdown(
            f"### 验证结果: :{COLOR_MAP.get(result.status, 'gray')}[{result.status}] — 可信度 {result.score}/100"
        )

        st.progress(result.score / 100, text=f"{result.score} 分")

        st.markdown("**验证详情：**")
        for d in result.details:
            st.markdown(f"- {d}")

        with st.expander("API 原始数据"):
            st.json(result.raw_data)

        if result.corrections:
            st.markdown("**💡 修正建议：**")
            for c in result.corrections:
                st.markdown(f"- {c}")

# ── Tab 2: 批量验证 ────────────────────────────────────────────────

with tab2:
    st.markdown("#### 上传 CSV 文件进行批量验证")

    template_df = pd.DataFrame(columns=["title", "authors", "journal", "doi", "year"])
    template_csv = template_df.to_csv(index=False).encode("utf-8")
    st.download_button("📥 下载 CSV 模板", template_csv, "batch_template.csv", "text/csv")

    uploaded = st.file_uploader("选择 CSV 文件（需包含 title 列）", type="csv")

    if uploaded:
        df = pd.read_csv(uploaded)
        st.write(f"已加载 **{len(df)}** 条记录")
        st.dataframe(df.head(10))

        if st.button("开始批量验证", type="primary"):
            records = []
            for _, row in df.iterrows():
                records.append(ve.ReferenceRecord(
                    title=str(row.get("title", "")),
                    authors=str(row.get("authors", "")),
                    journal=str(row.get("journal", "")) if pd.notna(row.get("journal")) else None,
                    doi=str(row.get("doi", "")) if pd.notna(row.get("doi")) else None,
                    year=int(row["year"]) if pd.notna(row.get("year")) else None,
                ))

            results_data = []
            with st.spinner(f"正在并发验证 {len(records)} 条记录（3线程）..."):
                all_results = ve.verify_batch_concurrent(records, max_workers=3)
                for ref, r in zip(records, all_results):
                    results_data.append({
                        "标题": ref.title, "作者": ref.authors,
                        "期刊": ref.journal or "", "DOI": ref.doi or "",
                        "输入年份": ref.year or "",
                        "状态": r.status, "可信度": r.score,
                        "验证详情": "\n".join(r.details),
                    })

            result_df = pd.DataFrame(results_data)
            st.success(f"验证完成！共 {len(result_df)} 条记录")

            st.markdown("**6档分类统计：**")
            cols = st.columns(6)
            for i, status in enumerate(STATUS_ORDER):
                count = int((result_df["状态"] == status).sum())
                cols[i].metric(STATUS_DISPLAY[status], count)

            fig = px.pie(
                result_df, names="状态", color="状态",
                category_orders={"状态": STATUS_ORDER},
                color_discrete_map={
                    "确定真实": "#28a745", "高度可信": "#5cb85c",
                    "存疑-可能为真": "#f0ad4e", "存疑-可能为假": "#ffc107",
                    "高度存疑": "#dc3545", "确定虚假": "#c9302c",
                },
                title="验证结果分布（6档）",
            )
            st.plotly_chart(fig)

            fig2 = px.histogram(result_df, x="可信度", nbins=20, title="可信度分布")
            st.plotly_chart(fig2)

            st.dataframe(result_df, use_container_width=True)

            csv = result_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 导出验证结果 (CSV)", csv, "verification_results.csv", "text/csv")

            # PDF 报告
            if st.button("📄 生成 PDF 报告"):
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    pdf_path = tmp.name
                ve.generate_pdf_report(all_results, records, pdf_path)
                with open(pdf_path, "rb") as f:
                    st.download_button("📥 下载 PDF 报告", f, "verification_report.pdf", "application/pdf")
                os.unlink(pdf_path)

# ── Tab 3: 中文文献验证 ────────────────────────────────────────────

with tab3:
    st.markdown("#### 粘贴知网/万方引用格式，自动解析并验证")
    st.caption("支持格式：[1]作者.标题[J].期刊名,2022,(7):156-159.")

    raw_text = st.text_area(
        "粘贴引用文本（每行一条）", height=200,
        placeholder="[1]张三,李四.基于深度学习的洪水预测模型研究[J].水利学报,2022,53(6):156-169.\n[2]王五.人工智能在气象预测中的应用[D].北京大学,2023.",
    )

    if st.button("解析并验证", type="primary", key="btn_cn", disabled=not raw_text.strip()):
        parsed = ve.parse_references_batch(raw_text)
        records = []
        for p in parsed:
            records.append(ve.ReferenceRecord(
                title=p.get("title", "") or "", authors="", doi=None, year=p.get("year"),
            ))

        st.info(f"解析出 {len(records)} 条文献")

        results_data = []
        with st.spinner(f"正在验证 {len(records)} 条..."):
            for i, rec in enumerate(records):
                r = ve.verify_record(rec)
                results_data.append({
                    "序号": i + 1, "标题": rec.title, "年份": rec.year or "",
                    "状态": r.status, "可信度": r.score,
                    "验证详情": "\n".join(r.details),
                })

        result_df = pd.DataFrame(results_data)

        cols = st.columns(6)
        for i, status in enumerate(STATUS_ORDER):
            count = int((result_df["状态"] == status).sum())
            cols[i].metric(STATUS_DISPLAY[status], count)

        st.dataframe(result_df, use_container_width=True)

        csv = result_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button("📥 导出结果 (CSV)", csv, "cn_literature_results.csv", "text/csv")

# ── Tab 4: 文件导入 ────────────────────────────────────────────────

with tab4:
    st.markdown("#### 上传 PDF 或 BibTeX 文件，自动提取并验证参考文献")

    uploaded_file = st.file_uploader("选择文件", type=["pdf", "bib", "txt"])
    file_type = st.radio("文件类型", ["自动检测", "PDF", "BibTeX"], horizontal=True)

    if uploaded_file:
        file_bytes = uploaded_file.read()
        st.write(f"文件大小：**{len(file_bytes) / 1024:.1f} KB**")

        if st.button("提取并验证", type="primary", key="btn_file"):
            records = []

            fname = uploaded_file.name.lower()
            is_bib = fname.endswith(".bib") or file_type == "BibTeX"
            is_pdf = fname.endswith(".pdf") or file_type == "PDF"

            with st.spinner("正在提取参考文献..."):
                if is_bib:
                    try:
                        text = file_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        text = file_bytes.decode("latin-1")
                    records = ve.extract_references_from_bibtex(text)
                    st.info(f"从 BibTeX 提取出 {len(records)} 条文献")
                elif is_pdf:
                    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                        tmp.write(file_bytes)
                        tmp_path = tmp.name
                    try:
                        records = ve.extract_references_from_pdf(tmp_path)
                        st.info(f"从 PDF 提取出 {len(records)} 条文献")
                    finally:
                        os.unlink(tmp_path)
                else:
                    st.error("无法识别文件类型，请手动选择")

            if records:
                results_data = []
                with st.spinner(f"正在级联验证 {len(records)} 条记录..."):
                    all_results = ve.verify_batch_concurrent(records, max_workers=3)
                    for ref, r in zip(records, all_results):
                        results_data.append({
                            "标题": ref.title, "DOI": ref.doi or "",
                            "年份": ref.year or "", "状态": r.status,
                            "可信度": r.score, "验证详情": "\n".join(r.details),
                        })

                result_df = pd.DataFrame(results_data)
                st.success(f"验证完成！共 {len(result_df)} 条记录")

                cols = st.columns(6)
                for i, status in enumerate(STATUS_ORDER):
                    count = int((result_df["状态"] == status).sum())
                    cols[i].metric(STATUS_DISPLAY[status], count)

                fig = px.pie(
                    result_df, names="状态", color="状态",
                    category_orders={"状态": STATUS_ORDER},
                    color_discrete_map={
                        "确定真实": "#28a745", "高度可信": "#5cb85c",
                        "存疑-可能为真": "#f0ad4e", "存疑-可能为假": "#ffc107",
                        "高度存疑": "#dc3545", "确定虚假": "#c9302c",
                    },
                    title="验证结果分布（6档）",
                )
                st.plotly_chart(fig)

                st.dataframe(result_df, use_container_width=True)

                csv = result_df.to_csv(index=False).encode("utf-8-sig")
                st.download_button("📥 导出结果 (CSV)", csv, "file_import_results.csv", "text/csv")
