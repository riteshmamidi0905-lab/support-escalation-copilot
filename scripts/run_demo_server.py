"""Run the operator UI locally on a CLEAN, synthetic Meridian environment with the deterministic demo entry points (A-E).

    python scripts/with_local_pg.py python scripts/run_demo_server.py [--port 8765]

Everything is fictional and local. Sign-in is SIMULATED (pick a persona). Demo mode is explicit: it enables scripted misbehaving stand-in models and customer-system faults that must never run in a real deployment.
The model in use is the deterministic RuleCaseModel stand-in, NOT an LLM."""
import argparse
import os
import secrets
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bench_env  # noqa: E402
import psycopg  # noqa: E402

from copilot.app import demo as D  # noqa: E402
from copilot.app.services import Services  # noqa: E402
from copilot.app.web import OperatorApp  # noqa: E402
from copilot.control.identity import IdentityAuthority  # noqa: E402
from copilot.db import admin as dbadmin  # noqa: E402
from copilot.db.session import make_pool  # noqa: E402


class Threaded(WSGIServer):
    daemon_threads = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self.process_request_thread, args=(request, client_address), daemon=True)
        t.start()

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:                                                  # noqa: BLE001
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


class Quiet(WSGIRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.command} {self.path.split(chr(63))[0]}\n")


def build_services():
    env = bench_env.build()
    base = os.environ["COPILOT_TEST_DATABASE_URL"]
    control_dsn = dbadmin.role_dsn(base, "copilot_control", env.passwords["copilot_control"], env.dbname)
    authority = IdentityAuthority(secrets.token_hex(32))
    picks, rows = D.demo_ticket_rows(env.dataset)
    with psycopg.connect(env.loader_dsn) as c, c.transaction():                                  # the privileged loader role stays in scripts: application code cannot import it
        c.cursor().executemany("INSERT INTO copilot.tickets VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
    svc = Services.build(app_pool=env.pool, control_pool=make_pool(control_dsn, 2, 12), guard=env.guard, intake=env.intake, authority=authority, dataset=env.dataset, demo=True, personas=D.demo_personas(env.dataset, picks))
    svc.demo_tickets = picks
    D.register_custom_tickets(svc, picks, env.dataset)
    return svc, env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    svc, env = build_services()
    app = OperatorApp(svc)
    stop = threading.Event()

    def background():                                                      # the recovery worker: resumes cases whose approval was decided, retries stalled work (the web path resumes immediately too)
        wk = svc.worker("demo-worker")
        while not stop.wait(2.0):
            try:
                svc.refresh_agent()
                wk.tick()
            except Exception as e:                                         # noqa: BLE001
                sys.stderr.write(f"recovery tick failed: {type(e).__name__}\n")
    threading.Thread(target=background, daemon=True).start()
    srv = make_server(a.host, a.port, app, server_class=Threaded, handler_class=Quiet)
    print(f"Operator UI on http://{a.host}:{a.port}/login  (synthetic data, simulated sign-in, stand-in model)  dataset={env.dataset}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()


if __name__ == "__main__":
    main()
