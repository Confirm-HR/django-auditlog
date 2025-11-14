# Migration to add PostgreSQL trigram indices on User model fields
# This enables fast text search on actor fields (first_name, last_name, username)
# Works with any User model configured via AUTH_USER_MODEL setting

from django.conf import settings
from django.db import migrations


def create_user_trigram_indices(apps, schema_editor):
    """
    Dynamically create trigram indices on User model fields.
    Uses the configured AUTH_USER_MODEL to determine the correct table.
    """
    # Get the User model
    User = apps.get_model(settings.AUTH_USER_MODEL)
    db_table = User._meta.db_table

    # Get the username field name (could be 'username', 'email', etc.)
    username_field = User.USERNAME_FIELD

    # Create indices using CONCURRENTLY for zero-downtime production deployment
    # Note: We can't use CONCURRENTLY in a transaction, so atomic=False is required
    sql_commands = [
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS {db_table}_first_name_trgm_idx
        ON {db_table} USING gin (first_name gin_trgm_ops);
        """,
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS {db_table}_last_name_trgm_idx
        ON {db_table} USING gin (last_name gin_trgm_ops);
        """,
        f"""
        CREATE INDEX CONCURRENTLY IF NOT EXISTS {db_table}_{username_field}_trgm_idx
        ON {db_table} USING gin ({username_field} gin_trgm_ops);
        """,
    ]

    for sql in sql_commands:
        schema_editor.execute(sql)


def drop_user_trigram_indices(apps, schema_editor):
    """
    Drop trigram indices on User model fields.
    """
    User = apps.get_model(settings.AUTH_USER_MODEL)
    db_table = User._meta.db_table
    username_field = User.USERNAME_FIELD

    sql_commands = [
        f"DROP INDEX CONCURRENTLY IF EXISTS {db_table}_first_name_trgm_idx;",
        f"DROP INDEX CONCURRENTLY IF EXISTS {db_table}_last_name_trgm_idx;",
        f"DROP INDEX CONCURRENTLY IF EXISTS {db_table}_{username_field}_trgm_idx;",
    ]

    for sql in sql_commands:
        schema_editor.execute(sql)


class Migration(migrations.Migration):
    # CRITICAL: atomic = False is required for CREATE INDEX CONCURRENTLY
    # CONCURRENTLY cannot run inside a transaction block
    atomic = False

    dependencies = [
        ("auditlog", "0019_add_trigram_indices"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(
            create_user_trigram_indices,
            reverse_code=drop_user_trigram_indices,
        ),
    ]
