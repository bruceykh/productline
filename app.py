# -*- utf-8 -*-
"""OPE 竞品产品线调研 —— 本地 Streamlit 应用。

启动：streamlit run app.py
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from src import store
from src.config import BRANDS, CATEGORIES, CATEGORY_ORDER
from src.pipeline import render_and_export, run_all
from src.render import RenderOptions

st.set_page_config(page_title="OPE 产品线调研", page_icon="🌿", layout="wide")

CATEGORY_LABELS = {k: CATEGORIES[k].zh for k in CATEGORY_ORDER}


def _init_state() -> None:
    st.session_state.setdefault("brand", "greenworks")
    st.session_state.setdefault("result", None)
    st.session_state.setdefault("log", [])


def _progress(stage: str, done: int, total: int, msg: str) -> None:
    state = st.session_state
    state["log"].append(f"[{stage}] {msg}")
    bar = state.get("bar")
    if bar is not None and total:
        bar.progress(min(done / total, 1.0), text=f"{stage} {done}/{total} · {msg[:70]}")


def _run(brand_key: str, categories: list[str], limit: int, dpi: int, refresh: bool) -> None:
    opts = RenderOptions(dpi=dpi)
    st.session_state["log"] = []
    with st.status("正在处理…", expanded=True) as status:
        bar = st.progress(0.0, text="准备中")
        st.session_state["bar"] = bar
        try:
            if refresh:
                result = run_all(
                    brand_key,
                    categories,
                    limit=limit,
                    opts=opts,
                    progress=_progress,
                    refresh=True,
                )
            else:
                skus = store.load_skus(brand_key)
                if not skus:
                    st.error("没有已缓存的 SKU 数据，请先执行采集。")
                    return
                from src.pipeline import process_images

                process_images(brand_key, skus, opts, _progress)
                store.save_skus(brand_key, skus)
                result = render_and_export(brand_key, skus, opts, categories)
            st.session_state["result"] = result
            bar.progress(1.0, text="完成")
            status.update(label="完成", state="complete", expanded=False)
        except NotImplementedError as exc:
            status.update(label="未接入", state="error")
            st.error(str(exc))
        except Exception as exc:  # 采集失败时保留已缓存数据，便于排查
            status.update(label="执行失败", state="error")
            st.exception(exc)
        finally:
            st.session_state["bar"] = None
            st.session_state["log"] = st.session_state["log"][-200:]


def _sku_table(skus) -> None:
    rows = []
    for s in skus:
        rows.append(
            {
                "SKU编码": s.sku_code,
                "品类": CATEGORY_LABELS.get(s.category, s.category),
                "型号名称": s.model_name,
                "KIT/Bare": s.kit_or_bare,
                "尺寸(in)": s.get("size_in"),
                "CFM": s.get("cfm"),
                "Push/SP": s.get("propulsion"),
                "Brushless": "Brushless" if s.get("brushless") else ("" if s.get("brushless") is None else "Brushed"),
                "电压(V)": s.get("platform_voltage_v"),
                "容量(Ah)": s.get("battery_ah"),
                "MSRP": s.get("price_msrp"),
                "图片状态": s.image_status,
                "审核状态": s.review_status,
                "备注": s.notes,
            }
        )
    df = pd.DataFrame(rows)
    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        disabled=["SKU编码", "品类", "图片状态"],
        column_config={
            "审核状态": st.column_config.SelectboxColumn(
                options=["pending", "approved", "rejected"], default="pending"
            ),
            "KIT/Bare": st.column_config.SelectboxColumn(options=["kit", "bare", "unknown"]),
            "Push/SP": st.column_config.SelectboxColumn(options=["push", "self-propelled", ""]),
        },
        key="sku_editor",
    )

    if st.button("保存审核修改", type="primary"):
        by_code = {s.sku_code: s for s in skus}
        manual_keys = {
            "尺寸(in)": "size_in",
            "CFM": "cfm",
            "Push/SP": "propulsion",
            "电压(V)": "platform_voltage_v",
            "容量(Ah)": "battery_ah",
            "MSRP": "price_msrp",
        }
        for _, row in edited.iterrows():
            s = by_code.get(row["SKU编码"])
            if not s:
                continue
            s.review_status = row["审核状态"]
            s.kit_or_bare = row["KIT/Bare"]
            s.notes = row["备注"] if isinstance(row["备注"], str) else ""
            for col, key in manual_keys.items():
                old = s.get(key)
                new = row[col]
                if pd.isna(new) or new in ("", "nan"):
                    continue
                try:
                    new_val = float(new)
                except (TypeError, ValueError):
                    new_val = new
                if old in (None, ""):
                    changed = True
                else:
                    try:
                        changed = abs(float(old) - float(new_val)) > 0.001
                    except (TypeError, ValueError):
                        changed = str(old) != str(new_val)
                if changed:
                    s.set(key, new_val, new_val, s.source_url, "manual")
        store.save_skus(st.session_state["brand"], skus)
        st.success("已保存。")


def main() -> None:
    _init_state()

    st.title("OPE 竞品产品线调研")
    st.caption("输入品牌 → 半自动采集 5 个品类 → 校对 SKU → 导出 PNG 产品线矩阵 + Excel 明细")

    with st.sidebar:
        st.header("调研设置")
        brand_key = st.selectbox(
            "竞品品牌",
            options=list(BRANDS.keys()),
            format_func=lambda k: f"{BRANDS[k].name}{'' if BRANDS[k].enabled else '（待接入）'}",
            index=0,
        )
        st.session_state["brand"] = brand_key
        if not BRANDS[brand_key].enabled:
            st.warning("该品牌的采集适配器尚未接入。第一版先验证 Greenworks，其余品牌共用同一套渲染/质检/导出。")

        categories = st.multiselect(
            "品类",
            options=list(CATEGORY_ORDER),
            default=list(CATEGORY_ORDER),
            format_func=lambda k: CATEGORIES[k].zh,
        )
        limit = st.number_input("每个品类 SKU 上限（0 = 全部）", min_value=0, max_value=300, value=0, step=5)
        dpi = st.selectbox("导出分辨率（1.5cm 图片高度）", options=[150, 300], index=1)
        st.caption(f"当前 1.5cm ≈ {int(15 / 25.4 * dpi)} px")

        refresh = st.radio("数据来源", ["重新采集", "只重渲染已缓存数据"], index=0) == "重新采集"
        started = st.button("开始采集并导出", type="primary", disabled=not BRANDS[brand_key].enabled or not categories)

    if started:
        _run(brand_key, categories, int(limit), int(dpi), refresh)

    result = st.session_state.get("result")
    if result is None:
        cached = store.load_skus(brand_key)
        out_png = Path("output") / brand_key / f"{brand_key}_ope_matrix.png"
        if cached and out_png.exists():
            st.info("检测到上一次的结果，可直接查看或点击左侧按钮重新渲染。")
        else:
            st.info("还没有数据。先在左侧选择品类，然后点击「开始采集并导出」。")
            st.markdown(
                """
                **采集逻辑（Greenworks）**
                1. Shopify 品类接口拉取 5 个品类的全部候选商品；
                2. 过滤配件/备件/套装/翻新，识别 KIT 与 Bare Tool；
                3. 标题 + 详情页规格表解析尺寸、CFM、Push/Self-Propelled、电压平台、Ah、MSRP；
                4. 每张主图去白边后按 contain 等比缩放到 1.5cm 高；
                5. 按 5 个横行渲染 PNG，行内 SKU 过宽时自动换行；
                6. 导出前执行 8 项质检，再输出 Excel 明细。
                """
            )
        return

    skus = result.skus
    tab_matrix, tab_edit, tab_qa, tab_download, tab_log = st.tabs(
        ["产品线矩阵", "SKU 校对", "质检", "下载", "采集日志"]
    )

    with tab_matrix:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("SKU 总数", len(skus))
        c2.metric("品类数", len([v for v in result.stats["by_category"].values() if v]))
        c3.metric("质检错误", result.stats["qa"]["errors"])
        c4.metric("质检警告", result.stats["qa"]["warnings"])
        st.image(str(result.png_path), use_container_width=True)

    with tab_edit:
        st.caption("审核状态与关键字段可在此修改，保存后重新导出即可生效。")
        _sku_table(skus)

    with tab_qa:
        st.json(result.stats["qa"], expanded=False)
        if result.issues:
            st.dataframe(
                pd.DataFrame(
                    [
                        {"检查项": i.check, "级别": i.level, "SKU": i.sku, "说明": i.message}
                        for i in result.issues
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("未发现问题。")

    with tab_download:
        st.download_button(
            "下载 PNG 产品线矩阵",
            data=result.png_path.read_bytes(),
            file_name=result.png_path.name,
            mime="image/png",
        )
        st.download_button(
            "下载 Excel 明细",
            data=result.excel_path.read_bytes(),
            file_name=result.excel_path.name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        st.caption(f"PNG：{result.png_path}")
        st.caption(f"Excel：{result.excel_path}")

    with tab_log:
        st.text("\n".join(st.session_state["log"][-80:]) or "暂无日志")


if __name__ == "__main__":
    main()
