from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from backend.competition.legacy_doc import LegacyDocConversionError, LegacyDocConverter


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _worker_command(script: str):
    def build(source: Path, destination: Path, pid_file: Path) -> list[str]:
        return [
            sys.executable,
            "-c",
            script,
            str(source),
            str(destination),
            str(pid_file),
        ]

    return build


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_legacy_word_worker_reuses_owned_window_pid_resolution() -> None:
    worker = (
        REPOSITORY_ROOT / "competition/convert-legacy-doc-worker.ps1"
    ).read_text(encoding="utf-8")

    assert '. (Join-Path $PSScriptRoot "word-process.ps1")' in worker
    assert "$wordPid = Get-IsolatedWordProcessId -WordApplication $word" in worker
    assert "[IntPtr]$word.Hwnd" not in worker


def test_legacy_doc_conversion_records_original_hash_without_overwriting_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.doc"
    source_bytes = b"controlled legacy bytes"
    source.write_bytes(source_bytes)
    source_mtime = source.stat().st_mtime_ns
    destination = tmp_path / "converted.docx"
    script = (
        "import os,sys,zipfile; "
        "open(sys.argv[3],'w',encoding='utf-8').write(str(os.getpid())); "
        "z=zipfile.ZipFile(sys.argv[2],'w'); "
        "z.writestr('[Content_Types].xml','<Types/>'); z.close()"
    )

    receipt = LegacyDocConverter(
        command_builder=_worker_command(script),
        timeout_seconds=5,
    ).convert(source, destination)

    assert source.read_bytes() == source_bytes
    assert source.stat().st_mtime_ns == source_mtime
    assert destination.is_file()
    assert zipfile.is_zipfile(destination)
    assert receipt["sourceSha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert receipt["outputSha256"] == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert set(receipt) == {"schemaVersion", "sourceSha256", "outputSha256"}
    saved_receipt = json.loads(
        destination.with_suffix(".conversion.json").read_text(encoding="utf-8")
    )
    assert saved_receipt == receipt


def test_legacy_doc_timeout_terminates_the_started_process_and_leaves_no_output(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"never overwrite")
    destination = tmp_path / "converted.docx"
    pid_observation = tmp_path / "observed.pid"
    script = (
        "import os,sys,time; "
        "open(sys.argv[3],'w',encoding='utf-8').write(str(os.getpid())); "
        "open(r'" + str(pid_observation).replace("\\", "\\\\") + "','w').write(str(os.getpid())); "
        "time.sleep(30)"
    )

    with pytest.raises(LegacyDocConversionError, match="超时"):
        LegacyDocConverter(
            command_builder=_worker_command(script),
            timeout_seconds=0.2,
        ).convert(source, destination)

    pid = int(pid_observation.read_text(encoding="utf-8"))
    for _ in range(50):
        if not _pid_exists(pid):
            break
        time.sleep(0.02)
    assert not _pid_exists(pid)
    assert source.read_bytes() == b"never overwrite"
    assert not destination.exists()
    assert not destination.with_suffix(".conversion.json").exists()


def test_legacy_doc_conversion_refuses_an_existing_destination_before_starting_word(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.doc"
    source.write_bytes(b"source")
    destination = tmp_path / "converted.docx"
    destination.write_bytes(b"existing")
    started = tmp_path / "started.txt"
    script = (
        "import pathlib; "
        "pathlib.Path(r'" + str(started).replace("\\", "\\\\") + "').write_text('yes')"
    )

    with pytest.raises(LegacyDocConversionError, match="已存在"):
        LegacyDocConverter(
            command_builder=_worker_command(script),
            timeout_seconds=5,
        ).convert(source, destination)

    assert destination.read_bytes() == b"existing"
    assert not started.exists()
