import argparse
import json
import sys
from pathlib import Path

import yaml
from filelock import Timeout

from .models import ProjectConfig
from .runtime import Runtime


def load_config(path):
    source = Path(path).resolve()
    data = yaml.safe_load(source.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise TypeError("configuration must be a YAML object")
    repo = Path(data["repo_path"])
    if not repo.is_absolute():
        data["repo_path"] = str(source.parent / repo)
    return ProjectConfig.model_validate(data)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local review-gated LangGraph controller")
    parser.add_argument("--config", help="Project YAML; relative repo_path resolves against it")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["start", "tick", "status", "events", "resume"]:
        command = sub.add_parser(name)
        command.add_argument("run_id")
    sub.add_parser("actions")
    command = sub.add_parser("bridge", help="Next deterministic host operation")
    command.add_argument("run_id")
    command.add_argument("--output", help="Save packet JSON and prompt text instead of printing")
    command = sub.add_parser("wake", help="Observe scheduled/startup delivery; never resumes")
    command.add_argument("run_id")
    command.add_argument("--delivery-id", required=True)
    command.add_argument("--trigger", choices=["schedule", "startup"], required=True)
    command.add_argument("--output", help="Save host packet and prompt text")
    command = sub.add_parser("pause")
    command.add_argument("run_id")
    command.add_argument("--reason", required=True)
    command = sub.add_parser("confirm-pause")
    command.add_argument("run_id")
    command.add_argument("--epoch", required=True, type=int)
    command.add_argument("--evidence", required=True)
    command = sub.add_parser("bind")
    command.add_argument("action_id")
    command.add_argument("external_id")
    command.add_argument("--locator", required=True)
    command = sub.add_parser("observe")
    command.add_argument("action_id")
    command.add_argument("state", choices=["RUNNING", "QUIESCENT"])
    command.add_argument("--epoch", required=True, type=int)
    command.add_argument("--evidence", required=True)
    command = sub.add_parser("signal-claim")
    command.add_argument("action_id")
    command.add_argument("verb", choices=["PAUSE", "RESUME", "REVIEW_FORMAT"])
    command.add_argument("--epoch", required=True, type=int)
    command = sub.add_parser("signal-ack")
    command.add_argument("signal_id")
    command.add_argument("--evidence", required=True)
    command.add_argument("--absent", action="store_true")
    command = sub.add_parser("review-import")
    command.add_argument("action_id")
    command.add_argument("file")
    command.add_argument("--locator", required=True)
    command.add_argument("--expected-revision", type=int)
    for name in ["request", "claim"]:
        command = sub.add_parser(name)
        command.add_argument("action_id")
    command = sub.add_parser("receipt")
    command.add_argument("action_id")
    command.add_argument("file")
    command.add_argument("--locator", required=True)
    command.add_argument("--expected-revision", type=int)
    command = sub.add_parser("reconcile-absent")
    command.add_argument("action_id")
    command.add_argument("--evidence", required=True)
    command = sub.add_parser("control")
    command.add_argument("run_id")
    command.add_argument("file", help="Structured control command JSON")
    command = sub.add_parser("export")
    command.add_argument("run_id")
    command.add_argument("directory")
    command = sub.add_parser("stop")
    command.add_argument("run_id")
    command.add_argument("--reason", required=True)
    command = sub.add_parser("demo")
    command.add_argument("--directory", default=".runs/demo")
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            from .demo import run_demo
            result = run_demo(Path(args.directory))
        else:
            if not args.config:
                parser.error("--config is required except for demo")
            runtime = Runtime(load_config(args.config))
            if args.command in {"start", "tick", "status", "resume"}:
                result = getattr(runtime, args.command)(args.run_id)
            elif args.command == "pause":
                result = runtime.pause(args.run_id, args.reason)
            elif args.command == "confirm-pause":
                result = runtime.confirm_pause(args.run_id, args.epoch, args.evidence)
            elif args.command == "bind":
                result = runtime.bind(args.action_id, args.external_id, args.locator)
            elif args.command == "observe":
                result = runtime.observe(args.action_id, args.state, args.evidence, args.epoch)
            elif args.command == "signal-claim":
                result = runtime.claim_signal(args.action_id, args.verb, args.epoch)
            elif args.command == "signal-ack":
                result = runtime.ack_signal(args.signal_id, args.evidence, args.absent)
            elif args.command == "review-import":
                result = runtime.import_review(args.action_id,
                    Path(args.file).read_text("utf-8-sig"), args.locator, args.expected_revision)
            elif args.command in {"bridge", "wake"}:
                result = (runtime.wake(args.run_id, args.delivery_id, args.trigger)
                          if args.command == "wake" else runtime.bridge(args.run_id))
                config_path = str(Path(args.config).resolve())
                # A shell-quoted command is guidance only; never evaluated by this CLI.
                quote = lambda value: "'" + value.replace("'", "''") + "'"
                prefix = "& " + quote(sys.executable) + " -m orchestrator --config "
                prefix += quote(config_path)
                result["status_command"] = prefix + " status " + quote(args.run_id)
                if result.get("operation") == "CREATE_WORKER":
                    runtime.root.joinpath("bridge").mkdir(exist_ok=True)
                    result["prompt"] += ("\n\nPowerShell 状态检查命令：\n" +
                        result["status_command"] + "\n读取真实绑定身份：\n" + prefix +
                        " request " + quote(result["action_id"]) +
                        "\n从返回值 binding.external_id 读取 worker_id；尚未绑定就结束回合等待通知。")
                if args.output:
                    path = Path(args.output).resolve()
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
                    prompt_path = path.with_suffix(".prompt.txt")
                    if "prompt" in result:
                        prompt_path.write_text(result["prompt"], "utf-8")
                    result = {"operation": result["operation"], "packet": str(path),
                              "prompt": str(prompt_path) if "prompt" in result else None}
            elif args.command == "events":
                result = runtime.store.events(args.run_id)
            elif args.command == "actions":
                result = [{k: a[k] for k in ["id", "kind", "status", "revision", "sealed"]}
                          for a in runtime.pending()]
            elif args.command == "request":
                result = runtime.store.action(args.action_id)
                result["binding"] = runtime.store.binding(args.action_id)
            elif args.command == "claim":
                result = runtime.claim(args.action_id)
            elif args.command == "receipt":
                runtime.receipt(args.action_id, read_json(args.file), args.locator,
                                args.expected_revision)
                result = {"recorded": args.action_id}
            elif args.command == "reconcile-absent":
                runtime.reconcile_absent(args.action_id, args.evidence)
                result = {"reconciled_absent": args.action_id}
            elif args.command == "control":
                result = runtime.control(args.run_id, read_json(args.file))
            elif args.command == "stop":
                result = runtime.stop(args.run_id, args.reason)
            else:
                result = {"export_directory": runtime.export(args.run_id, args.directory)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, OSError, Timeout) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
