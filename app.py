"""学术文献真实性验证工具 — Streamlit Web 界面"""

from __future__ import annotations
import io
import time

import pandas as pd
import streamlit as st
import plotly.express as px

from verify_engine import ReferenceRecord, verify_record, verify_batch, verify_batch_concurrent

st.set_page_config(page_title="文献真实性验证", page_icon="📚", layout="wide")
st.title("📚 学术文献真实性验证工具")
st.caption("基于 CrossRef / OpenAlex 公开 API + 规则引擎，自动识别 AI 生成的虚假文献")

# ── Tab 1: 单篇验证 ────────────────────────────────────────────────

tab1, tab2 = st.tabs(["🔍 单篇验证", "📂 批量验证"])

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
        ref = ReferenceRecord(
            title=title.strip(),
            authors=authors.strip(),
            journal=journal.strip() or None,
            doi=doi.strip() or None,
            year=year if year else None,
        )

        with st.spinner("正在验证..."):
            result = verify_record(ref)

        # 状态卡片
        color_map = {"可靠": "green", "可疑": "orange", "虚假": "red"}
        st.markdown(
            f"### 验证结果: :{color_map[result.status]}[{result.status}] — 可信度 {result.score}/100"
        )

        # 进度条
        st.progress(result.score / 100, text=f"{result.score} 分")

        # 详情列表
        st.markdown("**验证详情：**")
        for d in result.details:
            st.markdown(f"- {d}")

        # API 原始数据
        with st.expander("API 原始数据"):
            st.json(result.raw_data)

# ── Tab 2: 批量验证 ────────────────────────────────────────────────

with tab2:
    st.markdown("#### 上传 CSV 文件进行批量验证")

    template_df = pd.DataFrame(
        columns=["title", "authors", "journal", "doi", "year"],
    )
    template_csv = template_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "📥 下载 CSV 模板",
        template_csv,
        "batch_template.csv",
        "text/csv",
    )

    uploaded = st.file_uploader("选择 CSV 文件（需包含 title 列）", type="csv")

    if uploaded:
        df = pd.read_csv(uploaded)
        st.write(f"已加载 **{len(df)}** 条记录")
        st.dataframe(df.head(10))

        if st.button("开始批量验证", type="primary"):
            records = []
            for _, row in df.iterrows():
                records.append(
                    ReferenceRecord(
                        title=str(row.get("title", "")),
                        authors=str(row.get("authors", "")),
                        journal=str(row.get("journal", "")) if pd.notna(row.get("journal")) else None,
                        doi=str(row.get("doi", "")) if pd.notna(row.get("doi")) else None,
                        year=int(row["year"]) if pd.notna(row.get("year")) else None,
                    )
                )

            progress_bar = st.progress(0)
            status_text = st.empty()

            results_data = []
            with st.spinner(f"正在并发验证 {len(records)} 条记录..."):
                status_text.text(f"并发验证中（3线程）...")
                all_results = verify_batch_concurrent(records, max_workers=3)
                for ref, r in zip(records, all_results):
                    results_data.append(
                        {
                            "标题": ref.title,
                            "作者": ref.authors,
                            "期刊": ref.journal or "",
                            "DOI": ref.doi or "",
                            "输入年份": ref.year or "",
                            "状态": r.status,
                            "可信度": r.score,
                            "验证详情": "\n".join(r.details),
                        }
                    )
            progress_bar.progress(1.0)

            result_df = pd.DataFrame(results_data)
            st.success(f"验证完成！共 {len(result_df)} 条记录")

            # 汇总统计
            col_a, col_b, col_c = st.columns(3)
            status_counts = result_df["状态"].value_counts()
            col_a.metric("✅ 可靠", status_counts.get("可靠", 0))
            col_b.metric("⚠️ 可疑", status_counts.get("可疑", 0))
            col_c.metric("❌ 虚假", status_counts.get("虚假", 0))

            # 图表
            fig = px.pie(
                result_df, names="状态", color="状态",
                color_discrete_map={"可靠": "#28a745", "可疑": "#ffc107", "虚假": "#dc3545"},
                title="验证结果分布",
            )
            st.plotly_chart(fig)

            fig2 = px.histogram(result_df, x="可信度", nbins=20, title="可信度分布")
            st.plotly_chart(fig2)

            # 结果表格
            st.dataframe(result_df, use_container_width=True)

            # 导出
            csv = result_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 导出验证结果 (CSV)", csv, "verification_results.csv", "text/csv")
