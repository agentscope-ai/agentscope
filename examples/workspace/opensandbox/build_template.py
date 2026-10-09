# -*- coding: utf-8 -*-
"""Build and validate a prepared AgentScope image and FastSandbox template."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from typing import Any, TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from opensandbox import SandboxManager
    from opensandbox.config.connection import ConnectionConfig
    from opensandbox.models.templates import TemplateInfo

REPO_ROOT = Path(__file__).resolve().parents[3]
RECIPE = Path(__file__).resolve().parent / "template"
PLATFORM = "linux/amd64"
CHECK_COMMAND = "python3 /opt/agentscope-template/check_gateway.py"


def tagged_image(value: str) -> str:
    """Require an explicit OCI tag, without embedded login credentials."""
    if (
        re.search(r"\s", value)
        or value.startswith("-")
        or "@" in value
        or "://" in value
        or ":" not in value.rsplit("/", 1)[-1]
    ):
        raise argparse.ArgumentTypeError(
            "use an image name with an explicit tag"
        )
    name, tag = value.rsplit(":", 1)
    if not name or not re.fullmatch(r"[\w][\w.-]{0,127}", tag):
        raise argparse.ArgumentTypeError("invalid image name or tag")
    return value


def positive(value: str) -> int:
    """Parse a positive integer CLI setting."""
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def quantity(value: str) -> str:
    """Accept positive binary memory/disk quantities."""
    if not re.fullmatch(r"[1-9][0-9]*(Mi|Gi|Ti)", value):
        raise argparse.ArgumentTypeError("use a positive quantity such as 2Gi")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Read the image target and deployment-specific settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=tagged_image)
    parser.add_argument(
        "--publish", help="S3 template target, e.g. s3://bucket/publish"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Build/load and validate in local Docker only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without making changes",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        help="Extra packages installed in the gateway venv",
    )
    parser.add_argument("--cpu", type=positive, default=1)
    parser.add_argument("--memory", type=quantity, default="2Gi")
    parser.add_argument("--disk", type=quantity, default="4Gi")
    parser.add_argument("--nameservers", nargs="+", default=[])
    parser.add_argument(
        "--domain", default=os.getenv("OPENSANDBOX_DOMAIN", "")
    )
    parser.add_argument(
        "--protocol",
        choices=["http", "https"],
        default=os.getenv("OPENSANDBOX_PROTOCOL", "http"),
    )
    parser.add_argument("--wait-timeout", type=positive, default=1800)
    parser.add_argument(
        "--output", type=Path, default=Path("dist/opensandbox-template.json")
    )
    args = parser.parse_args(argv)
    if not args.local:
        target = urlparse(args.publish or "")
        if target.scheme != "s3" or not target.netloc:
            parser.error("--publish must be an s3://bucket/prefix target")
        if not args.dry_run and not args.domain:
            parser.error("set OPENSANDBOX_DOMAIN or pass --domain")
    if args.requirements and not args.requirements.is_file():
        parser.error("--requirements must name an existing file")
    return args


def template_request(image: str, args: argparse.Namespace) -> dict:
    """Use the same empty-gateway recipe in local and remote validation."""
    return {
        "image": image,
        "publish": args.publish,
        "resourceLimits": {
            "cpu": str(args.cpu),
            "memory": args.memory,
            "disk": args.disk,
        },
        "entrypoint": ["sh", "/opt/agentscope-template/start_gateway.sh"],
        "env": (
            {"SANDBOX_NAMESERVERS": " ".join(args.nameservers)}
            if args.nameservers
            else {}
        ),
        "readiness": {"probe": "cmd://" + CHECK_COMMAND},
        "metadata": {"origin": "agentscope-prepared-gateway"},
        "format": "native",
    }


def run_command(command: list[str]) -> None:
    """Keep build logs off stdout, which is reserved for the final result."""
    subprocess.run(command, check=True, stdout=sys.stderr, stderr=sys.stderr)


def build_image(args: argparse.Namespace) -> str:
    """Build from an isolated context with exactly one checkout wheel."""
    with tempfile.TemporaryDirectory(
        prefix="agentscope-template-"
    ) as directory:
        work = Path(directory)
        wheels = work / "wheels"
        run_command(
            [
                "uv",
                "build",
                "--wheel",
                "--out-dir",
                str(wheels),
                str(REPO_ROOT),
            ]
        )
        candidates = list(wheels.glob("agentscope-*.whl"))
        if len(candidates) != 1:
            raise RuntimeError(
                "wheel build must produce exactly one AgentScope wheel"
            )
        context = work / "context"
        shutil.copytree(
            RECIPE,
            context,
            ignore=shutil.ignore_patterns("__pycache__", "*.whl"),
        )
        shutil.copy2(candidates[0], context / candidates[0].name)
        if args.requirements:
            shutil.copy2(args.requirements, context / "requirements.txt")
        metadata = work / "image.json"
        run_command(
            [
                "docker",
                "buildx",
                "build",
                "--platform",
                PLATFORM,
                "--tag",
                args.image,
                "--metadata-file",
                str(metadata),
                "--load" if args.local else "--push",
                str(context),
            ]
        )
        if args.local:
            return args.image
        digest = json.loads(metadata.read_text()).get(
            "containerimage.digest", ""
        )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise RuntimeError(
                "Buildx did not return a published manifest digest"
            )
        return args.image.rsplit(":", 1)[0] + "@" + digest


def save_result(path: Path, result: dict) -> None:
    """Persist the template ID atomically without saving credentials."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        json.dump(result, stream, indent=2)
        stream.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_local(image: str, timeout: int) -> None:
    """Start the loaded image and check its real, session-free gateway."""
    name = "agentscope-template-check-" + uuid.uuid4().hex[:12]
    try:
        run_command(
            [
                "docker",
                "run",
                "--detach",
                "--platform",
                PLATFORM,
                "--network",
                "none",
                "--name",
                name,
                image,
            ]
        )
        deadline = time.monotonic() + min(timeout, 120)
        while True:
            probe = subprocess.run(
                [
                    "docker",
                    "exec",
                    name,
                    "python3",
                    "/opt/agentscope-template/check_gateway.py",
                ],
                capture_output=True,
                check=False,
            )
            if probe.returncode == 0:
                return
            if time.monotonic() >= deadline:
                run_command(
                    [
                        "docker",
                        "exec",
                        name,
                        "cat",
                        "/root/.agentscope/gateway.log",
                    ]
                )
                raise RuntimeError("local gateway readiness check failed")
            time.sleep(1)
    finally:
        run_command(["docker", "rm", "--force", "--volumes", name])


async def wait_template(
    manager: SandboxManager,
    template: TemplateInfo,
    timeout: int,
    output: Path,
    result: dict,
) -> TemplateInfo:
    """Wait for publication while retaining the ID on failure or timeout."""
    previous = None
    try:
        async with asyncio.timeout(timeout):
            while True:
                phase = template.status.phase
                result["phase"] = phase
                save_result(output, result)
                if phase != previous:
                    print(
                        f"Template {template.template_id}: {phase}",
                        file=sys.stderr,
                    )
                    previous = phase
                if phase == "Succeeded":
                    return template
                if phase == "Failed":
                    raise RuntimeError(
                        "template build failed: "
                        + (template.status.message or "no details")
                    )
                await asyncio.sleep(2)
                template = await manager.get_template(template.template_id)
    except TimeoutError as exc:
        raise RuntimeError(
            f"template {template.template_id} still building; "
            "query its saved ID before creating another"
        ) from exc


async def validate_template(
    template_id: str, config: ConnectionConfig, output: Path, result: dict
) -> None:
    """Verify a restored gateway directly, without bootstrap fallback."""
    from opensandbox import Sandbox
    from opensandbox.models.execd import RunCommandOpts
    from agentscope.workspace._opensandbox._template import (
        PREPARED_STATE_FILE,
        prepared_template_state,
    )

    sandbox = await Sandbox.create_from_template(
        template_id=template_id,
        connection_config=config,
        timeout=timedelta(minutes=5),
        ready_timeout=timedelta(seconds=120),
        metadata={"origin": "agentscope-template-check"},
    )
    result["validation_sandbox_id"] = sandbox.id
    result["validation_sandbox_deleted"] = False
    try:
        save_result(output, result)
        state = json.loads(await sandbox.files.read_bytes(PREPARED_STATE_FILE))
        if state != prepared_template_state(5600):
            raise RuntimeError(
                "restored template is incompatible with this "
                "AgentScope gateway"
            )
        execution = await sandbox.commands.run(
            CHECK_COMMAND, RunCommandOpts(timeout=timedelta(seconds=30))
        )
        if execution.error is not None or execution.exit_code != 0:
            raise RuntimeError(
                "restored gateway is unhealthy or contains session data"
            )
    finally:
        try:
            await sandbox.kill()
            result["validation_sandbox_deleted"] = True
        finally:
            await sandbox.close()
            save_result(output, result)


async def build_template(args: argparse.Namespace) -> dict:
    """Build, publish, and validate; retain useful state on failure."""
    result: dict[str, Any] = {
        "image": args.image,
        "platform": PLATFORM,
        "local": args.local,
        "validated": False,
    }
    manager: SandboxManager | None = None
    config: ConnectionConfig | None = None
    try:
        for command in ("uv", "docker"):
            if shutil.which(command) is None:
                raise RuntimeError(
                    f"install {command} before building a template"
                )
        run_command(["docker", "buildx", "version"])
        run_command(
            ["docker", "info", "--format", "{{.OSType}}/{{.Architecture}}"]
        )
        if not args.local:
            from opensandbox import Sandbox, SandboxManager
            from opensandbox.config.connection import ConnectionConfig
            from opensandbox.models.templates import (
                CreateTemplateRequest,
                TemplateFilter,
            )

            if not hasattr(Sandbox, "create_from_template") or not hasattr(
                SandboxManager, "create_template"
            ):
                raise RuntimeError(
                    "install a template-capable OpenSandbox SDK"
                )
            config = ConnectionConfig(
                domain=args.domain,
                protocol=args.protocol,
                request_timeout=timedelta(seconds=120),
            )
            manager = await SandboxManager.create(config)
            await manager.list_templates(TemplateFilter(page_size=1))
        save_result(args.output, result)
        result["image"] = build_image(args)
        save_result(args.output, result)
        if args.local:
            validate_local(result["image"], args.wait_timeout)
            result["phase"] = "LocalImageValidated"
        else:
            assert manager is not None and config is not None
            template = await manager.create_template(
                CreateTemplateRequest.model_validate(
                    template_request(result["image"], args)
                )
            )
            result["template_id"] = template.template_id
            save_result(args.output, result)
            await wait_template(
                manager, template, args.wait_timeout, args.output, result
            )
            await validate_template(
                template.template_id, config, args.output, result
            )
        result["validated"] = True
        save_result(args.output, result)
        return result
    except Exception as exc:
        result["error"] = str(exc)
        save_result(args.output, result)
        raise
    finally:
        if manager is not None:
            await manager.close()


def main(argv: list[str] | None = None) -> int:
    """Print a shell export only after the complete validation succeeds."""
    args = parse_args(argv)
    if args.dry_run:
        print(
            json.dumps(
                {
                    "source": str(REPO_ROOT),
                    "platform": PLATFORM,
                    "local": args.local,
                    "request": template_request(args.image, args),
                },
                indent=2,
            )
        )
        return 0
    try:
        result = asyncio.run(build_template(args))
    except KeyboardInterrupt:
        print(
            f"Interrupted; inspect {args.output} "
            "for any accepted template ID.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        print(f"Build failed: {exc}. Details: {args.output}", file=sys.stderr)
        return 1
    print(f"Validated result saved to {args.output}", file=sys.stderr)
    if args.local:
        print("Validated local image: " + result["image"])
    else:
        print(
            "export OPENSANDBOX_TEMPLATE_ID="
            + shlex.quote(result["template_id"])
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
