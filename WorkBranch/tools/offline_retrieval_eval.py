from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

# Allow direct execution from repo root:
#   python tools/offline_retrieval_eval.py ...
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from rag.logging_utils import get_logger
from rag.service.RAG_service import RAG_service

LOGGER = get_logger("offline_retrieval_eval")


def _load_dataset(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")

    if path.suffix.lower() == ".jsonl":
        rows: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return rows

    if path.suffix.lower() == ".json":
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, list):
            return obj
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            return obj["items"]
        raise ValueError("JSON dataset must be a list or {\"items\": [...]}")

    raise ValueError("dataset format must be .json or .jsonl")


def _unique_preserve_order(values: Iterable[int]) -> List[int]:
    seen = set()
    out: List[int] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _first_hit_rank(doc_ids: List[int], gold: set[int]) -> int | None:
    for idx, doc_id in enumerate(doc_ids, start=1):
        if doc_id in gold:
            return idx
    return None


def run_eval(
    dataset: List[Dict[str, Any]],
    k_values: List[int],
    mode: str,
    use_rerank: bool,
    rewrite_query: bool,
) -> Dict[str, Any]:
    service = RAG_service()
    try:
        max_k = max(k_values)

        totals = {k: {"recall_hits": 0, "mrr_sum": 0.0, "precision_sum": 0.0} for k in k_values}
        failures: List[Dict[str, Any]] = []
        sample_results: List[Dict[str, Any]] = []

        valid_count = 0
        for row in dataset:
            query = str(row.get("query", "")).strip()
            gold_ids = row.get("gold_doc_ids")
            qid = row.get("qid")
            if not query or not isinstance(gold_ids, list) or not gold_ids:
                continue

            gold = {int(x) for x in gold_ids}
            valid_count += 1

            payload = {
                "query": query,
                "top_k": max_k,
                "mode": mode,
                "use_rerank": use_rerank,
                "rewrite_query": rewrite_query,
            }
            response = service.rag_search(payload)
            if not response.ok:
                failures.append(
                    {
                        "qid": qid,
                        "query": query,
                        "gold_doc_ids": sorted(gold),
                        "error": f"{response.error.code}: {response.error.message}",
                    }
                )
                continue

            predicted_doc_ids = _unique_preserve_order([int(item.doc_id) for item in response.items if int(item.doc_id) > 0])
            hit_rank = _first_hit_rank(predicted_doc_ids, gold)

            for k in k_values:
                topk_ids = predicted_doc_ids[:k]
                hits = sum(1 for d in topk_ids if d in gold)
                recall_hit = 1 if hits > 0 else 0
                totals[k]["recall_hits"] += recall_hit
                totals[k]["precision_sum"] += hits / max(k, 1)
                if hit_rank is not None and hit_rank <= k:
                    totals[k]["mrr_sum"] += 1.0 / hit_rank

            sample_results.append(
                {
                    "qid": qid,
                    "query": query,
                    "gold_doc_ids": sorted(gold),
                    "predicted_doc_ids": predicted_doc_ids,
                    "hit_rank": hit_rank,
                }
            )

            if hit_rank is None:
                failures.append(
                    {
                        "qid": qid,
                        "query": query,
                        "gold_doc_ids": sorted(gold),
                        "predicted_doc_ids": predicted_doc_ids,
                    }
                )

        metrics: Dict[str, Dict[str, float]] = {}
        for k in k_values:
            denom = max(valid_count, 1)
            metrics[f"@{k}"] = {
                "Recall": round(totals[k]["recall_hits"] / denom, 6),
                "MRR": round(totals[k]["mrr_sum"] / denom, 6),
                "Precision": round(totals[k]["precision_sum"] / denom, 6),
            }

        return {
            "count_total_rows": len(dataset),
            "count_valid_rows": valid_count,
            "config": {
                "k_values": k_values,
                "mode": mode,
                "use_rerank": use_rerank,
                "rewrite_query": rewrite_query,
            },
            "metrics": metrics,
            "failures": failures,
            "samples": sample_results[: min(50, len(sample_results))],
        }
    finally:
        service.close()


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline retrieval evaluation for RAG")
    parser.add_argument("--dataset", required=True, help="Path to eval dataset (.json or .jsonl)")
    parser.add_argument("--k", default="3,5,10", help="Comma separated k values, e.g. 3,5,10")
    parser.add_argument("--mode", choices=["dense", "hybrid"], default="hybrid")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--rewrite-query", action="store_true")
    parser.add_argument("--out", default="logs/offline_eval_result.json", help="Output summary json path")
    parser.add_argument("--failures-out", default="logs/offline_eval_failures.jsonl", help="Output failures jsonl path")
    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset).resolve()
    k_values = [int(x.strip()) for x in args.k.split(",") if x.strip()]
    k_values = sorted(set(k_values))

    dataset = _load_dataset(dataset_path)
    result = run_eval(
        dataset=dataset,
        k_values=k_values,
        mode=args.mode,
        use_rerank=not args.no_rerank,
        rewrite_query=args.rewrite_query,
    )

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    failures_path = Path(args.failures_out).resolve()
    failures_path.parent.mkdir(parents=True, exist_ok=True)
    with failures_path.open("w", encoding="utf-8") as f:
        for row in result["failures"]:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    LOGGER.info(
        "offline_eval_done dataset=%s valid=%s metrics=%s out=%s failures_out=%s",
        str(dataset_path),
        result["count_valid_rows"],
        json.dumps(result["metrics"], ensure_ascii=False),
        str(out_path),
        str(failures_path),
    )

    print(json.dumps(result["metrics"], ensure_ascii=False, indent=2))
    print(f"summary: {out_path}")
    print(f"failures: {failures_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())