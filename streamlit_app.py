from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from sklearn.decomposition import PCA

from lol_search.config import load_settings
from lol_search.play_similarity import PlaySimilarity
from lol_search.query_engine import QueryEngine
from lol_search.rag import QueryInterpreter


ROOT = Path(__file__).resolve().parent


def _row_key(row: dict[str, Any]) -> tuple[str, int, int]:
    return str(row.get("match_id", "")), int(row.get("start_ms", 0)), int(row.get("end_ms", 0))


def format_ms(ms: int | float | None) -> str:
    total = max(0, int(float(ms or 0) // 1000))
    return f"{total // 60}:{total % 60:02d}"


def count_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


@st.cache_resource(show_spinner=False)
def get_settings():
    return load_settings(ROOT)


@st.cache_resource(show_spinner=False)
def get_engine(index_mtime: float) -> QueryEngine:
    _ = index_mtime
    return QueryEngine(get_settings().data_index_dir)


@st.cache_resource(show_spinner=False)
def get_play_similarity(index_mtime: float, clip_signature: tuple[tuple[str, int, int], ...]) -> PlaySimilarity:
    _ = index_mtime, clip_signature
    settings = get_settings()
    return PlaySimilarity(settings.data_index_dir, settings.data_clips_dir)


def get_index_mtime() -> float:
    path = get_settings().data_index_dir / "metadata.jsonl"
    return path.stat().st_mtime if path.exists() else 0.0


def load_status() -> dict[str, Any]:
    settings = get_settings()
    clips = sorted(
        [p for p in settings.data_clips_dir.iterdir() if p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if settings.data_clips_dir.exists() else []
    info_path = settings.data_index_dir / "index_info.json"
    info = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    return {
        "matches": len(list(settings.data_raw_dir.glob("*.match.json"))),
        "timelines": len(list(settings.data_raw_dir.glob("*.timeline.json"))),
        "windows": count_jsonl(settings.data_processed_dir / "windows.jsonl"),
        "clips": clips,
        "index": info,
    }


def resolve_rows(
    *,
    engine: QueryEngine,
    text: str,
    mode: str,
    top_k: int,
    use_llm_parser: bool,
    min_kills: int | None,
    max_kills: int | None,
    requires_dragon: bool,
    requires_baron: bool,
    no_kills: bool,
    window_seconds: int,
    sort_by: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    interpreter = QueryInterpreter(openai_api_key=get_settings().openai_api_key)
    parsed = interpreter.parse_with_llm(text) if use_llm_parser else interpreter.parse(text)

    if mode == "precise":
        if no_kills:
            rows = engine.top_macro_no_kill(window_seconds=window_seconds, top_k=top_k)
            filters = {"min_kills": 0, "max_kills": 0, "duration_s": window_seconds}
        else:
            filters = {
                "min_kills": min_kills,
                "max_kills": max_kills,
                "requires_dragon": requires_dragon,
                "requires_baron": requires_baron,
                "duration_s": window_seconds,
                "sort_by": sort_by,
                "descending": True,
                "top_k": top_k,
            }
            rows = engine.filter_search(**filters)
        return rows, filters, text

    filters = parsed.filters
    if filters and mode == "auto":
        rows = engine.filter_search(top_k=top_k, **filters)
    else:
        rows = engine.semantic_search(parsed.semantic_text, top_k=top_k)
    return rows, filters, parsed.semantic_text


def projection_for_rows(engine: QueryEngine, rows: list[dict[str, Any]]) -> pd.DataFrame:
    index_by_key: dict[tuple[str, int, int], int] = {}
    for i, row in enumerate(engine.metadata):
        index_by_key.setdefault(_row_key(row), i)

    selected_indices: list[int] = []
    selected_rows: list[dict[str, Any]] = []
    for row in rows:
        idx = index_by_key.get(_row_key(row))
        if idx is not None:
            selected_indices.append(idx)
            selected_rows.append(row)

    if not selected_rows:
        return pd.DataFrame()

    vectors = engine.embeddings[np.array(selected_indices, dtype=int)].astype(np.float32)
    if vectors.shape[0] >= 2 and vectors.shape[1] >= 2:
        coords = PCA(n_components=2).fit_transform(vectors)
    else:
        coords = np.zeros((vectors.shape[0], 2), dtype=np.float32)
    sims = vectors @ vectors.T

    records: list[dict[str, Any]] = []
    for i, row in enumerate(selected_rows):
        nearest = []
        if sims.shape[0] > 1:
            for j in np.argsort(-sims[i]):
                if int(j) == i:
                    continue
                nearest.append(f"#{int(j) + 1} {float(sims[i, int(j)]):.3f}")
                if len(nearest) >= 3:
                    break
        event_type = "Macro"
        if int(row.get("multikill_max", 0)) >= 3:
            event_type = "Multikill"
        elif bool(row.get("jungler_invade")):
            event_type = "Invade"
        elif bool(row.get("mid_roam")):
            event_type = "Roam"
        elif bool(row.get("mid_jungle_pair")):
            event_type = "Mid+Jungle"
        elif int(row.get("baron_kills", 0)) > 0:
            event_type = "Baron"
        elif int(row.get("dragon_kills", 0)) > 0:
            event_type = "Dragon"
        elif int(row.get("kills_total", 0)) > 0:
            event_type = "Fight"

        records.append(
            {
                "rank": i + 1,
                "pca_x": float(coords[i, 0]),
                "pca_y": float(coords[i, 1]),
                "event_type": event_type,
                "time": f"{format_ms(row.get('start_ms'))} - {format_ms(row.get('end_ms'))}",
                "nearest": ", ".join(nearest),
                **row,
            }
        )
    return pd.DataFrame.from_records(records)


def run_lol_search(args: list[str], timeout_s: int = 600) -> tuple[int | None, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}".rstrip(os.pathsep)
    python = sys.executable
    try:
        proc = subprocess.run(
            [python, "-m", "lol_search.cli", *args],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"Timeout apres {timeout_s}s: {' '.join(args)}"
    return proc.returncode, "\n".join(x for x in [proc.stdout.strip(), proc.stderr.strip()] if x)


def selected_result(rows: list[dict[str, Any]]) -> tuple[int, dict[str, Any] | None]:
    if not rows:
        return 0, None
    labels = [
        f"#{i + 1} {row.get('match_id')} {format_ms(row.get('start_ms'))}-{format_ms(row.get('end_ms'))}"
        for i, row in enumerate(rows)
    ]
    label = st.selectbox("Fenetre selectionnee", labels, key="selected_window")
    idx = labels.index(label)
    return idx + 1, rows[idx]


st.set_page_config(page_title="LoL Replay Search", page_icon="LOL", layout="wide")
st.title("LoL Replay Search")

status = load_status()
metric_cols = st.columns(4)
metric_cols[0].metric("Matches", status["matches"])
metric_cols[1].metric("Timelines", status["timelines"])
metric_cols[2].metric("Fenetres", status["windows"])
metric_cols[3].metric("Clips", len(status["clips"]))

with st.sidebar:
    st.header("Recherche")
    query_text = st.text_input("Texte", "invade d'un jungler dans la jungle ennemie en early game")
    mode = st.radio("Mode", ["auto", "semantic", "precise"], horizontal=True, index=0)
    top_k = st.slider("Top K", 2, 50, 12)
    use_llm_parser = st.toggle("Parser LLM", value=False)
    auto_fetch = st.toggle("Auto fetch", value=True)
    auto_load = st.toggle("Auto load", value=True)

    st.divider()
    st.header("Filtres")
    min_kills_raw = st.number_input("Kills min", min_value=0, value=0)
    max_kills_raw = st.number_input("Kills max", min_value=0, value=0)
    window_seconds = st.number_input("Fenetre (s)", min_value=1, value=30)
    requires_dragon = st.toggle("Dragon", value=False)
    requires_baron = st.toggle("Baron", value=False)
    no_kills = st.toggle("No kills", value=False)
    sort_by = st.selectbox(
        "Tri precise",
        ["gold_abs_swing", "kills_total", "dragon_kills", "baron_kills", "tower_kills", "seconds_to_next_dragon"],
    )

    st.divider()
    st.header("Pipeline")
    ingest_riot_id = st.text_input("Riot ID", placeholder="GameName#EUW")
    ingest_count = st.number_input("Count", min_value=1, value=10)
    step_seconds = st.number_input("Step (s)", min_value=1, value=10)

engine = get_engine(get_index_mtime())
min_kills = int(min_kills_raw) if min_kills_raw > 0 else None
max_kills = int(max_kills_raw) if max_kills_raw > 0 else None

if "rows" not in st.session_state:
    st.session_state.rows = []
    st.session_state.filters = {}
    st.session_state.semantic_text = ""
    st.session_state.console = "Ready"

search_clicked = st.sidebar.button("Chercher", type="primary", width="stretch")
if search_clicked:
    with st.spinner("Recherche dans l'index..."):
        rows, filters, semantic_text = resolve_rows(
            engine=engine,
            text=query_text,
            mode=str(mode),
            top_k=int(top_k),
            use_llm_parser=use_llm_parser,
            min_kills=min_kills,
            max_kills=max_kills,
            requires_dragon=requires_dragon,
            requires_baron=requires_baron,
            no_kills=no_kills,
            window_seconds=int(window_seconds),
            sort_by=sort_by,
        )
    st.session_state.rows = rows
    st.session_state.filters = filters
    st.session_state.semantic_text = semantic_text
    st.session_state.console = json.dumps({"mode": mode, "semantic_text": semantic_text, "filters": filters}, indent=2)

rows = st.session_state.rows

left, right = st.columns([1.65, 1.0], gap="large")

with left:
    st.subheader("Projection PCA")
    if rows:
        df = projection_for_rows(engine, rows)
        df["plot_size"] = df["kills_total"].fillna(0).astype(float).clip(lower=1)
        fig = px.scatter(
            df,
            x="pca_x",
            y="pca_y",
            color="event_type",
            size="plot_size",
            size_max=18,
            hover_data={
                "rank": True,
                "match_id": True,
                "time": True,
                "kills_total": True,
                "dragon_kills": True,
                "baron_kills": True,
                "gold_abs_swing": True,
                "multikill_max": True,
                "multikill_champions": True,
                "phase": True,
                "invade_team": True,
                "invade_zones": True,
                "roam_target_zones": True,
                "nearest": True,
                "pca_x": False,
                "pca_y": False,
                "plot_size": False,
            },
            color_discrete_map={
                "Invade": "#b94e35",
                "Roam": "#247a84",
                "Mid+Jungle": "#6f5fb8",
                "Multikill": "#b02f7c",
                "Dragon": "#2d8f74",
                "Fight": "#a57421",
                "Baron": "#6b3f25",
                "Macro": "#8b9088",
            },
        )
        fig.update_traces(marker={"line": {"width": 1, "color": "white"}})
        fig.update_layout(height=520, margin=dict(l=12, r=12, t=24, b=12), legend_title_text="")
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Lance une recherche pour afficher la projection PCA des fenetres.")

    st.subheader("Resultats")
    if rows:
        table = pd.DataFrame(rows)
        table.insert(0, "rank", range(1, len(table) + 1))
        table["start"] = table["start_ms"].map(format_ms)
        table["end"] = table["end_ms"].map(format_ms)
        preferred_columns = [
            "rank",
            "match_id",
            "start",
            "end",
            "phase",
            "kills_total",
            "dragon_kills",
            "baron_kills",
            "gold_abs_swing",
            "multikill_max",
            "multikill_champions",
            "ace",
            "first_blood",
            "jungler_invade",
            "invade_team",
            "invade_zones",
            "mid_roam",
            "roam_target_zones",
            "mid_jungle_pair",
            "blue_jungler_champion",
            "red_jungler_champion",
            "blue_mid_champion",
            "red_mid_champion",
            "player_names",
            "text",
        ]
        display_columns = [col for col in preferred_columns if col in table.columns]
        st.dataframe(
            table[display_columns],
            width="stretch",
            hide_index=True,
        )

with right:
    st.subheader("Replay")
    result_index, row = selected_result(rows)
    if row:
        st.write(f"**{row['match_id']}**")
        st.caption(f"{format_ms(row['start_ms'])} - {format_ms(row['end_ms'])}")
        st.write(row.get("text", ""))

        c1, c2, c3 = st.columns(3)
        selected_args = ["query-open-result", "--match-id", str(row["match_id"]), "--start-ms", str(row["start_ms"]), "--end-ms", str(row["end_ms"])]
        if c1.button("Open", width="stretch"):
            args = [*selected_args, "--no-record"]
            if auto_fetch:
                args.append("--auto-fetch")
            if not auto_load:
                args.append("--no-auto-load")
            with st.spinner("Ouverture du replay..."):
                code, output = run_lol_search(args)
            st.session_state.console = output or f"exit={code}"
        if c2.button("Clip", width="stretch"):
            args = [
                "export-clip",
                "--match-id",
                str(row["match_id"]),
                "--start-ms",
                str(row["start_ms"]),
                "--end-ms",
                str(row["end_ms"]),
                "--title",
                "streamlit_selected",
            ]
            if auto_fetch:
                args.append("--auto-fetch")
            if auto_load:
                args.append("--auto-load")
            with st.spinner("Export du clip..."):
                code, output = run_lol_search(args)
            st.session_state.console = output or f"exit={code}"
        if c3.button("Record", width="stretch"):
            args = [*selected_args, "--auto-fetch"]
            if not auto_load:
                args.append("--no-auto-load")
            with st.spinner("Ouverture et enregistrement..."):
                code, output = run_lol_search(args)
            st.session_state.console = output or f"exit={code}"

    st.subheader("Voisins")
    if row and rows:
        df = projection_for_rows(engine, rows)
        selected = df[df["rank"] == result_index]
        if not selected.empty:
            st.write(selected.iloc[0]["nearest"] or "Aucun voisin")

    st.subheader("Console")
    st.code(st.session_state.console, language="json")

st.divider()
pipe_cols = st.columns(4)
if pipe_cols[0].button("Ingest", disabled=not ingest_riot_id):
    code, output = run_lol_search(["ingest-summoner", "--riot-id", ingest_riot_id, "--count", str(ingest_count)], timeout_s=900)
    st.session_state.console = output or f"exit={code}"
if pipe_cols[1].button("Build windows"):
    code, output = run_lol_search(["build-windows", "--window-seconds", str(window_seconds), "--step-seconds", str(step_seconds)])
    st.session_state.console = output or f"exit={code}"
if pipe_cols[2].button("Build index"):
    code, output = run_lol_search(["build-index"], timeout_s=900)
    get_engine.clear()
    st.session_state.console = output or f"exit={code}"
if pipe_cols[3].button("Export batch"):
    args = ["export-clips-from-query", "--text", query_text, "--top-k", str(top_k), "--export-k", "3", "--continue-on-error"]
    if use_llm_parser:
        args.append("--use-llm-parser")
    if auto_fetch:
        args.append("--auto-fetch")
    if auto_load:
        args.append("--auto-load")
    code, output = run_lol_search(args, timeout_s=1200)
    st.session_state.console = output or f"exit={code}"

st.subheader("Clips")
clips = load_status()["clips"]
if len(clips) >= 2:
    st.subheader("Plays similaires — recherche multimodale")
    st.caption("Prototype non supervise : description, evenements du match et apparence/mouvement des images. Chaque score vaut de 0 a 1 relativement aux clips disponibles : 1 signifie meilleur candidat, pas similarite parfaite.")
    anchor_name = st.selectbox("Clip de reference", [clip.name for clip in clips], key="similarity_anchor")
    weight_cols = st.columns(3)
    text_weight = weight_cols[0].slider("Description", 0.0, 3.0, 1.0, 0.25)
    gameplay_weight = weight_cols[1].slider("Evenements de jeu", 0.0, 3.0, 1.0, 0.25)
    video_weight = weight_cols[2].slider("Images video", 0.0, 3.0, 1.0, 0.25)
    if st.button("Trouver des plays similaires", type="primary"):
        if text_weight + gameplay_weight + video_weight <= 0:
            st.warning("Choisir au moins une modalite.")
        else:
            signature = tuple((clip.name, clip.stat().st_size, clip.stat().st_mtime_ns) for clip in clips)
            with st.spinner("Analyse des images et comparaison des clips..."):
                try:
                    similarity = get_play_similarity(get_index_mtime(), signature)
                    st.session_state.similar_plays = similarity.similar(
                        anchor_name,
                        text_weight=text_weight,
                        gameplay_weight=gameplay_weight,
                        video_weight=video_weight,
                        top_k=3,
                    )
                    st.session_state.similar_anchor = anchor_name
                except (RuntimeError, ValueError) as exc:
                    st.error(str(exc))
    if st.session_state.get("similar_anchor") == anchor_name:
        for rank, item in enumerate(st.session_state.get("similar_plays", []), 1):
            with st.expander(f"#{rank} {item['match_id']} {format_ms(item['start_ms'])}–{format_ms(item['end_ms'])} · score {item['score']:.2f}"):
                st.video(item["path"])
                st.write(item["text"])
                st.caption(
                    f"Description {item['text_score']:.2f} · Evenements {item['gameplay_score']:.2f} · Video {item['video_score']:.2f}"
                )

if clips:
    cols = st.columns(3)
    for i, clip in enumerate(clips):
        with cols[i % 3]:
            st.video(str(clip))
            st.caption(f"{clip.name} - {max(1, round(clip.stat().st_size / 1024 / 1024))} MB")
else:
    st.caption("Aucun clip exporte pour l'instant.")
