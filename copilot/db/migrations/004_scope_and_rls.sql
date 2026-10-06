-- 004: signed trusted scope + forced row-level security.
--
-- A scope is  (account_id, case_id, expiry, key_id, signature)  minted ONLY by the trusted case service from a case row it created from a ticket row.
-- The application role can set the five session settings below to anything it likes, but the database only honours a set that carries a valid HMAC
-- over them, made with a key the application role cannot read, and not yet expired. There is NO function the app role can call that signs anything.
CREATE TABLE copilot_private.scope_keys (
  key_id text PRIMARY KEY,
  secret text NOT NULL CHECK (length(secret) >= 32),
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

-- HMAC-SHA256 from the built-in sha256(bytea) (RFC 2104), so there is no extension to trust or install. Cross-checked against Python's hmac in tests.
CREATE FUNCTION copilot_private.hmac_sha256(key bytea, msg bytea) RETURNS bytea
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog AS $$
DECLARE k bytea := key; ipad bytea := ''::bytea; opad bytea := ''::bytea; i integer;
BEGIN
  IF length(k) > 64 THEN k := sha256(k); END IF;
  k := k || decode(repeat('00', 64 - length(k)), 'hex');
  FOR i IN 0..63 LOOP
    ipad := ipad || set_byte('\x00'::bytea, 0, get_byte(k, i) # 54);   -- 0x36
    opad := opad || set_byte('\x00'::bytea, 0, get_byte(k, i) # 92);   -- 0x5c
  END LOOP;
  RETURN sha256(opad || sha256(ipad || msg));
END $$;

-- Returns the account the CURRENT SESSION is validly scoped to, or NULL (=> RLS shows nothing). Fails closed on every malformed input.
CREATE FUNCTION copilot_private.scope_account() RETURNS text
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, copilot_private, copilot AS $$
DECLARE
  acc text := current_setting('app.scope_account', true);
  cas text := current_setting('app.scope_case', true);
  exp_txt text := current_setting('app.scope_exp', true);
  kid text := current_setting('app.scope_key', true);
  sig text := current_setting('app.scope_sig', true);
  exp_i bigint; secret_v text; expected text;
BEGIN
  IF acc IS NULL OR cas IS NULL OR exp_txt IS NULL OR kid IS NULL OR sig IS NULL OR acc = '' OR cas = '' OR kid = '' OR sig = '' THEN RETURN NULL; END IF;
  BEGIN exp_i := exp_txt::bigint; EXCEPTION WHEN others THEN RETURN NULL; END;
  IF exp_i < extract(epoch FROM now())::bigint THEN RETURN NULL; END IF;
  SELECT k.secret INTO secret_v FROM copilot_private.scope_keys k WHERE k.key_id = kid AND k.active;
  IF NOT FOUND THEN RETURN NULL; END IF;
  -- the case must exist and belong to exactly this account (a signing bug or a mismatched pair fails closed here, independently of the signature)
  PERFORM 1 FROM copilot.cases c WHERE c.case_id = cas AND c.account_id = acc;
  IF NOT FOUND THEN RETURN NULL; END IF;
  expected := encode(copilot_private.hmac_sha256(convert_to(secret_v, 'UTF8'), convert_to('v1|' || acc || '|' || cas || '|' || exp_txt || '|' || kid, 'UTF8')), 'hex');
  IF expected = sig THEN RETURN acc; END IF;
  RETURN NULL;
END $$;

-- Row-level security: enabled AND forced (the owner is not exempt). Policies are per role, so a role with no policy sees nothing at all.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['accounts','account_contacts','contracts','integrations','tickets','ticket_history','incident_accounts','cases'] LOOP
    EXECUTE format('ALTER TABLE copilot.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE copilot.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_read ON copilot.%I FOR SELECT TO copilot_app USING (account_id = (SELECT copilot_private.scope_account()))', t);
    IF t <> 'cases' THEN
      EXECUTE format('CREATE POLICY loader_insert ON copilot.%I FOR INSERT TO copilot_loader WITH CHECK (true)', t);
    END IF;
  END LOOP;
END $$;
-- accounts' own key column is account_id, so the generic policy above already scopes it to a single row.

-- The definer function runs as the (forced) owner, so the owner needs a policy to read cases for the consistency check above.
CREATE POLICY definer_read_cases ON copilot.cases FOR SELECT TO copilot_owner USING (true);

-- The trusted intake role (the case service, NOT the model-facing tools) may read tickets to learn which account a ticket belongs to and create cases.
CREATE POLICY intake_read_tickets ON copilot.tickets FOR SELECT TO copilot_intake USING (true);
CREATE POLICY intake_read_cases   ON copilot.cases   FOR SELECT TO copilot_intake USING (true);
CREATE POLICY intake_write_cases  ON copilot.cases   FOR INSERT TO copilot_intake WITH CHECK (true);
