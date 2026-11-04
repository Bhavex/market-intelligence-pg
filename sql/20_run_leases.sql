ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS run_leases (
    lease_name    VARCHAR(40)  PRIMARY KEY,
    run_id        BIGINT       REFERENCES pipeline_runs(run_id) ON DELETE SET NULL,
    job_name      VARCHAR(40),
    acquired_at   TIMESTAMPTZ,
    heartbeat_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    CHECK ((run_id IS NULL) = (expires_at IS NULL))
);

CREATE TABLE IF NOT EXISTS run_lease_log (
    event_id    BIGSERIAL    PRIMARY KEY,
    lease_name  VARCHAR(40)  NOT NULL,
    run_id      BIGINT,
    event       VARCHAR(12)  NOT NULL CHECK (event IN ('acquired', 'released', 'taken_over', 'refused', 'reaped', 'lost')),
    at          TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_lease_log_run ON run_lease_log (run_id, at);

DROP TRIGGER IF EXISTS run_lease_log_append_only ON run_lease_log;
CREATE TRIGGER run_lease_log_append_only
BEFORE UPDATE OR DELETE ON run_lease_log
FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE OR REPLACE FUNCTION acquire_run_lease(p_lease TEXT, p_run BIGINT, p_job TEXT, p_ttl INTERVAL)
RETURNS TABLE (acquired BOOLEAN, holder_run BIGINT, holder_job TEXT, holder_since TIMESTAMPTZ, holder_expires TIMESTAMPTZ)
LANGUAGE plpgsql
AS $$
DECLARE
    l run_leases%ROWTYPE;
BEGIN
    INSERT INTO run_leases (lease_name) VALUES (p_lease) ON CONFLICT (lease_name) DO NOTHING;
    SELECT * INTO l FROM run_leases WHERE lease_name = p_lease FOR UPDATE;
    IF l.run_id IS NOT NULL AND l.run_id <> p_run AND l.expires_at > clock_timestamp() THEN
        RETURN QUERY SELECT FALSE, l.run_id, l.job_name::TEXT, l.acquired_at, l.expires_at;
        RETURN;
    END IF;
    IF l.run_id IS NOT NULL AND l.run_id <> p_run THEN
        UPDATE pipeline_runs
        SET status = 'failed',
            finished_at = GREATEST(clock_timestamp(), started_at),
            message = concat_ws('; ', message,
                                format('lease %s expired at %s without a heartbeat; taken over by run %s',
                                       p_lease, l.expires_at, p_run))
        WHERE run_id = l.run_id AND status = 'running';
        INSERT INTO run_lease_log (lease_name, run_id, event, detail)
        VALUES (p_lease, l.run_id, 'taken_over', format('expired at %s; taken over by run %s', l.expires_at, p_run));
    END IF;
    UPDATE run_leases
    SET run_id = p_run, job_name = p_job, acquired_at = clock_timestamp(),
        heartbeat_at = clock_timestamp(), expires_at = clock_timestamp() + p_ttl
    WHERE lease_name = p_lease;
    UPDATE pipeline_runs SET heartbeat_at = clock_timestamp() WHERE run_id = p_run;
    INSERT INTO run_lease_log (lease_name, run_id, event, detail) VALUES (p_lease, p_run, 'acquired', p_job);
    RETURN QUERY SELECT TRUE, p_run, p_job, clock_timestamp(), clock_timestamp() + p_ttl;
END;
$$;

CREATE OR REPLACE FUNCTION renew_run_lease(p_lease TEXT, p_run BIGINT, p_ttl INTERVAL)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE run_leases
    SET heartbeat_at = clock_timestamp(), expires_at = clock_timestamp() + p_ttl
    WHERE lease_name = p_lease AND run_id = p_run AND expires_at > clock_timestamp();
    IF NOT FOUND THEN
        INSERT INTO run_lease_log (lease_name, run_id, event, detail)
        VALUES (p_lease, p_run, 'lost', 'heartbeat found the lease expired or held by another run');
        RETURN FALSE;
    END IF;
    UPDATE pipeline_runs SET heartbeat_at = clock_timestamp() WHERE run_id = p_run;
    RETURN TRUE;
END;
$$;

CREATE OR REPLACE FUNCTION release_run_lease(p_lease TEXT, p_run BIGINT)
RETURNS BOOLEAN
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE run_leases
    SET run_id = NULL, job_name = NULL, acquired_at = NULL, heartbeat_at = NULL, expires_at = NULL
    WHERE lease_name = p_lease AND run_id = p_run;
    IF FOUND THEN
        INSERT INTO run_lease_log (lease_name, run_id, event) VALUES (p_lease, p_run, 'released');
    END IF;
    RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION reap_stale_runs(p_stale_after INTERVAL, p_by BIGINT DEFAULT NULL)
RETURNS TABLE (run_id BIGINT, job_name TEXT, last_seen TIMESTAMPTZ)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    WITH stale AS (
        UPDATE pipeline_runs r
        SET status = 'failed',
            finished_at = GREATEST(clock_timestamp(), r.started_at),
            message = concat_ws('; ', r.message,
                                format('marked failed by the stale-run detector%s: no heartbeat since %s (limit %s)',
                                       CASE WHEN p_by IS NULL THEN '' ELSE format(' (run %s)', p_by) END,
                                       COALESCE(r.heartbeat_at, r.started_at), p_stale_after))
        WHERE r.status = 'running'
          AND COALESCE(r.heartbeat_at, r.started_at) < clock_timestamp() - p_stale_after
          AND (p_by IS NULL OR r.run_id <> p_by)
          AND NOT EXISTS (SELECT 1 FROM run_leases l
                          WHERE l.run_id = r.run_id AND l.expires_at > clock_timestamp())
        RETURNING r.run_id, r.job_name::TEXT, COALESCE(r.heartbeat_at, r.started_at) AS last_seen
    ), logged AS (
        INSERT INTO run_lease_log (lease_name, run_id, event, detail)
        SELECT 'pipeline', s.run_id, 'reaped', format('no heartbeat since %s', s.last_seen) FROM stale s
    )
    SELECT s.run_id, s.job_name, s.last_seen FROM stale s;
END;
$$;

CREATE OR REPLACE VIEW v_run_leases AS
SELECT l.lease_name, l.run_id, l.job_name, l.acquired_at, l.heartbeat_at, l.expires_at,
       (l.run_id IS NOT NULL AND l.expires_at > clock_timestamp()) AS is_live,
       EXTRACT(EPOCH FROM l.expires_at - clock_timestamp())::NUMERIC(12,1) AS seconds_to_expiry
FROM run_leases l;

CREATE OR REPLACE VIEW v_stale_runs AS
SELECT r.run_id, r.job_name, r.started_at, r.heartbeat_at,
       clock_timestamp() - COALESCE(r.heartbeat_at, r.started_at) AS silent_for,
       EXISTS (SELECT 1 FROM run_leases l WHERE l.run_id = r.run_id AND l.expires_at > clock_timestamp()) AS holds_live_lease
FROM pipeline_runs r
WHERE r.status = 'running';

CREATE OR REPLACE VIEW v_lease_holdings AS
SELECT a.lease_name, a.run_id, a.at AS held_from,
       COALESCE((SELECT MIN(e.at) FROM run_lease_log e
                 WHERE e.lease_name = a.lease_name AND e.run_id = a.run_id AND e.at >= a.at
                   AND e.event IN ('released', 'taken_over', 'lost', 'reaped')), 'infinity') AS held_until
FROM run_lease_log a
WHERE a.event = 'acquired';

CREATE OR REPLACE VIEW v_lease_overlaps AS
SELECT x.lease_name, x.run_id AS run_a, y.run_id AS run_b, x.held_from AS a_from, x.held_until AS a_until,
       y.held_from AS b_from, y.held_until AS b_until
FROM v_lease_holdings x
JOIN v_lease_holdings y
  ON y.lease_name = x.lease_name AND y.run_id > x.run_id
 AND tstzrange(x.held_from, x.held_until) && tstzrange(y.held_from, y.held_until);

GRANT SELECT ON run_leases, run_lease_log, v_run_leases, v_stale_runs, v_lease_holdings, v_lease_overlaps TO readonly_access;
