import fcntl
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from spec_agent.schemas.customer_request import Customer, CustomerRequest
from spec_agent.schemas.standard import Standard
from spec_agent.services.standards import demo_standard


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.lock_file = (root / "server.lock").open("a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close()
            raise RuntimeError(
                "A server is already running with this data directory. Use workers=1."
            )
        self.connection = sqlite3.connect(root / "app.sqlite", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS standards (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS inspections (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, initial_state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS customer_requests (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS customers (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS form_profiles (
                id TEXT PRIMARY KEY, family_id TEXT NOT NULL, created_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, due TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS automation_jobs (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS process_flows (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS traceback_cases (
                id TEXT PRIMARY KEY, customer TEXT NOT NULL, created_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS limit_approvals (
                id TEXT PRIMARY KEY, customer TEXT NOT NULL, created_at TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS company_profile (id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS support_programs (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                inspection_id TEXT NOT NULL, request_id TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY (inspection_id, request_id)
            );
        """)
        standard = demo_standard()
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO standards VALUES (?, ?)",
                (standard.id, standard.model_dump_json()),
            )

    def standards(self) -> list[Standard]:
        return [
            Standard.model_validate_json(row["data"])
            for row in self.connection.execute("SELECT data FROM standards ORDER BY id")
        ]

    def standard(self, standard_id: str) -> Standard:
        row = self.connection.execute(
            "SELECT data FROM standards WHERE id=?", (standard_id,)
        ).fetchone()
        if row is None:
            raise KeyError("The standard could not be found.")
        return Standard.model_validate_json(row["data"])

    def save_standard(self, standard: Standard):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO standards VALUES (?, ?)",
                (standard.id, standard.model_dump_json()),
            )

    def family(self, lineage: str) -> list[Standard]:
        """All versions of a standard, oldest first."""
        return sorted(
            (
                standard
                for standard in self.standards()
                if standard.id == lineage or standard.family_id == lineage
            ),
            key=lambda standard: standard.version,
        )

    def delete_standard(self, standard_id: str):
        with self.connection:
            self.connection.execute("DELETE FROM standards WHERE id=?", (standard_id,))

    def inspection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in self.connection.execute("SELECT initial_state FROM inspections"):
            standard_id = json.loads(row["initial_state"])["standard"]["id"]
            counts[standard_id] = counts.get(standard_id, 0) + 1
        return counts

    def create_inspection(self, inspection_id: str, state: dict):
        with self.connection:
            self.connection.execute(
                "INSERT INTO inspections VALUES (?, ?, ?)",
                (inspection_id, state["created_at"], json.dumps(state)),
            )

    def initial_state(self, inspection_id: str) -> dict:
        row = self.connection.execute(
            "SELECT initial_state FROM inspections WHERE id=?", (inspection_id,)
        ).fetchone()
        if row is None:
            raise KeyError("The inspection could not be found.")
        return json.loads(row["initial_state"])

    def inspection_ids(self) -> list[str]:
        return [
            row["id"]
            for row in self.connection.execute(
                "SELECT id FROM inspections ORDER BY created_at DESC"
            )
        ]

    def request(self, inspection_id: str, request_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT payload FROM requests WHERE inspection_id=? AND request_id=?",
            (inspection_id, request_id),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def save_request(self, inspection_id: str, request_id: str, payload: dict):
        with self.connection:
            self.connection.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?)",
                (inspection_id, request_id, json.dumps(payload), timestamp()),
            )

    def requests(self, inspection_id: str) -> list[dict]:
        return [
            json.loads(row["payload"])
            for row in self.connection.execute(
                "SELECT payload FROM requests WHERE inspection_id=? ORDER BY created_at",
                (inspection_id,),
            )
        ]

    # --- customers and customer requests ---------------------------------------------------

    def save_customer_request(self, request: CustomerRequest):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO customer_requests VALUES (?, ?, ?)",
                (request.id, request.created_at, request.model_dump_json()),
            )

    def customer_request(self, request_id: str) -> CustomerRequest:
        row = self.connection.execute(
            "SELECT data FROM customer_requests WHERE id=?", (request_id,)
        ).fetchone()
        if row is None:
            raise KeyError("The customer request could not be found.")
        return CustomerRequest.model_validate_json(row["data"])

    def customer_requests(self) -> list[CustomerRequest]:
        return [
            CustomerRequest.model_validate_json(row["data"])
            for row in self.connection.execute(
                "SELECT data FROM customer_requests ORDER BY created_at DESC"
            )
        ]

    def save_customer(self, customer: Customer):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO customers VALUES (?, ?)",
                (customer.id, customer.model_dump_json()),
            )

    def customers(self) -> list[Customer]:
        return [
            Customer.model_validate_json(row["data"])
            for row in self.connection.execute("SELECT data FROM customers")
        ]

    def save_form_profile(self, profile: dict):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO form_profiles VALUES (?, ?, ?, ?)",
                (profile["id"], profile["family_id"], profile["created_at"], json.dumps(profile)),
            )

    def form_profile(self, family_id: str) -> dict | None:
        row = self.connection.execute(
            "SELECT data FROM form_profiles WHERE family_id=? ORDER BY created_at DESC LIMIT 1",
            (family_id,),
        ).fetchone()
        return json.loads(row["data"]) if row else None

    def form_profiles(self) -> list[dict]:
        return [
            json.loads(row["data"])
            for row in self.connection.execute(
                "SELECT data FROM form_profiles ORDER BY created_at DESC"
            )
        ]

    def save_limit_approval(self, approval: dict):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO limit_approvals VALUES (?, ?, ?, ?)",
                (
                    approval["id"],
                    approval["customer"],
                    approval["created_at"],
                    json.dumps(approval),
                ),
            )

    def limit_approvals(self) -> list[dict]:
        return [
            json.loads(row["data"])
            for row in self.connection.execute(
                "SELECT data FROM limit_approvals ORDER BY created_at DESC"
            )
        ]

    def save_process_flow(self, flow: dict):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO process_flows VALUES (?, ?, ?)",
                (flow["id"], flow["created_at"], json.dumps(flow)),
            )

    def process_flows(self) -> list[dict]:
        return [
            json.loads(row["data"])
            for row in self.connection.execute("SELECT data FROM process_flows ORDER BY created_at")
        ]

    def delete_process_flow(self, flow_id: str):
        with self.connection:
            if not self.connection.execute(
                "DELETE FROM process_flows WHERE id=?", (flow_id,)
            ).rowcount:
                raise KeyError("The process flow could not be found.")

    def save_traceback_case(self, case: dict):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO traceback_cases VALUES (?, ?, ?, ?)",
                (case["id"], case["customer"], case["created_at"], json.dumps(case)),
            )

    def traceback_cases(self) -> list[dict]:
        return [
            json.loads(row["data"])
            for row in self.connection.execute(
                "SELECT data FROM traceback_cases ORDER BY created_at DESC"
            )
        ]

    def save_task(self, task: dict):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO tasks VALUES (?, ?, ?)",
                (task["id"], task["due"], json.dumps(task)),
            )

    def task(self, task_id: str) -> dict:
        row = self.connection.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError("The task could not be found.")
        return json.loads(row["data"])

    def tasks(self) -> list[dict]:
        return [
            json.loads(row["data"])
            for row in self.connection.execute("SELECT data FROM tasks ORDER BY due, id")
        ]

    def delete_task(self, task_id: str):
        with self.connection:
            if not self.connection.execute("DELETE FROM tasks WHERE id=?", (task_id,)).rowcount:
                raise KeyError("The task could not be found.")

    def save_automation_job(self, job: dict):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO automation_jobs VALUES (?, ?, ?)",
                (job["id"], job["created_at"], json.dumps(job)),
            )

    def automation_job(self, job_id: str) -> dict:
        row = self.connection.execute(
            "SELECT data FROM automation_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError("The processing record could not be found.")
        return json.loads(row["data"])

    def automation_jobs(self) -> list[dict]:
        return [
            json.loads(row["data"])
            for row in self.connection.execute(
                "SELECT data FROM automation_jobs ORDER BY created_at DESC LIMIT 200"
            )
        ]

    def company_profile(self) -> dict | None:
        row = self.connection.execute("SELECT data FROM company_profile WHERE id='main'").fetchone()
        return json.loads(row["data"]) if row else None

    def save_company_profile(self, profile: dict):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO company_profile VALUES ('main', ?)",
                (json.dumps(profile, ensure_ascii=False),),
            )

    def save_support_program(self, program: dict):
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO support_programs VALUES (?, ?, ?)",
                (program["id"], program["created_at"], json.dumps(program, ensure_ascii=False)),
            )

    def support_program(self, program_id: str) -> dict:
        row = self.connection.execute(
            "SELECT data FROM support_programs WHERE id=?", (program_id,)
        ).fetchone()
        if row is None:
            raise KeyError("The support programme could not be found.")
        return json.loads(row["data"])

    def support_programs(self) -> list[dict]:
        return [
            json.loads(row["data"])
            for row in self.connection.execute(
                "SELECT data FROM support_programs ORDER BY created_at DESC"
            )
        ]

    def delete_support_program(self, program_id: str):
        with self.connection:
            if not self.connection.execute(
                "DELETE FROM support_programs WHERE id=?", (program_id,)
            ).rowcount:
                raise KeyError("The support programme could not be found.")

    def close(self):
        self.connection.close()
        fcntl.flock(self.lock_file, fcntl.LOCK_UN)
        self.lock_file.close()
