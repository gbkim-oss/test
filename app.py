import hashlib
import sqlite3
import time
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parent / "marketing.db"
ADMIN_ID = "admin"
ADMIN_PASSWORD_SHA256 = (
    "ac9689e2272427085e35b9d3e3e8bed88cb3434828b43b86fc0596cad4c6e270"
)
MAX_ATTEMPTS = 3
LOCK_SECONDS = 300


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _init_auth_state() -> None:
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "failed_attempts" not in st.session_state:
        st.session_state.failed_attempts = 0
    if "lock_until" not in st.session_state:
        st.session_state.lock_until = 0.0


def _is_locked() -> bool:
    return time.time() < st.session_state.lock_until


def _remaining_lock_seconds() -> int:
    return max(0, int(st.session_state.lock_until - time.time()))


@st.cache_data
def load_report() -> pd.DataFrame:
    if not DB_PATH.is_file():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT date, channel, campaign, impressions, clicks, cost, conversions, revenue FROM daily_report",
            conn,
        )
    finally:
        conn.close()
    df["date"] = pd.to_datetime(df["date"])
    return df


def render_login() -> None:
    st.title("마케팅 대시보드")
    st.subheader("로그인")

    if _is_locked():
        r = _remaining_lock_seconds()
        st.error(
            f"로그인 시도가 {MAX_ATTEMPTS}회 초과되어 {r // 60}분 {r % 60}초 후에 다시 시도할 수 있습니다."
        )
        return

    with st.form("login_form", clear_on_submit=False):
        uid = st.text_input("아이디", autocomplete="username")
        pw = st.text_input("비밀번호", type="password", autocomplete="current-password")
        submitted = st.form_submit_button("로그인")

    if not submitted:
        return

    if not uid or not pw:
        st.warning("아이디와 비밀번호를 입력하세요.")
        return

    if uid.strip() == ADMIN_ID and _sha256_hex(pw) == ADMIN_PASSWORD_SHA256:
        st.session_state.authenticated = True
        st.session_state.failed_attempts = 0
        st.session_state.lock_until = 0.0
        st.rerun()
        return

    st.session_state.failed_attempts += 1
    left = MAX_ATTEMPTS - st.session_state.failed_attempts
    if st.session_state.failed_attempts >= MAX_ATTEMPTS:
        st.session_state.lock_until = time.time() + LOCK_SECONDS
        st.session_state.failed_attempts = 0
        st.error(
            f"비밀번호가 올바르지 않습니다. {MAX_ATTEMPTS}회 실패로 {LOCK_SECONDS // 60}분간 로그인이 제한됩니다."
        )
        st.rerun()
    else:
        st.error(f"아이디 또는 비밀번호가 올바르지 않습니다. ({left}회 남음)")


def render_dashboard() -> None:
    df_all = load_report()
    if df_all.empty:
        st.error(f"DB를 찾을 수 없거나 데이터가 없습니다: {DB_PATH}")
        return

    st.title("마케팅 성과 대시보드")

    with st.sidebar:
        st.header("필터")
        dmin = df_all["date"].min().date()
        dmax = df_all["date"].max().date()
        dr = st.date_input(
            "기간",
            value=(dmin, dmax),
            min_value=dmin,
            max_value=dmax,
        )
        if isinstance(dr, tuple) and len(dr) == 2:
            start_d, end_d = dr[0], dr[1]
        else:
            start_d = end_d = dr if not isinstance(dr, tuple) else dr[0]

        channels = sorted(df_all["channel"].unique().tolist())
        sel_ch = st.multiselect("채널", options=channels, default=channels)

        camp_opts = sorted(
            df_all[df_all["channel"].isin(sel_ch)]["campaign"].unique().tolist()
        )
        sel_camp = st.multiselect("캠페인", options=camp_opts, default=camp_opts)

        st.divider()
        if st.button("로그아웃"):
            st.session_state.authenticated = False
            st.session_state.failed_attempts = 0
            st.session_state.lock_until = 0.0
            st.rerun()

    mask = (
        (df_all["date"].dt.date >= start_d)
        & (df_all["date"].dt.date <= end_d)
        & (df_all["channel"].isin(sel_ch))
        & (df_all["campaign"].isin(sel_camp))
    )
    df = df_all.loc[mask].copy()

    if df.empty:
        st.info("선택한 필터에 맞는 데이터가 없습니다.")
        return

    total_cost = int(df["cost"].sum())
    total_rev = int(df["revenue"].sum())
    total_imp = int(df["impressions"].sum())
    total_clk = int(df["clicks"].sum())
    total_conv = int(df["conversions"].sum())
    roas = total_rev / total_cost if total_cost else 0.0
    ctr = (total_clk / total_imp * 100) if total_imp else 0.0
    cvr = (total_conv / total_clk * 100) if total_clk else 0.0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("총 비용", f"{total_cost:,}원")
    c2.metric("총 매출", f"{total_rev:,}원")
    c3.metric("ROAS", f"{roas:.2f}")
    c4.metric("CTR", f"{ctr:.2f}%")
    c5.metric("CVR", f"{cvr:.2f}%")

    daily = (
        df.assign(day=df["date"].dt.date)
        .groupby("day", as_index=False)
        .agg(cost=("cost", "sum"), revenue=("revenue", "sum"))
        .sort_values("day")
        .rename(columns={"day": "일자"})
    )
    st.subheader("일별 비용·매출 추이")
    st.line_chart(daily.set_index("일자")[["cost", "revenue"]])

    by_ch = (
        df.groupby("channel", as_index=False)
        .agg(cost=("cost", "sum"), revenue=("revenue", "sum"), clicks=("clicks", "sum"))
        .sort_values("cost", ascending=False)
    )
    st.subheader("채널별 비용·매출")
    st.bar_chart(by_ch.set_index("channel")[["cost", "revenue"]])

    by_camp = (
        df.groupby("campaign", as_index=False)
        .agg(cost=("cost", "sum"), revenue=("revenue", "sum"))
        .sort_values("revenue", ascending=False)
        .head(15)
    )
    st.subheader("캠페인 매출 상위 15")
    st.bar_chart(by_camp.set_index("campaign")["revenue"])

    with st.expander("필터 적용 원본 데이터"):
        show = df.sort_values(["date", "channel", "campaign"]).copy()
        show["date"] = show["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(show, use_container_width=True, hide_index=True)


def main() -> None:
    st.set_page_config(
        page_title="마케팅 대시보드",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _init_auth_state()

    if not st.session_state.authenticated:
        render_login()
        return

    render_dashboard()


if __name__ == "__main__":
    main()
