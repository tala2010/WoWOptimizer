from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any




RELEASE_ID = "hub-0.5.49-native-guard"
RESTORABLE_RELEASE_IDS = frozenset({
    # Keep historical IDs explicit. Never accept an arbitrary ledger release.
    "hub-0.5.11-wa-v18-layout1",
    "hub-0.5.14-wa-v20-private-lgf1",
    "hub-0.5.14-wa-v21-private-lgf2",
    "hub-0.5.15-wa-v22-private-lgf3",
    "hub-0.5.16-wa-v22-private-lgf3",
    "hub-0.5.21-signed-runtime-grant",
    "hub-0.5.22-signed-runtime-grant",
    "hub-0.5.23-vf-decision-cache",
    "hub-0.5.24-vf-shared-env-scope",
    "hub-0.5.25-vf-apl-registration-r2",
    "hub-0.5.26-vf-apl-environment",
    "hub-0.5.27-vf-assignment-bridge",
    "hub-0.5.28-vf-common-channel",
    "hub-0.5.29-vf-negative-aura-cache",
    "hub-0.5.30-vf-negative-aura-adaptive",
    "hub-0.5.31-vf-allocation-reuse",
    "hub-0.5.32-wa-exact-aura-demand",
    "hub-0.5.33-wa-cleu-aura-visibility",
    "hub-0.5.35-wa-aura-runtime-fastpath",
    "hub-0.5.36-wa-incremental-aura-gate",
    "hub-0.5.37-wa-vf-fallback-lean-runtime",
    "hub-0.5.38-wa-vf-init-registration-lean",
    "hub-0.5.39-wa-active-inventory-fast-cleu",
    "hub-0.5.40-wa-single-trigger-cleu",
    "hub-0.5.41-wa-irrelevant-removal-cache",
    "hub-0.5.42-wa-source-player-cleu",
    "hub-0.5.43-integrated-exact-cleu-routing",
    "hub-0.5.44-wa-known-cleu-relief",
    "hub-0.5.45-wa-static-progress-text-cache",
    "hub-0.5.46-wa-protected-layout-fix",
    "hub-0.5.47-wa-functional-native-restore",
    "hub-0.5.65-titan-wa-5219-exact-demand-local-r1",
    "hub-0.5.66-titan-wa-52111-three-file-local-r1",
    RELEASE_ID,
})
_BUILTIN_RESTORABLE_RELEASE_IDS = RESTORABLE_RELEASE_IDS

# These payloads contain only line-splice operations between the known clean
# Lulu addon files and the approved shared-input bridge. They do not contain
# complete third-party addon files. Every input and output is pinned by SHA-256;
# an unknown file makes its whole component use the native path.
# The concrete WA/Plater compatibility profile is release data, not trusted
# installer code. Source tests and the offline publisher read it from assets; a
# frozen customer EXE receives it only after signed cloud verification.
_BUILTIN_PROFILE_FILE = Path(__file__).resolve().parents[1] / 'assets' / 'approved_hub_release_profile.json'


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_profile():
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return json.loads((base / "assets/approved_hub_release_profile.json").read_text(encoding="utf-8"))


_PROFILE = _load_profile()
_ACTIVE_RELEASE_RECEIPT: dict[str, Any] | None = None


def _validate_release_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("approved release profile must be an object")
    release_id = str(profile.get("release_id", "")).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", release_id):
        raise ValueError("approved release id is invalid")
    components = profile.get("components")
    if not isinstance(components, dict) or set(components) != {
        "weakauras", "plater", "ndui"
    }:
        raise ValueError("approved release components are not allowed")
    allowed_addons = {
        "weakauras": "WeakAuras",
        "plater": "Plater",
        "ndui": "NDui",
    }
    file_count = 0
    for name, component in components.items():
        if not isinstance(component, dict) or component.get("addon") != allowed_addons[name]:
            raise ValueError(f"approved release addon is invalid: {name}")
        files = component.get("files")
        if not isinstance(files, list) or len(files) > 64:
            raise ValueError(f"approved release file list is invalid: {name}")
        for spec in files:
            file_count += 1
            if not isinstance(spec, dict):
                raise ValueError("approved release file spec is invalid")
            relative = str(spec.get("path", ""))
            path = Path(relative)
            if (
                not relative
                or path.is_absolute()
                or ".." in path.parts
                or path.suffix.lower() not in {".lua", ".xml", ".toc"}
            ):
                raise ValueError(f"approved release path is invalid: {relative}")
            for field in ("clean_sha256", "approved_sha256"):
                if not re.fullmatch(r"[0-9a-f]{64}", str(spec.get(field, ""))):
                    raise ValueError(f"approved release hash is invalid: {relative}")
            ops = spec.get("ops")
            if not isinstance(ops, list) or len(ops) > 128:
                raise ValueError(f"approved release operations are invalid: {relative}")
            lineage = spec.get("trusted_lineage_sha256s", [])
            if (
                not isinstance(lineage, list)
                or len(lineage) > 32
                or any(
                    not re.fullmatch(r"[0-9a-f]{64}", str(value))
                    for value in lineage
                )
            ):
                raise ValueError(f"approved release lineage is invalid: {relative}")
    if file_count <= 0:
        raise ValueError("approved release profile is empty")
    # Round-trip to detach all mutable values from the caller.
    return json.loads(json.dumps(profile, ensure_ascii=False))


def configure_release_profile(profile, release_envelope=None, profile_sha256=""):
    validated = _validate_release_profile(profile)
    global _PROFILE, RELEASE_ID, RESTORABLE_RELEASE_IDS
    _PROFILE = validated
    RELEASE_ID = validated["release_id"]
    RESTORABLE_RELEASE_IDS = frozenset(set(_BUILTIN_RESTORABLE_RELEASE_IDS) | {RELEASE_ID})
    return RELEASE_ID


def _cloud_receipt_allows_release(receipt, release_id):
    # Public edition trusts only its explicit shipped release IDs and hashes.
    return False


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    _atomic_write_bytes(path, data)


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return dict(default or {})
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"invalid JSON object: {path}")
    return value


def _installation_id(addons_dir: Path) -> str:
    normalized = os.path.normcase(str(addons_dir.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def _upgrade_sources(spec: dict[str, Any]) -> list[dict[str, Any]]:
    sources = spec.get("upgrade_sources", [])
    if not isinstance(sources, list):
        raise ValueError(f"upgrade_sources must be a list for {spec['path']}")
    seen: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError(f"invalid upgrade source for {spec['path']}")
        required = {
            "source_sha256",
            "newline_mode",
            "trailing_newline",
            "ops",
        }
        if set(source) != required:
            raise ValueError(f"invalid upgrade source fields for {spec['path']}")
        source_hash = source["source_sha256"]
        if not isinstance(source_hash, str) or len(source_hash) != 64:
            raise ValueError(f"invalid upgrade source hash for {spec['path']}")
        if source_hash in seen:
            raise ValueError(f"duplicate upgrade source hash for {spec['path']}")
        seen.add(source_hash)
    return sources


def _source_variant(
    spec: dict[str, Any], source_hash: str
) -> tuple[str, dict[str, Any]] | None:
    if source_hash == spec["clean_sha256"]:
        return "clean", spec
    if source_hash == spec.get("base_approved_sha256"):
        return "base-approved", {}
    for source in _upgrade_sources(spec):
        if source_hash == source["source_sha256"]:
            return "upgrade", source
    return None


def _owned_approved_hashes(spec: dict[str, Any]) -> set[str]:
    hashes = {
        spec["approved_sha256"],
        *(source["source_sha256"] for source in _upgrade_sources(spec)),
        *(str(value) for value in spec.get("trusted_lineage_sha256s", [])),
    }
    if spec.get("base_approved_sha256"):
        hashes.add(spec["base_approved_sha256"])
    return hashes


def _ledger_release_is_trusted(ledger: dict[str, Any]) -> bool:
    release_id = str(ledger.get("release_id", ""))
    return (
        release_id in _BUILTIN_RESTORABLE_RELEASE_IDS
        or _cloud_receipt_allows_release(
            ledger.get("cloud_release_receipt"), release_id
        )
    )


def _profile_target_keys(addons_dir: Path) -> set[str]:
    targets: set[str] = set()
    for component in _PROFILE.get("components", {}).values():
        addon_root = addons_dir / component["addon"]
        for spec in component.get("files", []):
            target = (addon_root / Path(spec["path"])).resolve()
            targets.add(os.path.normcase(str(target)))
    return targets


def _plan_retired_managed_files(
    *,
    addons_dir: Path,
    state_dir: Path,
    first_root: Path,
    first_files: dict[str, Any],
    previous_ledger: dict[str, Any],
    current_target_keys: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Restore exact old approved files that the new profile no longer owns.

    A release upgrade must not leave omitted layout/frame patches installed.
    The previous ledger and immutable first-original store are the authority:
    only an exact hash that we previously wrote may be restored.  Missing,
    already-native, or externally changed files are never written.
    """
    managed = previous_ledger.get("managed_files", [])
    if not isinstance(managed, list):
        raise ValueError("previous managed_files ledger is invalid")

    omitted = []
    for item in managed:
        if not isinstance(item, dict):
            raise ValueError("previous managed file entry is invalid")
        target_value = item.get("target")
        if not isinstance(target_value, str) or not target_value:
            raise ValueError("previous managed file target is invalid")
        target = Path(target_value).resolve()
        if os.path.normcase(str(target)) not in current_target_keys:
            omitted.append((item, target))

    if not omitted:
        return [], []
    if not _ledger_release_is_trusted(previous_ledger):
        raise ValueError(
            "previous release is not trusted; omitted managed files cannot be retired"
        )

    changes: list[dict[str, Any]] = []
    outcomes: list[dict[str, str]] = []
    seen_targets: set[str] = set()
    addons_root = addons_dir.resolve()
    for item, target in omitted:
        normalized = os.path.normcase(str(target))
        if normalized in seen_targets:
            raise ValueError(f"duplicate previous managed target: {target}")
        seen_targets.add(normalized)
        if addons_root not in target.parents:
            raise ValueError(f"previous managed target escaped AddOns: {target}")

        first_key = item.get("first_key")
        approved_hash = item.get("approved_sha256")
        if not isinstance(first_key, str) or not first_key:
            raise ValueError(f"previous first_original key is invalid: {target}")
        key_parts = first_key.split("/", 1)
        if (
            len(key_parts) != 2
            or not key_parts[0]
            or not key_parts[1]
            or (addons_dir / Path(first_key)).resolve() != target
        ):
            raise ValueError(f"previous first_original key does not match target: {target}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(approved_hash or "")):
            raise ValueError(f"previous approved hash is invalid: {target}")
        record = first_files.get(first_key)
        if not isinstance(record, dict):
            raise ValueError(f"immutable first_original is missing: {first_key}")
        clean_hash = record.get("sha256")
        first_approved_hash = record.get("approved_sha256")
        if not re.fullmatch(r"[0-9a-f]{64}", str(clean_hash or "")):
            raise ValueError(f"first_original hash is invalid: {first_key}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(first_approved_hash or "")):
            raise ValueError(f"first_original approval hash is invalid: {first_key}")
        _, backup = _verified_first_original(
            state_dir=state_dir,
            first_root=first_root,
            first_files=first_files,
            key=first_key,
            target=target,
            clean_sha256=clean_hash,
            # The immutable record pins the first approved hash while the
            # previous trusted ledger pins the latest hash after one or more
            # approved upgrades. Both belong to the same owned lineage.
            owned_approved_sha256s={approved_hash, first_approved_hash},
        )
        addon, relative = key_parts

        if not target.exists():
            outcomes.append({"addon": addon, "status": "missing", "target": str(target)})
            continue
        if not target.is_file():
            outcomes.append({"addon": addon, "status": "external", "target": str(target)})
            continue
        current_hash = _sha256_file(target)
        if current_hash == clean_hash:
            outcomes.append({"addon": addon, "status": "already-native", "target": str(target)})
            continue
        if current_hash != approved_hash:
            outcomes.append({"addon": addon, "status": "external", "target": str(target)})
            continue

        changes.append({
            "component": "retired-managed",
            "addon": addon,
            "target": target,
            "relative": relative,
            "before": target.read_bytes(),
            "before_sha256": current_hash,
            "target_existed": True,
            "after": backup.read_bytes(),
            "after_sha256": clean_hash,
            "action": "retire-to-native",
        })
        outcomes.append({"addon": addon, "status": "retired", "target": str(target)})
    return changes, outcomes


def _apply_line_ops(
    source: bytes,
    ops: list[Any],
    newline_mode: str,
    trailing_newline: bool,
) -> bytes:
    text = source.decode("utf-8-sig")
    preserve_newlines = newline_mode == "preserve"
    lines = text.splitlines(keepends=preserve_newlines)
    for start, end, replacement in reversed(ops):
        lines[int(start):int(end)] = list(replacement)
    if preserve_newlines:
        text = "".join(lines)
    else:
        text = "\n".join(lines)
        if trailing_newline:
            text += "\n"
    return text.encode("utf-8")


def _render_approved(source: bytes, spec: dict[str, Any]) -> bytes:
    source_hash = _sha256_bytes(source)
    variant = _source_variant(spec, source_hash)
    if variant is None:
        raise ValueError(f"unsupported source hash for {spec['path']}: {source_hash}")
    variant_name, source_spec = variant
    if variant_name == "base-approved":
        result = source
    else:
        result = _apply_line_ops(
            source,
            source_spec["ops"],
            source_spec.get("newline_mode", "lf"),
            bool(source_spec.get("trailing_newline")),
        )
    if spec.get("post_ops") is not None:
        result = _apply_line_ops(
            result,
            spec["post_ops"],
            spec.get("post_newline_mode", "lf"),
            bool(spec.get("post_trailing_newline")),
        )
    actual = _sha256_bytes(result)
    if actual != spec["approved_sha256"]:
        raise ValueError(
            f"approved output hash mismatch for {spec['path']}: "
            f"expected {spec['approved_sha256']}, got {actual}"
        )
    return result


def _component_plan(addons_dir: Path, name: str, component: dict[str, Any]) -> dict[str, Any]:
    addon_name = component["addon"]
    addon_root = addons_dir / addon_name
    specs = list(component.get("files", []))
    result: dict[str, Any] = {
        "name": name,
        "addon": addon_name,
        "root": addon_root,
        "state": "missing",
        "changes": [],
        "files": [],
        "unknown": [],
    }
    if not specs or not addon_root.is_dir():
        return result

    for spec in specs:
        target = addon_root / Path(spec["path"])
        if not target.is_file():
            result["files"].append({
                "path": str(target),
                "relative": spec["path"],
                "current_sha256": None,
                "clean_sha256": spec["clean_sha256"],
                "approved_sha256": spec["approved_sha256"],
                "owned_approved_sha256s": sorted(_owned_approved_hashes(spec)),
                "state": "missing",
            })
            result["unknown"].append({"path": str(target), "reason": "missing"})
            continue
        source = target.read_bytes()
        current_hash = _sha256_bytes(source)
        file_state = {
            "path": str(target),
            "relative": spec["path"],
            "current_sha256": current_hash,
            "clean_sha256": spec["clean_sha256"],
            "approved_sha256": spec["approved_sha256"],
            "owned_approved_sha256s": sorted(_owned_approved_hashes(spec)),
        }
        result["files"].append(file_state)
        if current_hash == spec["approved_sha256"]:
            file_state["state"] = "approved"
        else:
            variant = _source_variant(spec, current_hash)
            if variant is None:
                file_state["state"] = "unknown"
                result["unknown"].append({
                    "path": str(target),
                    "reason": "hash",
                    "sha256": current_hash,
                })
                continue
            source_kind, _ = variant
            file_state["state"] = source_kind
            result["changes"].append({
                "component": name,
                "addon": addon_name,
                "target": target,
                "relative": spec["path"],
                "before": source,
                "before_sha256": current_hash,
                "after": _render_approved(source, spec),
                "after_sha256": spec["approved_sha256"],
                "clean_sha256": spec["clean_sha256"],
                "owned_approved_sha256s": sorted(_owned_approved_hashes(spec)),
                "requires_first_original": source_kind == "upgrade",
            })

    if result["unknown"]:
        # The component is atomic: never apply its otherwise-compatible files.
        result["changes"] = []
        result["state"] = "native-fallback"
    elif result["changes"]:
        result["state"] = "apply"
    else:
        result["state"] = "approved"
    return result


def _result(
    *,
    ok: bool,
    mode: str,
    changed: int,
    skipped: int,
    message: str,
    ledger_path: Path,
    components: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "release_id": RELEASE_ID,
        "mode": mode,
        "changed": int(changed),
        "skipped": int(skipped),
        "message": message,
        "ledger_path": str(ledger_path),
        "components": components or [],
    }


def _verified_first_original(
    *,
    state_dir: Path,
    first_root: Path,
    first_files: dict[str, Any],
    key: str,
    target: Path,
    clean_sha256: str,
    owned_approved_sha256s: set[str],
) -> tuple[dict[str, Any], Path]:
    record = first_files.get(key)
    if not isinstance(record, dict):
        raise ValueError(f"immutable first_original is missing: {key}")

    recorded_target = record.get("target")
    if (
        not isinstance(recorded_target, str)
        or Path(recorded_target).resolve() != target.resolve()
    ):
        raise ValueError(f"first_original target does not match owned file: {key}")
    if record.get("sha256") != clean_sha256:
        raise ValueError(f"first_original is not the pinned native file: {key}")
    if record.get("approved_sha256") not in owned_approved_sha256s:
        raise ValueError(f"first_original approval lineage is not recognized: {key}")

    backup_value = record.get("backup")
    if not isinstance(backup_value, str) or not backup_value:
        raise ValueError(f"first_original backup path is invalid: {key}")
    backup = (state_dir / backup_value).resolve()
    if first_root.resolve() not in backup.parents:
        raise ValueError(f"first_original backup escaped immutable root: {key}")
    if not backup.is_file() or _sha256_file(backup) != clean_sha256:
        raise ValueError(f"first_original backup is missing or corrupt: {key}")
    return record, backup


def apply_release(addons_dir: Path, ledger_base: Path) -> dict[str, Any]:
    """Apply the pinned approved addon bridge transaction.

    Each addon component is all-or-nothing. Unknown files are left byte-for-
    byte untouched. If the same component was only partly patched by an older
    run, every owned approved file is transactionally restored to its immutable
    first original before the component is reported as native fallback.
    """
    addons_dir = Path(addons_dir).resolve()
    ledger_base = Path(ledger_base).resolve()
    state_dir = ledger_base / "approved_hub_release" / _installation_id(addons_dir)
    ledger_path = state_dir / "release.json"
    first_root = state_dir / "first_original"
    first_manifest_path = state_dir / "first_original.json"
    transaction_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid.uuid4().hex[:8]
    transaction_dir = state_dir / "transactions" / transaction_id
    journal_path = transaction_dir / "journal.json"

    component_plans: list[dict[str, Any]] = []
    try:
        if not addons_dir.is_dir():
            return _result(
                ok=False,
                mode="native-fallback",
                changed=0,
                skipped=0,
                message=f"AddOns directory not found: {addons_dir}",
                ledger_path=ledger_path,
            )
        for name, component in _PROFILE.get("components", {}).items():
            component_plans.append(_component_plan(addons_dir, name, component))
    except Exception as exc:
        return _result(
            ok=False,
            mode="native-fallback",
            changed=0,
            skipped=0,
            message=f"approved release preflight failed; no files changed: {exc}",
            ledger_path=ledger_path,
        )

    apply_changes = [
        change for plan in component_plans for change in plan["changes"]
    ]
    skipped = sum(
        len(_PROFILE["components"][plan["name"]].get("files", []))
        for plan in component_plans
        if plan["state"] in {"missing", "native-fallback"}
    )
    component_summary = [
        {
            "name": plan["name"],
            "addon": plan["addon"],
            "state": plan["state"],
            "changed": len(plan["changes"]),
            "restored": 0,
            "retired": 0,
            "retire_skipped": 0,
            "retire_unsafe": 0,
            "unknown": plan["unknown"],
        }
        for plan in component_plans
    ]

    first_manifest: dict[str, Any]
    retired_changes: list[dict[str, Any]] = []
    retired_outcomes: list[dict[str, str]] = []
    previous_ledger: dict[str, Any] = {}
    current_target_keys = _profile_target_keys(addons_dir)
    try:
        first_manifest = _read_json(first_manifest_path, {
            "release_id": RELEASE_ID,
            "installation_id": _installation_id(addons_dir),
            "created_at": _utc_now(),
            "files": {},
        })
        first_files = first_manifest.setdefault("files", {})
        if not isinstance(first_files, dict):
            raise ValueError("first_original files ledger is invalid")

        # Validate every existing first backup before any target write.
        for key, record in first_files.items():
            backup = state_dir / record["backup"]
            expected = record["sha256"]
            if not backup.is_file() or _sha256_file(backup) != expected:
                raise ValueError(f"first_original backup is missing or corrupt: {key}")

        previous_ledger = _read_json(ledger_path)
        retired_changes, retired_outcomes = _plan_retired_managed_files(
            addons_dir=addons_dir,
            state_dir=state_dir,
            first_root=first_root,
            first_files=first_files,
            previous_ledger=previous_ledger,
            current_target_keys=current_target_keys,
        )

        for change in apply_changes:
            key = f"{change['addon']}/{Path(change['relative']).as_posix()}"
            if key in first_files:
                try:
                    _verified_first_original(
                        state_dir=state_dir,
                        first_root=first_root,
                        first_files=first_files,
                        key=key,
                        target=change["target"],
                        clean_sha256=change["clean_sha256"],
                        owned_approved_sha256s=set(change["owned_approved_sha256s"]),
                    )
                    continue
                except ValueError:
                    # A user may update an addon back to the exact native
                    # version after an older product release captured a now
                    # obsolete baseline.  That clean file is a safe new
                    # restore point; unknown or approved-patched files never
                    # enter this branch.
                    if (
                        change["requires_first_original"]
                        or not change["target"].is_file()
                        or _sha256_file(change["target"])
                        != change["clean_sha256"]
                    ):
                        raise
                    backup = (
                        first_root
                        / "rebased"
                        / transaction_id
                        / change["addon"]
                        / Path(change["relative"])
                    )
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(change["target"], backup)
                    if _sha256_file(backup) != change["clean_sha256"]:
                        raise ValueError(f"rebased first_original verification failed: {key}")
                    first_files[key] = {
                        "target": str(change["target"]),
                        "backup": str(backup.relative_to(state_dir)),
                        "sha256": change["clean_sha256"],
                        "approved_sha256": change["after_sha256"],
                        "captured_at": _utc_now(),
                        "rebased_from_stale_baseline": True,
                    }
                    _atomic_write_json(first_manifest_path, first_manifest)
                    continue
            if change["requires_first_original"]:
                raise ValueError(
                    "upgrade source cannot be trusted without an existing "
                    f"immutable first_original: {key}"
                )
            backup = first_root / change["addon"] / Path(change["relative"])
            if backup.exists():
                raise ValueError(f"untracked first_original already exists: {backup}")
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(change["target"], backup)
            if _sha256_file(backup) != change["before_sha256"]:
                raise ValueError(f"first_original verification failed: {backup}")
            first_files[key] = {
                "target": str(change["target"]),
                "backup": str(backup.relative_to(state_dir)),
                "sha256": change["before_sha256"],
                "approved_sha256": change["after_sha256"],
                "captured_at": _utc_now(),
            }
            # Commit each first backup independently; a later run never replaces it.
            _atomic_write_json(first_manifest_path, first_manifest)

        # An unknown file makes its component native-only. If another file in
        # that component is still at our approved hash, leaving it in place
        # would be a half-installed bridge. Only an immutable, hash-pinned first
        # original may be used to undo that owned patch.
        native_restore_changes: list[dict[str, Any]] = []
        for plan in component_plans:
            plan["native_restore_changes"] = []
            if plan["state"] != "native-fallback":
                continue
            for file_state in plan["files"]:
                if file_state.get("state") not in {"approved", "upgrade", "missing"}:
                    continue
                key = f"{plan['addon']}/{Path(file_state['relative']).as_posix()}"
                if file_state.get("state") == "missing" and key not in first_files:
                    # A file that was already absent before this product ever
                    # managed the component has no trustworthy source to restore.
                    continue
                target = Path(file_state["path"]).resolve()
                _, backup = _verified_first_original(
                    state_dir=state_dir,
                    first_root=first_root,
                    first_files=first_files,
                    key=key,
                    target=target,
                    clean_sha256=file_state["clean_sha256"],
                    owned_approved_sha256s=set(
                        file_state["owned_approved_sha256s"]
                    ),
                )
                target_existed = file_state.get("state") != "missing"
                if target_existed:
                    if (
                        not target.is_file()
                        or _sha256_file(target) != file_state["current_sha256"]
                    ):
                        raise ValueError(
                            f"approved file changed during native fallback preflight: {key}"
                        )
                    before = target.read_bytes()
                else:
                    if target.exists():
                        raise ValueError(
                            f"missing file appeared during native fallback preflight: {key}"
                        )
                    before = b""
                change = {
                    "component": plan["name"],
                    "addon": plan["addon"],
                    "target": target,
                    "relative": file_state["relative"],
                    "before": before,
                    "before_sha256": file_state["current_sha256"],
                    "target_existed": target_existed,
                    "after": backup.read_bytes(),
                    "after_sha256": file_state["clean_sha256"],
                    "action": "restore-native" if target_existed else "restore-native-missing",
                }
                plan["native_restore_changes"].append(change)
                native_restore_changes.append(change)
    except Exception as exc:
        return _result(
            ok=False,
            mode="native-fallback",
            changed=0,
            skipped=skipped,
            message=f"first_original preparation failed; no target files changed: {exc}",
            ledger_path=ledger_path,
            components=component_summary,
        )

    changes = apply_changes + native_restore_changes + retired_changes
    for summary, plan in zip(component_summary, component_plans):
        summary["restored"] = len(plan.get("native_restore_changes", []))
    for outcome in retired_outcomes:
        summary = next(
            (
                item for item in component_summary
                if item["addon"] == outcome["addon"]
            ),
            None,
        )
        if summary is None:
            continue
        if outcome["status"] == "retired":
            summary["retired"] += 1
        elif outcome["status"] == "external":
            summary["retire_unsafe"] += 1
        else:
            summary["retire_skipped"] += 1

    journal: dict[str, Any] = {
        "release_id": RELEASE_ID,
        "transaction_id": transaction_id,
        "created_at": _utc_now(),
        "state": "prepared",
        "files": [],
    }
    applied: list[dict[str, Any]] = []
    try:
        for index, change in enumerate(changes):
            before_path = transaction_dir / "before" / change["addon"] / Path(change["relative"])
            target_existed = change.get("target_existed", True)
            before_value = ""
            if target_existed:
                before_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(change["target"], before_path)
                if _sha256_file(before_path) != change["before_sha256"]:
                    raise ValueError(f"transaction backup verification failed: {before_path}")
                before_value = str(before_path)
            elif change["target"].exists():
                raise ValueError(f"missing transaction target unexpectedly exists: {change['target']}")
            journal["files"].append({
                "order": index,
                "target": str(change["target"]),
                "before": before_value,
                "before_sha256": change["before_sha256"],
                "target_existed": target_existed,
                "approved_sha256": change["after_sha256"],
                "action": change.get("action", "apply-approved"),
            })
        _atomic_write_json(journal_path, journal)

        for change in changes:
            # Register the target before the write. If a filesystem wrapper
            # raises after replacing the file, rollback still owns that write.
            applied.append(change)
            _atomic_write_bytes(change["target"], change["after"])
            if _sha256_file(change["target"]) != change["after_sha256"]:
                raise ValueError(f"post-write verification failed: {change['target']}")

        journal["state"] = "committed"
        journal["committed_at"] = _utc_now()
        _atomic_write_json(journal_path, journal)
    except Exception as exc:
        rollback_errors: list[str] = []
        for change in reversed(applied):
            try:
                if change.get("target_existed", True):
                    _atomic_write_bytes(change["target"], change["before"])
                    if _sha256_file(change["target"]) != change["before_sha256"]:
                        raise ValueError("rollback hash mismatch")
                else:
                    change["target"].unlink(missing_ok=True)
                    if change["target"].exists():
                        raise ValueError("rollback could not remove created target")
            except Exception as rollback_exc:
                rollback_errors.append(f"{change['target']}: {rollback_exc}")
        journal["state"] = "rollback-incomplete" if rollback_errors else "rolled-back"
        journal["failed_at"] = _utc_now()
        journal["error"] = str(exc)
        journal["rollback_errors"] = rollback_errors
        try:
            _atomic_write_json(journal_path, journal)
        except Exception:
            pass
        detail = f"apply failed and changed files were rolled back: {exc}"
        if rollback_errors:
            detail += "; rollback errors: " + "; ".join(rollback_errors)
        return _result(
            # An apply failure is never reported as success, even when the
            # safety rollback completed perfectly.
            ok=False,
            mode="native-fallback",
            changed=0,
            skipped=skipped,
            message=detail,
            ledger_path=ledger_path,
            components=component_summary,
        )

    mode = "applied" if apply_changes or retired_changes else (
        "native-fallback"
        if any(plan["state"] == "native-fallback" for plan in component_plans)
        else "already"
        if any(plan["state"] == "approved" for plan in component_plans)
        else "native-fallback"
    )
    fallback_target_keys = {
        os.path.normcase(
            str((plan["root"] / Path(spec["path"])).resolve())
        )
        for plan in component_plans
        if plan["state"] == "native-fallback"
        for spec in _PROFILE["components"][plan["name"]].get("files", [])
    }
    ledger = {
        "release_id": RELEASE_ID,
        "installation_id": _installation_id(addons_dir),
        "addons_dir": str(addons_dir),
        "updated_at": _utc_now(),
        "mode": mode,
        "last_transaction": str(journal_path),
        "components": component_summary,
        "managed_files": [
            {
                "target": str(change["target"]),
                "approved_sha256": change["after_sha256"],
                "first_key": f"{change['addon']}/{Path(change['relative']).as_posix()}",
            }
            for change in apply_changes
        ],
    }
    if _ACTIVE_RELEASE_RECEIPT:
        ledger["cloud_release_receipt"] = _ACTIVE_RELEASE_RECEIPT
    # Preserve only files that the current profile still owns. Omitted files
    # were either retired to native above or left untouched because another
    # updater/user changed them; neither case belongs in the new release ledger.
    try:
        previous = previous_ledger or _read_json(ledger_path)
        if (
            "cloud_release_receipt" not in ledger
            and previous.get("release_id") == RELEASE_ID
            and isinstance(previous.get("cloud_release_receipt"), dict)
        ):
            ledger["cloud_release_receipt"] = previous["cloud_release_receipt"]
        merged = {}
        for item in list(previous.get("managed_files", [])) + ledger["managed_files"]:
            if not isinstance(item, dict) or not item.get("target"):
                continue
            target_key = os.path.normcase(str(Path(item["target"]).resolve()))
            if (
                target_key not in current_target_keys
                or target_key in fallback_target_keys
            ):
                continue
            merged[item["target"]] = item
        ledger["managed_files"] = list(merged.values())
    except Exception:
        pass
    try:
        _atomic_write_json(ledger_path, ledger)
    except Exception as exc:
        rollback_errors: list[str] = []
        for change in reversed(applied):
            try:
                if change.get("target_existed", True):
                    _atomic_write_bytes(change["target"], change["before"])
                    if _sha256_file(change["target"]) != change["before_sha256"]:
                        raise ValueError("rollback hash mismatch")
                else:
                    change["target"].unlink(missing_ok=True)
                    if change["target"].exists():
                        raise ValueError("rollback could not remove created target")
            except Exception as rollback_exc:
                rollback_errors.append(f"{change['target']}: {rollback_exc}")
        journal["state"] = "rollback-incomplete" if rollback_errors else "rolled-back"
        journal["failed_at"] = _utc_now()
        journal["error"] = f"release summary ledger write failed: {exc}"
        journal["rollback_errors"] = rollback_errors
        try:
            _atomic_write_json(journal_path, journal)
        except Exception:
            pass
        detail = f"release summary ledger write failed; target files rolled back: {exc}"
        if rollback_errors:
            detail += "; rollback errors: " + "; ".join(rollback_errors)
        return _result(
            ok=False,
            mode="native-fallback",
            changed=0,
            skipped=skipped,
            message=detail,
            ledger_path=ledger_path,
            components=component_summary,
        )

    labels = ", ".join(f"{item['addon']}={item['state']}" for item in component_summary)
    return _result(
        ok=True,
        mode=mode,
        changed=len(changes),
        skipped=skipped,
        message=(
            f"{RELEASE_ID}: {labels}; applied {len(apply_changes)}, "
            f"restored {len(native_restore_changes)}, "
            f"retired {len(retired_changes)}, "
            f"retire-unsafe {sum(1 for item in retired_outcomes if item['status'] == 'external')}, "
            f"skipped {skipped}"
        ),
        ledger_path=ledger_path,
        components=component_summary,
    )


def restore_release(ledger_path: Path) -> dict[str, Any]:
    """Restore owned files to their immutable first_original copies.

    Externally updated files are never overwritten. This function intentionally
    does not remove RaidOptimizerHub; callers may only remove it after every
    dependent file was restored safely.
    """
    ledger_path = Path(ledger_path).resolve()
    try:
        ledger = _read_json(ledger_path)
        ledger_release_id = str(ledger.get("release_id", ""))
        if (
            ledger_release_id not in _BUILTIN_RESTORABLE_RELEASE_IDS
            and not _cloud_receipt_allows_release(
                ledger.get("cloud_release_receipt"), ledger_release_id
            )
        ):
            raise ValueError("release id does not match the approved release")
        state_dir = ledger_path.parent
        first_manifest = _read_json(state_dir / "first_original.json")
        first_files = first_manifest.get("files", {})
        if not isinstance(first_files, dict):
            raise ValueError("first_original files ledger is invalid")
    except Exception as exc:
        return {
            "ok": False,
            "release_id": str(locals().get("ledger_release_id", RELEASE_ID)),
            "restored": 0,
            "skipped": 0,
            "unsafe": 0,
            "message": f"restore ledger could not be read: {exc}",
            "ledger_path": str(ledger_path),
        }

    restored = 0
    skipped = 0
    unsafe = 0
    errors: list[str] = []
    for item in reversed(list(ledger.get("managed_files", []))):
        if not isinstance(item, dict):
            continue
        record = first_files.get(item.get("first_key"))
        target_value = item.get("target")
        if not record or not isinstance(target_value, str) or not target_value:
            skipped += 1
            continue
        target = Path(target_value)
        backup = state_dir / record["backup"]
        try:
            if not backup.is_file() or _sha256_file(backup) != record["sha256"]:
                unsafe += 1
                errors.append(f"first_original corrupt: {backup}")
                continue
            if target.is_file():
                current_hash = _sha256_file(target)
                if current_hash == record["sha256"]:
                    # Idempotent restore: it is already at our immutable first
                    # original, so this is safe and needs no rewrite.
                    skipped += 1
                    continue
                if current_hash != item["approved_sha256"]:
                    skipped += 1
                    unsafe += 1
                    continue
            _atomic_write_bytes(target, backup.read_bytes())
            if _sha256_file(target) != record["sha256"]:
                raise ValueError("restored hash mismatch")
            restored += 1
        except Exception as exc:
            unsafe += 1
            errors.append(f"{target}: {exc}")

    ledger["restore"] = {
        "at": _utc_now(),
        "restored": restored,
        "skipped": skipped,
        "unsafe": unsafe,
    }
    try:
        _atomic_write_json(ledger_path, ledger)
    except Exception as exc:
        errors.append(f"restore summary ledger write failed: {exc}")
    return {
        "ok": unsafe == 0,
        "release_id": str(ledger.get("release_id", RELEASE_ID)),
        "restored": restored,
        "skipped": skipped,
        "unsafe": unsafe,
        "message": (
            f"restored {restored}, skipped {skipped}, unsafe {unsafe}"
            + (("; " + "; ".join(errors)) if errors else "")
        ),
        "ledger_path": str(ledger_path),
    }
