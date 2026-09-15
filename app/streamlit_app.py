"""Source entry point: python -m streamlit run app/streamlit_app.py."""
from datetime import date
from uuid import uuid4
import streamlit as st
from eda.conversation.checkpoint import validate_thread
from eda.conversation.models import STATE_VERSION
from eda.metrics.definitions import SEMANTIC
from eda.viz import runtime
from eda.viz.spec import figure

st.set_page_config(page_title="业务证据分析", layout="wide")
for key, value in {"thread_id": uuid4().hex, "provider": "fake", "reference_date": date(2024, 12, 31),
                   "messages": [], "last_response": None, "last_submission_id": None,
                   "processing": False, "audit_warning": None}.items():
    if key not in st.session_state:
        st.session_state[key] = value


def clear_display():
    st.session_state.messages = []
    st.session_state.last_response = None
    st.session_state.last_submission_id = None
    st.session_state.audit_warning = None


with st.sidebar:
    st.header("会话")
    st.selectbox("provider", ["fake", "real"], key="provider", disabled=st.session_state.processing)
    st.text("thread_id: " + st.session_state.thread_id)
    st.caption("恢复的是业务状态，不恢复历史查询结果或完整消息历史。浏览器状态可能随连接重建丢失。")
    st.date_input("reference_date", key="reference_date", disabled=st.session_state.processing)
    st.text("semantic: " + SEMANTIC.schema_version + " / state: " + STATE_VERSION)
    if st.button("新建会话", disabled=st.session_state.processing):
        st.session_state.thread_id = uuid4().hex
        clear_display()
        st.rerun()
    with st.form("restore"):
        restore_id = st.text_input("已有 thread_id", max_chars=128)
        restore = st.form_submit_button("恢复会话", disabled=st.session_state.processing)
    if restore:
        try:
            validate_thread(restore_id)
            session = runtime.service("fake", st.session_state.reference_date.isoformat()).inspect(restore_id)
            if session.turn_count == 0:
                st.warning("未找到已完成的业务状态。")
            else:
                st.session_state.thread_id = restore_id
                clear_display()
                st.rerun()
        except Exception:
            st.error("无法恢复，请检查会话编号与参考日期。")

st.title("业务证据分析")
st.caption("本地 Fake 演示 · 确定性结果 · 无法判断原因")
with st.form("question_form"):
    question = st.text_input("问题", max_chars=2048)
    new_topic = st.checkbox("新话题")
    submitted = st.form_submit_button("提交", disabled=st.session_state.processing)
if submitted and not st.session_state.processing:
    if not question.strip():
        st.warning("请输入问题。")
    else:
        st.session_state.processing = True
        run_id = uuid4().hex
        st.session_state.last_submission_id = run_id
        try:
            view, warning = runtime.submit(st.session_state.thread_id, question, st.session_state.provider,
                st.session_state.reference_date.isoformat(), new_topic, run_id)
            st.session_state.last_response = view
            st.session_state.audit_warning = warning
            st.session_state.messages.append({"status": view.status, "request_id": view.technical.get("request_id")})
        finally:
            st.session_state.processing = False

view = st.session_state.last_response
if view:
    st.subheader("当前轮状态：" + view.status)
    st.text(view.message)
    if view.status == "success":
        st.subheader(view.metric + " · " + view.operation + "（" + view.unit + "）")
        with st.expander("计划与实际口径", expanded=True):
            st.json(view.plan)
        for key in ("metric_value", "baseline_value", "current_value", "absolute_change", "change_rate_display"):
            if key in view.kpis:
                st.metric(key, "无定义" if view.kpis[key] is None else str(view.kpis[key]))
        st.dataframe(list(view.rows), hide_index=True)
        if view.hidden:
            st.json(view.hidden)
        if view.chart:
            st.plotly_chart(figure(view.chart, view.chart_rows), width="stretch")
        st.write("completeness")
        st.json(view.completeness)
        for warning in view.warnings:
            st.warning(warning)
        st.write("evidence_id → query_id")
        st.json(view.evidence)
    if st.session_state.audit_warning:
        st.warning(st.session_state.audit_warning)
    with st.expander("技术详情"):
        st.json({k: v for k, v in view.technical.items() if k != "sql"})
        if view.technical.get("sql"):
            st.code(view.technical["sql"], language="sql")
