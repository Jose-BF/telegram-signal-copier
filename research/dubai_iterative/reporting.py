"""Immutable, compact artifacts for bounded Dubai research runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Mapping, Sequence
import uuid

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


ARTIFACT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ResearchArtifacts:
    run_card: Mapping[str, object]
    frontier: Sequence[Mapping[str, object]]
    generation_rows: Sequence[Mapping[str, object]]
    candidate_rows: Sequence[Mapping[str, object]] | pd.DataFrame | Path
    signal_rows: Sequence[Mapping[str, object]] | pd.DataFrame | Path


@dataclass(frozen=True)
class PublishedRun:
    run_id: str
    run_dir: Path


class ProvenanceConflictError(RuntimeError):
    """An immutable run directory no longer matches its recorded bytes."""


def publish_run(artifacts: ResearchArtifacts, output_root: Path) -> PublishedRun:
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    identity_payload = dict(artifacts.run_card)
    identity_payload.pop("run_id", None)
    run_id = hashlib.sha256(_canonical_bytes(identity_payload)).hexdigest()[:20]
    run_dir = output_root / run_id
    expected_text = _expected_text_files(artifacts, run_id)
    expected_tables = {
        "candidate_matrix.parquet": _expected_table_hash(
            artifacts.candidate_rows,
            output_root,
        ),
        "signal_results.parquet": _expected_table_hash(
            artifacts.signal_rows,
            output_root,
        ),
    }
    if run_dir.exists():
        _verify_existing(run_dir, expected_text, expected_tables)
        return PublishedRun(run_id, run_dir)

    temporary = output_root / f".{run_id}.{uuid.uuid4().hex}.tmp"
    try:
        (temporary / "charts").mkdir(parents=True, exist_ok=False)
        for relative, content in expected_text.items():
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        _write_table(artifacts.candidate_rows, temporary / "candidate_matrix.parquet")
        _write_table(artifacts.signal_rows, temporary / "signal_results.parquet")
        _write_charts(artifacts, temporary / "charts")
        manifest = {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "run_id": run_id,
            "files": {
                path.relative_to(temporary).as_posix(): _sha256_file(path)
                for path in sorted(temporary.rglob("*"))
                if path.is_file()
            },
        }
        (temporary / "artifact_manifest.json").write_bytes(
            _pretty_json_bytes(manifest)
        )
        try:
            os.replace(temporary, run_dir)
        except FileExistsError:
            _verify_existing(run_dir, expected_text, expected_tables)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return PublishedRun(run_id, run_dir)


def _expected_text_files(
    artifacts: ResearchArtifacts,
    run_id: str,
) -> dict[str, bytes]:
    run_card = dict(artifacts.run_card)
    run_card["run_id"] = run_id
    generation = b"".join(
        _canonical_bytes(dict(row)) + b"\n"
        for row in artifacts.generation_rows
    )
    return {
        "run_card.json": _pretty_json_bytes(run_card),
        "frontier.json": _pretty_json_bytes(list(artifacts.frontier)),
        "generation_summary.jsonl": generation,
    }


def _verify_existing(
    run_dir: Path,
    expected_text: Mapping[str, bytes],
    expected_tables: Mapping[str, str],
) -> None:
    manifest_path = run_dir / "artifact_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvenanceConflictError(
            f"artifact_manifest.json is missing or invalid: {exc}"
        ) from exc
    if manifest.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise ProvenanceConflictError("artifact_manifest.json schema mismatch")
    recorded = manifest.get("files")
    if not isinstance(recorded, dict):
        raise ProvenanceConflictError("artifact_manifest.json has no file hashes")
    for relative, expected_hash in recorded.items():
        path = run_dir / relative
        if not path.is_file() or _sha256_file(path) != expected_hash:
            raise ProvenanceConflictError(f"immutable artifact conflict: {relative}")
    for relative, expected_bytes in expected_text.items():
        path = run_dir / relative
        if not path.is_file() or path.read_bytes() != expected_bytes:
            raise ProvenanceConflictError(f"immutable artifact conflict: {relative}")
    for relative, expected_hash in expected_tables.items():
        if recorded.get(relative) != expected_hash:
            raise ProvenanceConflictError(f"immutable artifact conflict: {relative}")


def _write_charts(artifacts: ResearchArtifacts, chart_dir: Path) -> None:
    signals = _read_table(artifacts.signal_rows)
    generations = pd.DataFrame(tuple(artifacts.generation_rows))

    pnl = (
        pd.to_numeric(signals.get("pnl_eur"), errors="coerce").fillna(0.0)
        if "pnl_eur" in signals
        else pd.Series(dtype=float)
    )
    equity = pnl.cumsum()
    _line_chart(
        equity.index + 1,
        equity,
        chart_dir / "equity.png",
        title="Cumulative simulated result",
        x_label="Signal result",
        y_label="EUR",
        color="#147d64",
    )
    drawdown = equity.cummax() - equity
    _line_chart(
        drawdown.index + 1,
        drawdown,
        chart_dir / "floating_drawdown.png",
        title="Cumulative drawdown",
        x_label="Signal result",
        y_label="EUR",
        color="#c43d4b",
        fill=True,
    )
    x = (
        pd.to_numeric(generations.get("generation"), errors="coerce")
        if "generation" in generations
        else pd.Series(dtype=float)
    )
    y = (
        pd.to_numeric(generations.get("evaluated"), errors="coerce")
        if "evaluated" in generations
        else pd.Series(dtype=float)
    )
    _line_chart(
        x,
        y,
        chart_dir / "generation_progress.png",
        title="Search progress",
        x_label="Generation",
        y_label="Unique strategies evaluated",
        color="#2458a6",
    )


def _line_chart(
    x,
    y,
    path: Path,
    *,
    title: str,
    x_label: str,
    y_label: str,
    color: str,
    fill: bool = False,
) -> None:
    figure, axis = plt.subplots(figsize=(8, 4.2), constrained_layout=True)
    axis.set_title(title)
    axis.set_xlabel(x_label)
    axis.set_ylabel(y_label)
    axis.grid(True, color="#d9dee5", linewidth=0.7)
    if len(y):
        axis.plot(x, y, color=color, linewidth=2)
        if fill:
            axis.fill_between(x, y, color=color, alpha=0.18)
    else:
        axis.text(0.5, 0.5, "No numerical rows", ha="center", va="center", transform=axis.transAxes)
    figure.savefig(path, dpi=130, metadata={"Software": "telegram-signal-copier"})
    plt.close(figure)


def _write_table(source, target: Path) -> None:
    if isinstance(source, Path):
        if not source.is_file():
            raise FileNotFoundError(source)
        shutil.copyfile(source, target)
        return
    frame = source.copy() if isinstance(source, pd.DataFrame) else pd.DataFrame(tuple(source))
    frame.to_parquet(target, index=False)


def _read_table(source) -> pd.DataFrame:
    if isinstance(source, Path):
        return pd.read_parquet(source)
    return source.copy() if isinstance(source, pd.DataFrame) else pd.DataFrame(tuple(source))


def _expected_table_hash(source, directory: Path) -> str:
    if isinstance(source, Path):
        if not source.is_file():
            raise FileNotFoundError(source)
        return _sha256_file(source)
    handle_path = directory / f".table-hash-{uuid.uuid4().hex}.parquet"
    try:
        _write_table(source, handle_path)
        return _sha256_file(handle_path)
    finally:
        handle_path.unlink(missing_ok=True)


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        ensure_ascii=True,
    ).encode("utf-8")


def _pretty_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            allow_nan=False,
            ensure_ascii=True,
        )
        + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
