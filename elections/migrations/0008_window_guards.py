from django.db import migrations

VOTE_GUARD = """
CREATE OR REPLACE FUNCTION gea_vote_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e elections_election%ROWTYPE;
BEGIN
  IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'Votes are immutable'; END IF;
  SELECT * INTO e FROM elections_election WHERE id=NEW.election_id FOR UPDATE;
  IF e.activated_at IS NULL OR e.paused OR e.result_snapshot IS NOT NULL OR NOT EXISTS (
    SELECT 1 FROM elections_votingwindow w WHERE w.election_id = e.id
      AND clock_timestamp() >= w.starts_at AND clock_timestamp() < w.ends_at
  ) THEN
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
"""

# After activation the opening and name are frozen; closes_at may only grow while the election is open,
# which is how an operator extends or appends a voting session.
ELECTION_GUARD = """
CREATE OR REPLACE FUNCTION gea_election_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.activated_at IS NOT NULL THEN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Activated election cannot be deleted'; END IF;
    IF (OLD.name, OLD.opens_at, OLD.activated_at) IS DISTINCT FROM (NEW.name, NEW.opens_at, NEW.activated_at)
       OR (NEW.closes_at IS DISTINCT FROM OLD.closes_at
           AND NOT (NEW.closes_at > OLD.closes_at AND OLD.closes_at > clock_timestamp())) THEN
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
"""

WINDOW_GUARD = """
CREATE FUNCTION gea_window_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE e elections_election%ROWTYPE;
BEGIN
  SELECT * INTO e FROM elections_election
    WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.election_id ELSE NEW.election_id END FOR UPDATE;
  IF e.activated_at IS NOT NULL THEN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Activated voting sessions cannot be removed'; END IF;
    IF TG_OP = 'INSERT' AND NOT (NEW.starts_at > clock_timestamp() AND NEW.starts_at >= e.opens_at) THEN
      RAISE EXCEPTION 'New voting sessions must start in the future';
    END IF;
    IF TG_OP = 'UPDATE' AND NOT (NEW.election_id = OLD.election_id AND NEW.starts_at = OLD.starts_at
        AND NEW.ends_at > OLD.ends_at AND OLD.ends_at > clock_timestamp()) THEN
      RAISE EXCEPTION 'Activated voting sessions can only be extended before they end';
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER gea_window_guard BEFORE INSERT OR UPDATE OR DELETE ON elections_votingwindow
  FOR EACH ROW EXECUTE FUNCTION gea_window_guard();
"""

# Bodies from 0002_database_guards, restored on reverse.
OLD_VOTE_GUARD = VOTE_GUARD.replace("""OR NOT EXISTS (
    SELECT 1 FROM elections_votingwindow w WHERE w.election_id = e.id
      AND clock_timestamp() >= w.starts_at AND clock_timestamp() < w.ends_at
  ) THEN""", """
     OR clock_timestamp() < e.opens_at OR clock_timestamp() >= e.closes_at THEN""")
OLD_ELECTION_GUARD = ELECTION_GUARD.replace("""    IF (OLD.name, OLD.opens_at, OLD.activated_at) IS DISTINCT FROM (NEW.name, NEW.opens_at, NEW.activated_at)
       OR (NEW.closes_at IS DISTINCT FROM OLD.closes_at
           AND NOT (NEW.closes_at > OLD.closes_at AND OLD.closes_at > clock_timestamp())) THEN""",
"""    IF (OLD.name, OLD.opens_at, OLD.closes_at, OLD.activated_at) IS DISTINCT FROM
       (NEW.name, NEW.opens_at, NEW.closes_at, NEW.activated_at) THEN""")


def install(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        for sql in [VOTE_GUARD, ELECTION_GUARD, WINDOW_GUARD]:
            schema_editor.execute(sql, params=None)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP TRIGGER gea_window_guard ON elections_votingwindow")
        schema_editor.execute("DROP FUNCTION gea_window_guard()")
        for sql in [OLD_VOTE_GUARD, OLD_ELECTION_GUARD]:
            schema_editor.execute(sql, params=None)


class Migration(migrations.Migration):
    dependencies = [("elections", "0007_voting_windows")]
    operations = [migrations.RunPython(install, uninstall)]
