from django.db import migrations


def install(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        CREATE FUNCTION gea_empty_box_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM id FROM elections_election WHERE id=1 FOR UPDATE;
          IF TG_TABLE_NAME = 'elections_candidate' THEN
            IF NEW.is_blank AND (NEW.number <> 2 OR NEW.nim <> '' OR NOT EXISTS (
                SELECT 1 FROM elections_office WHERE id=NEW.office_id AND slug='senator'
            )) THEN RAISE EXCEPTION 'Empty box is only allowed as senator choice 2, without a NIM'; END IF;
          ELSE
            IF NEW.slug <> 'senator' AND EXISTS (
                SELECT 1 FROM elections_candidate WHERE office_id=NEW.id AND is_blank
            ) THEN RAISE EXCEPTION 'Empty box is only allowed for senator'; END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER gea_empty_box_candidate_guard BEFORE INSERT OR UPDATE ON elections_candidate
          FOR EACH ROW EXECUTE FUNCTION gea_empty_box_guard();
        CREATE TRIGGER gea_empty_box_office_guard BEFORE UPDATE ON elections_office
          FOR EACH ROW EXECUTE FUNCTION gea_empty_box_guard();
    """, params=None)


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("""
            DROP TRIGGER gea_empty_box_candidate_guard ON elections_candidate;
            DROP TRIGGER gea_empty_box_office_guard ON elections_office;
            DROP FUNCTION gea_empty_box_guard();
        """, params=None)


class Migration(migrations.Migration):
    dependencies = [("elections", "0011_candidate_details")]
    operations = [migrations.RunPython(install, uninstall)]
