"""Read-only probe: is there a LEGITIMATE local language-model runtime on this machine? Installs nothing, downloads nothing, calls no paid or remote service, starts no model.

Checks: well-known local inference servers answering on localhost (Ollama, LM Studio, vLLM, llama.cpp server), inference binaries on PATH, Python inference libraries already installed, and model
files already on disk (sizes only). Writes reports/m5/real-model-probe.json. The result decides ONE thing: whether a real-model experiment may be run under docs/m5-real-model-protocol.md.
If nothing is found the report says so and the real-model evaluation is NOT executed; no number is ever invented or relabelled."""
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVERS = {"ollama": ("127.0.0.1", 11434, "/api/tags"), "lm_studio": ("127.0.0.1", 1234, "/v1/models"), "vllm": ("127.0.0.1", 8000, "/v1/models"), "llama_cpp_server": ("127.0.0.1", 8080, "/v1/models")}
BINARIES = ["ollama", "llama-server", "llama-cli", "llamafile", "lms", "vllm", "mlx_lm.server", "mlx_lm.generate"]
LIBS = ["llama_cpp", "mlx_lm", "mlx", "transformers", "torch", "vllm", "ctransformers", "onnxruntime_genai"]
MODEL_DIRS = ["~/.ollama/models", "~/.cache/huggingface/hub", "~/.cache/lm-studio/models", "~/Library/Application Support/LM Studio/models", "~/.lmstudio/models", "~/models"]


def port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:                    # noqa: S310 - localhost only
            return json.loads(r.read().decode())
    except Exception:                                                        # noqa: BLE001
        return None


def dir_size_gb(p: Path) -> float:
    total = 0
    for r, _d, fs in os.walk(p):
        for f in fs:
            try:
                total += (Path(r) / f).stat().st_size
            except OSError:
                pass
    return round(total / 1e9, 2)


def ram_gb() -> float | None:
    try:
        if sys.platform == "darwin":
            return round(int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True).stdout) / 1e9, 1)       # noqa: S603, S607
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9, 1)
    except Exception:                                                        # noqa: BLE001
        return None


def main() -> int:
    out = {"machine": {"platform": platform.platform(), "arch": platform.machine(), "python": platform.python_version(), "ram_gb": ram_gb()}, "servers": {}, "binaries": {}, "python_libraries": {}, "model_files": {}}
    for name, (host, port, path) in SERVERS.items():
        up = port_open(host, port)
        out["servers"][name] = {"port": port, "listening": up, "models": [m.get("name") or m.get("id") for m in ((get(f"http://{host}:{port}{path}") or {}).get("models") or (get(f"http://{host}:{port}{path}") or {}).get("data") or [])] if up else []}
    for b in BINARIES:
        out["binaries"][b] = shutil.which(b)
    for lib in LIBS:
        out["python_libraries"][lib] = importlib.util.find_spec(lib) is not None
    for d in MODEL_DIRS:
        p = Path(d).expanduser()
        if p.exists():
            out["model_files"][d] = {"exists": True, "size_gb": dir_size_gb(p), "entries": sorted(x.name for x in p.iterdir() if x.name not in ("CACHEDIR.TAG", "blobs") and not x.name.startswith("."))[:20]}
    served = [n for n, s in out["servers"].items() if s["listening"] and s["models"]]
    local_files = [d for d, v in out["model_files"].items() if v["size_gb"] >= 0.5 and not all(("bge" in e or "MiniLM" in e or "minilm" in e) for e in v["entries"])]   # the retrieval models M2 uses are not generative
    out["usable_runtime"] = bool(served)
    out["decision"] = ("a local server with at least one model is answering: a real-model experiment MAY be run under docs/m5-real-model-protocol.md" if served else
                       "no local language-model server is answering. Nothing is installed or downloaded by this project to change that (no large downloads, no invasive installs, no paid API). "
                       "Real-model evaluation was NOT executed" + (f"; model files exist on disk ({', '.join(local_files)}) but no runtime serves them" if local_files else "") + ".")
    (ROOT / "reports" / "m5").mkdir(parents=True, exist_ok=True)
    (ROOT / "reports" / "m5" / "real-model-probe.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("usable_runtime", "decision")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
