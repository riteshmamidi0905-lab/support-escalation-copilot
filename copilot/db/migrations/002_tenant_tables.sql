-- 002: tenant tables. Every one carries account_id, the RLS key. Child tables use COMPOSITE foreign keys so a row can never point at another account's parent.
CREATE TABLE copilot.accounts (
  account_id text PRIMARY KEY,
  name text NOT NULL,
  tier text NOT NULL CHECK (tier IN ('Standard','Premier','Enterprise')),
  region text NOT NULL,
  onboarded_on date NOT NULL,
  status text NOT NULL
);
CREATE TABLE copilot.account_contacts (
  account_id text NOT NULL REFERENCES copilot.accounts(account_id),
  name text NOT NULL,
  email text NOT NULL,
  PRIMARY KEY (account_id, email)
);
CREATE TABLE copilot.contracts (
  contract_id text PRIMARY KEY,
  account_id text NOT NULL REFERENCES copilot.accounts(account_id),
  tier text NOT NULL,
  sla_response_minutes integer NOT NULL,
  sla_resolution_hours integer NOT NULL,
  max_agent_requestable_pct numeric NOT NULL,
  max_manager_approvable_pct numeric NOT NULL,
  effective_from date NOT NULL,
  effective_to date
);
CREATE TABLE copilot.integrations (
  integration_id text PRIMARY KEY,
  account_id text NOT NULL REFERENCES copilot.accounts(account_id),
  kind text NOT NULL,
  provider text NOT NULL,
  sync_interval_minutes integer NOT NULL,
  endpoint_host text NOT NULL,
  status text NOT NULL,
  last_sync_at timestamptz,
  last_resync_at timestamptz
);
CREATE TABLE copilot.tickets (
  ticket_id text PRIMARY KEY,
  account_id text NOT NULL REFERENCES copilot.accounts(account_id),
  created_at timestamptz NOT NULL,
  product_area text NOT NULL,
  severity text NOT NULL,
  subject text NOT NULL,
  body text NOT NULL,                       -- UNTRUSTED free text
  reporter_name text NOT NULL,
  reporter_email text NOT NULL,
  channel text NOT NULL,
  UNIQUE (ticket_id, account_id)
);
CREATE TABLE copilot.ticket_history (
  ticket_id text NOT NULL,
  account_id text NOT NULL,
  seq integer NOT NULL,
  at timestamptz NOT NULL,
  author text NOT NULL,
  text text NOT NULL,
  PRIMARY KEY (ticket_id, seq),
  FOREIGN KEY (ticket_id, account_id) REFERENCES copilot.tickets(ticket_id, account_id)
);
CREATE TABLE copilot.incident_accounts (   -- which accounts an incident affected. Tenant-scoped on purpose: the incident itself is global, WHO it affected is not.
  incident_id text NOT NULL,
  account_id text NOT NULL REFERENCES copilot.accounts(account_id),
  PRIMARY KEY (incident_id, account_id)
);
CREATE TABLE copilot.cases (               -- a case is bound to exactly one ticket and therefore one account, by the TRUSTED intake role
  case_id text PRIMARY KEY,
  account_id text NOT NULL,
  ticket_id text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'open',
  FOREIGN KEY (ticket_id, account_id) REFERENCES copilot.tickets(ticket_id, account_id)
);
CREATE SEQUENCE copilot.case_seq START 1;
CREATE INDEX tickets_account_idx ON copilot.tickets(account_id);
CREATE INDEX integrations_account_idx ON copilot.integrations(account_id);
