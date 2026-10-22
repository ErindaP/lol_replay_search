from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np
from sklearn.decomposition import PCA

from .config import load_settings
from .query_engine import QueryEngine
from .rag import QueryInterpreter


def _json_default(value: Any) -> Any:
    try:
        return value.item()
    except AttributeError:
        return str(value)


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=_json_default))


def cmd_query(args: argparse.Namespace) -> None:
    settings = load_settings()
    engine = QueryEngine(settings.data_index_dir)
    interpreter = QueryInterpreter(openai_api_key=settings.openai_api_key)

    if args.mode == "precise":
        if args.no_kills:
            rows = engine.top_macro_no_kill(window_seconds=args.window_seconds, top_k=args.top_k)
            filters: dict[str, Any] = {"min_kills": 0, "max_kills": 0, "duration_s": args.window_seconds}
        else:
            filters = {
                "min_kills": args.min_kills,
                "max_kills": args.max_kills,
                "requires_dragon": args.requires_dragon,
                "requires_baron": args.requires_baron,
                "duration_s": args.window_seconds,
                "sort_by": args.sort_by,
                "descending": not args.ascending,
                "top_k": args.top_k,
            }
            rows = engine.filter_search(**filters)
        parsed_text = args.text or "precise filters"
    else:
        parsed = interpreter.parse_with_llm(args.text) if args.use_llm_parser else interpreter.parse(args.text)
        filters = parsed.filters
        parsed_text = parsed.semantic_text
        if filters and args.mode == "auto":
            rows = engine.filter_search(top_k=args.top_k, **filters)
        else:
            rows = engine.semantic_search(parsed.semantic_text, top_k=args.top_k)

    _print_json(
        {
            "status": "ok",
            "mode": args.mode,
            "query": args.text,
            "semantic_text": parsed_text,
            "filters": filters,
            "results": rows,
        }
    )


def _row_key(row: dict[str, Any]) -> tuple[str, int, int]:
    return (
        str(row.get("match_id", "")),
        int(row.get("start_ms", 0)),
        int(row.get("end_ms", 0)),
    )


def cmd_projection(args: argparse.Namespace) -> None:
    settings = load_settings()
    engine = QueryEngine(settings.data_index_dir)
    interpreter = QueryInterpreter(openai_api_key=settings.openai_api_key)

    rows = _resolve_projection_rows(args, engine, interpreter)
    if not rows:
        _print_json({"status": "ok", "mode": args.mode, "query": args.text, "points": [], "explained_variance": []})
        return

    index_by_key: dict[tuple[str, int, int], int] = {}
    for i, row in enumerate(engine.metadata):
        index_by_key.setdefault(_row_key(row), i)

    selected_indices: list[int] = []
    selected_rows: list[dict[str, Any]] = []
    for row in rows:
        idx = index_by_key.get(_row_key(row))
        if idx is None:
            continue
        selected_indices.append(idx)
        selected_rows.append(row)

    if not selected_indices:
        _print_json({"status": "ok", "mode": args.mode, "query": args.text, "points": [], "explained_variance": []})
        return

    vectors = engine.embeddings[np.array(selected_indices, dtype=int)].astype(np.float32)
    if vectors.shape[0] >= 2 and vectors.shape[1] >= 2:
        pca = PCA(n_components=2)
        coords = pca.fit_transform(vectors)
        explained = [float(x) for x in pca.explained_variance_ratio_]
    else:
        coords = np.zeros((vectors.shape[0], 2), dtype=np.float32)
        explained = []

    min_xy = coords.min(axis=0)
    max_xy = coords.max(axis=0)
    span = np.maximum(max_xy - min_xy, 1e-9)
    norm = (coords - min_xy) / span

    sims = vectors @ vectors.T
    points: list[dict[str, Any]] = []
    for rank, (row, idx) in enumerate(zip(selected_rows, selected_indices), start=1):
        nearest = []
        if sims.shape[0] > 1:
            order = np.argsort(-sims[rank - 1])
            for j in order:
                if int(j) == rank - 1:
                    continue
                nearest.append({"rank": int(j) + 1, "similarity": float(sims[rank - 1, int(j)])})
                if len(nearest) >= 3:
                    break

        points.append(
            {
                "id": f"{row.get('match_id')}-{row.get('start_ms')}-{row.get('end_ms')}",
                "rank": rank,
                "index": int(idx),
                "x": float(norm[rank - 1, 0]),
                "y": float(norm[rank - 1, 1]),
                "raw_x": float(coords[rank - 1, 0]),
                "raw_y": float(coords[rank - 1, 1]),
                "nearest": nearest,
                **row,
            }
        )

    _print_json(
        {
            "status": "ok",
            "mode": args.mode,
            "query": args.text,
            "points": points,
            "explained_variance": explained,
        }
    )


def _resolve_projection_rows(args: argparse.Namespace, engine: QueryEngine, interpreter: QueryInterpreter) -> list[dict[str, Any]]:
    parsed = interpreter.parse_with_llm(args.text) if args.use_llm_parser else interpreter.parse(args.text)
    filters = parsed.filters
    if args.mode == "precise":
        if args.no_kills:
            return engine.top_macro_no_kill(window_seconds=args.window_seconds, top_k=args.top_k)
        return engine.filter_search(
            min_kills=args.min_kills,
            max_kills=args.max_kills,
            requires_dragon=args.requires_dragon,
            requires_baron=args.requires_baron,
            duration_s=args.window_seconds,
            sort_by=args.sort_by,
            descending=not args.ascending,
            top_k=args.top_k,
        )
    if filters and args.mode == "auto":
        return engine.filter_search(top_k=args.top_k, **filters)
    return engine.semantic_search(parsed.semantic_text, top_k=args.top_k)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JSON API helpers for the LoL replay web app")
    sub = parser.add_subparsers(dest="command", required=True)

    p_query = sub.add_parser("query", help="Return structured query results as JSON")
    p_query.add_argument("--text", default="")
    p_query.add_argument("--mode", choices=["auto", "semantic", "precise"], default="auto")
    p_query.add_argument("--top-k", type=int, default=8)
    p_query.add_argument("--use-llm-parser", action="store_true")
    p_query.add_argument("--window-seconds", type=int, default=30)
    p_query.add_argument("--min-kills", type=int, default=None)
    p_query.add_argument("--max-kills", type=int, default=None)
    p_query.add_argument("--requires-dragon", action="store_true")
    p_query.add_argument("--requires-baron", action="store_true")
    p_query.add_argument("--sort-by", default="gold_abs_swing")
    p_query.add_argument("--ascending", action="store_true")
    p_query.add_argument("--no-kills", action="store_true")
    p_query.set_defaults(func=cmd_query)

    p_projection = sub.add_parser("projection", help="Return PCA 2D projection for query result windows")
    p_projection.add_argument("--text", default="")
    p_projection.add_argument("--mode", choices=["auto", "semantic", "precise"], default="auto")
    p_projection.add_argument("--top-k", type=int, default=20)
    p_projection.add_argument("--use-llm-parser", action="store_true")
    p_projection.add_argument("--window-seconds", type=int, default=30)
    p_projection.add_argument("--min-kills", type=int, default=None)
    p_projection.add_argument("--max-kills", type=int, default=None)
    p_projection.add_argument("--requires-dragon", action="store_true")
    p_projection.add_argument("--requires-baron", action="store_true")
    p_projection.add_argument("--sort-by", default="gold_abs_swing")
    p_projection.add_argument("--ascending", action="store_true")
    p_projection.add_argument("--no-kills", action="store_true")
    p_projection.set_defaults(func=cmd_projection)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
