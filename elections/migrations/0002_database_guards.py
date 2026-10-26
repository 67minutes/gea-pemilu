from django.db import migrations

SQL = """
CREATE FUNCTION gea_vote_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e elections_election%ROWTYPE;
BEGIN
  IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Votes are immutable'; END IF;
  SELECT * INTO e FROM elections_election WHERE id=NEW.election_id FOR UPDATE;
  IF e.activated_at IS NULL OR e.paused OR e.result_snapshot IS NOT NULL
     OR clock_timestamp() < e.opens_at OR clock_timestamp() >= e.closes_at THEN
    RAISE EXCEPTION 'Voting is not open';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM elections_voter v JOIN elections_user u ON u.id=v.user_id
    JOIN elections_office o ON o.id=NEW.office_id
    JOIN elections_candidate c ON c.id=NEW.candidate_id
    WHERE v.id=NEW.voter_id AND v.election_id=e.id AND o.election_id=e.id
      AND c.office_id=o.id AND u.is_active AND NOT u.is_staff
  ) THEN RAISE EXCEPTION 'Invalid voter, office, or candidate'; END IF;
  NEW.submitted_at := clock_timestamp();
  RETURN NEW;
END $$;
CREATE TRIGGER gea_vote_guard BEFORE INSERT OR UPDATE OR DELETE ON elections_vote
  FOR EACH ROW EXECUTE FUNCTION gea_vote_guard();

CREATE FUNCTION gea_configuration_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE active timestamptz;
BEGIN
  SELECT activated_at INTO active FROM elections_election WHERE id=1 FOR UPDATE;
  IF active IS NOT NULL THEN
    IF TG_TABLE_NAME = 'elections_voter' AND TG_OP = 'UPDATE' THEN
      IF (OLD.user_id, OLD.election_id, OLD.display_name) IS DISTINCT FROM
         (NEW.user_id, NEW.election_id, NEW.display_name) THEN
        RAISE EXCEPTION 'Activated voter roster is frozen';
      END IF;
    ELSE RAISE EXCEPTION 'Activated election configuration is frozen'; END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER gea_candidate_guard BEFORE INSERT OR UPDATE OR DELETE ON elections_candidate
  FOR EACH ROW EXECUTE FUNCTION gea_configuration_guard();
CREATE TRIGGER gea_office_guard BEFORE INSERT OR UPDATE OR DELETE ON elections_office
  FOR EACH ROW EXECUTE FUNCTION gea_configuration_guard();
CREATE TRIGGER gea_roster_guard BEFORE INSERT OR UPDATE OR DELETE ON elections_voter
  FOR EACH ROW EXECUTE FUNCTION gea_configuration_guard();

CREATE FUNCTION gea_election_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.activated_at IS NOT NULL THEN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Activated election cannot be deleted'; END IF;
    IF (OLD.name, OLD.opens_at, OLD.closes_at, OLD.activated_at) IS DISTINCT FROM
       (NEW.name, NEW.opens_at, NEW.closes_at, NEW.activated_at) THEN
      RAISE EXCEPTION 'Activated election configuration is frozen';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.result_snapshot IS NOT NULL AND
       (OLD.result_snapshot, OLD.finalized_at) IS DISTINCT FROM (NEW.result_snapshot, NEW.finalized_at) THEN
      RAISE EXCEPTION 'Final results are immutable';
    END IF;
    IF NEW.result_snapshot IS NOT NULL AND (NEW.activated_at IS NULL OR clock_timestamp() < NEW.closes_at) THEN
      RAISE EXCEPTION 'Results unavailable before closing';
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER gea_election_guard BEFORE UPDATE OR DELETE ON elections_election
  FOR EACH ROW EXECUTE FUNCTION gea_election_guard();

CREATE FUNCTION gea_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD.username, OLD.email) IS DISTINCT FROM (NEW.username, NEW.email) AND EXISTS (
    SELECT 1 FROM elections_voter v JOIN elections_election e ON e.id=v.election_id
    WHERE v.user_id=OLD.id AND e.activated_at IS NOT NULL
  ) THEN RAISE EXCEPTION 'Activated voter identity is frozen'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER gea_identity_guard BEFORE UPDATE ON elections_user
  FOR EACH ROW EXECUTE FUNCTION gea_identity_guard();

CREATE FUNCTION gea_audit_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Audit events are append-only'; END $$;
CREATE TRIGGER gea_audit_guard BEFORE UPDATE OR DELETE ON elections_auditevent
  FOR EACH ROW EXECUTE FUNCTION gea_audit_guard();
"""


def install(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL, params=None)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for table, trigger in [
            ("vote", "vote"), ("candidate", "candidate"), ("office", "office"),
            ("voter", "roster"), ("election", "election"), ("user", "identity"), ("auditevent", "audit"),
        ]:
            schema_editor.execute(f"DROP TRIGGER gea_{trigger}_guard ON elections_{table}")
        for name in ["vote", "configuration", "election", "identity", "audit"]:
            schema_editor.execute(f"DROP FUNCTION gea_{name}_guard()")


class Migration(migrations.Migration):
    dependencies = [("elections", "0001_initial")]
    operations = [migrations.RunPython(install, uninstall)]
